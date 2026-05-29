# Microscopy Images

## Overview

Microscopy provides high-resolution, whole-sample spatial context. Supported tissue imaging types include H&E-stained brightfield sections, multi-channel immunofluorescence, and general brightfield acquisitions.

The preprocessing pipeline normalises the pixel data to a uniform `float32 [0, 1]` range, optionally enhances colour and contrast, removes background, crops to the tissue region, and writes the result as a multi-resolution OME-TIFF pyramid for efficient downstream access at any scale.

---

## Supported Input Formats

| Extension | Description |
|-----------|-------------|
| `.ome.tiff` / `.ome.tif` | OME-TIFF (any bit depth, any number of channels, existing pyramids are ignored) |
| `.tiff` / `.tif` | Standard TIFF (8-bit, 16-bit, float; channels-first or channels-last) |
| `.czi` | Zeiss CZI; multi-scene files are supported — only the first scene is used |

FOCUS searches the sample directory by extension priority in the order shown. Only the first matching file is loaded.

!!! note "Channel handling"
    Images with more than three channels are silently clipped to the first three channels after loading. Single-channel images are expanded to `(H, W, 1)`.

---

## Directory Layout

Place one image file per sample inside `<sample_id>/<modality_name>/`:

```
dataset_root/
├── sample_A/
│   └── he_image/
│       └── section_A.tif
├── sample_B/
│   └── he_image/
│       └── section_B.czi
```

The modality name (e.g. `he_image`) must match the name declared in the FOCUS configuration file.

---

## Preprocessing Steps

1. **Load and normalise to float32 [0, 1]** — TIFF files are read with `tifffile`; CZI files with `czifile`. Integer arrays are divided by their dtype maximum; float arrays are used as-is if already in range. The array is reshaped to `(H, W, C)`.

2. **Colour enhancement** — gamma correction (`image^gamma`) brightens the image when `gamma < 1`. Contrast stretching saturates a configurable percentage of pixels at both ends of the intensity histogram to maximise dynamic range. Both operations are skipped when `color_enhancement=False`.

3. **Background removal** — the image is converted to grayscale and inverted (white background becomes black). A Gaussian blur is applied to suppress detail, then an Otsu threshold on the blurred image determines the foreground. Small objects below `min_object_size` pixels are removed, holes are filled, and contours smaller than `min_object_coverage` × image area are discarded. The detected background is replaced with the fill colour (`white` or `black`). Skipped when `remove_background=False`.

4. **Crop to tissue** — the tight bounding box of the non-background region is computed, expanded by `crop_margin` pixels on all sides (clamped to image boundaries), and the image is cropped. Skipped when `crop_to_tissue=False`.

5. **OME-TIFF pyramid construction** — the processed float32 image is downsampled by successive factors of 2 (area interpolation). The number of resolution levels is **computed automatically** from the image dimensions so that the smallest pyramid level fits within a 3,000 × 3,000 pixel cap (for efficient GUI rendering); it is not user-configurable. Each level is stored as an independent IFD in a BigTIFF container with zlib compression and a full OME-XML metadata block in the first IFD. RGB images are written as interleaved photometric RGB; multi/single-channel images as separate `minisblack` planes.

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `color_enhancement` | `bool` | `True` | Apply gamma correction and contrast stretching | `True`, `False` |
| `gamma` | `float` | `0.45` | Gamma exponent; values < 1 brighten the image | Any positive float |
| `contrast_saturation` | `float` | `0.35` | Percentage of pixels saturated at each histogram end | `0.0` – `100.0` |
| `remove_background` | `bool` | `True` | Detect and fill background pixels | `True`, `False` |
| `background_color` | `str` | `"white"` | Fill colour for background regions | `"white"`, `"black"` |
| `gaussian_blur_kernel_size` | `int` | `251` | Kernel size for Gaussian blur used in background detection (must be odd) | Positive odd integer |
| `clip_percentile` | `int` | `99` | Intensity percentile clipped before Otsu thresholding | `1` – `100` |
| `min_object_size` | `int` | `500` | Minimum connected component size (pixels) retained in the tissue mask | Positive integer |
| `min_object_coverage` | `float` | `0.01` | Minimum tissue contour area as a fraction of total image area | `0.0` – `1.0` |
| `crop_to_tissue` | `bool` | `True` | Crop to the bounding box of the tissue region | `True`, `False` |
| `crop_margin` | `int` | `250` | Pixel margin added around the tissue bounding box | Non-negative integer |
| `force_recomputing` | `bool` | `False` | Reprocess even if a cached output file already exists | `True`, `False` |

!!! note "Pyramid levels are not configurable"
    The number of OME-TIFF resolution levels is computed automatically from the image size (so the smallest level stays within 3,000 × 3,000 px). There is no `pyramid_levels` parameter.

---

## Registration

!!! warning "Only `feature_extraction` is compatible"
    Microscopy images use **patch-based feature extraction** for registration.  
    `spot_interpolation` is **not** compatible with image modalities.

The `feature_extraction` registration type extracts patch embeddings centred on each anchor spot's location in image space using [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath), a pathology foundation model. GPU acceleration is used when available; the model is downloaded from HuggingFace on first use and requires a valid HuggingFace token.

**Registration settings:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `patch_size` | `int` | `224` | Side length (pixels) of each patch extracted around an anchor spot |
| `background_color` | `str` | — | Background fill colour passed to the feature extractor (`"white"` or `"black"`) |

```yaml
registration_type: feature_extraction
registration_settings:
  patch_size: 224
  background_color: white
```

---

## Output

The preprocessing step produces a single multi-resolution OME-TIFF per sample at:

```
<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Data type | `float32` |
| Value range | `[0, 1]` |
| Compression | zlib |
| Container | BigTIFF |
| Pyramid levels | Computed automatically (smallest level ≤ 3,000 × 3,000 px) |
| Downsampling factor | 0.5× per level (area interpolation) |
| Metadata | OME-XML in first IFD; physical pixel size stored when available |
| RGB images | Interleaved `photometric=rgb` |
| Single/multi-channel | Separate `minisblack` planes per channel |

Registration produces an AnnData (`.h5ad`) with:

- `.X` — patch embedding matrix `(N_spots, embedding_dim)`, `float32`
- `.obsm['spatial']` — spot centre coordinates in image space, `float32`
- `.obs['sample_id']` — sample identifier

---

## Config Example

```yaml
modalities:
  - name: he_image
    type: microscopy_image
    processing_settings:
      color_enhancement: true
      gamma: 0.45
      contrast_saturation: 0.35
      remove_background: true
      background_color: white
      gaussian_blur_kernel_size: 251
      clip_percentile: 99
      min_object_size: 500
      min_object_coverage: 0.01
      crop_to_tissue: true
      crop_margin: 250
    registration_type: feature_extraction
    registration_settings:
      patch_size: 224
      background_color: white
```
