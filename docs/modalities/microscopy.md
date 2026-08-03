# Microscopy Images

## Overview

Microscopy provides high-resolution, whole-sample spatial context. FOCUS reads one image file per sample (H&E brightfield, immunofluorescence, or any other TIFF/qpTIFF/CZI acquisition) and writes a multi-resolution OME-TIFF pyramid per sample.

`MicroscopyImageDataset.process_dataset()` handles one sample at a time and runs five labeled steps per sample, reported on the console and in the GUI as `1/5` … `5/5`:

| Step | What it does |
|------|--------------|
| `1/5` | Loads the image and normalises it to `float32` in `[0, 1]`, shape `(H, W, C)` with at most 3 channels |
| `2/5` | Colour enhancement: gamma correction followed by percentile contrast stretching |
| `3/5` | Tissue detection on a downsampled proxy, then background fill |
| `4/5` | Crop to the tissue bounding box plus a margin |
| `5/5` | Writes the multi-resolution OME-TIFF |

Steps `2/5`, `3/5` and `4/5` are individually switchable from the configuration (`color_enhancement`, `remove_background`, `crop_to_tissue`); when one is off, its step line reports `not required` and the image passes through unchanged. Steps `1/5` and `5/5` always run.

!!! note "One tissue-detection pass serves both background removal and cropping"
    The mask is computed once, as soon as **either** `remove_background` or `crop_to_tissue` is enabled, and both steps consume it. Enabling only `crop_to_tissue` still runs the full Otsu detection; disabling both skips it entirely.

!!! note "Per-sample failures do not stop the run"
    `process_dataset()` wraps each sample in a `try`/`except`. When a sample raises, the line `Error processing sample <sample_id>: <error>` is printed to the console, that sample is left out of the returned `{sample_id: path}` mapping, and processing continues with the next sample. The one exception is a **missing input file**: that `FileNotFoundError` is raised while the sample objects are being constructed, before processing starts, and it aborts the modality.

---

## Supported input formats

| Extension | Description |
|-----------|-------------|
| `.ome.tiff` / `.ome.tif` | OME-TIFF, read with `tifffile`: the file's first series at its base level; any pyramid it already carries is not reused |
| `.qptiff` | Akoya/PerkinElmer Vectra/PhenoImager qpTIFF. Every series and pyramid level is compared by pixel count and only the largest is loaded, so thumbnail, macro and label images are discarded regardless of their order in the file |
| `.tiff` / `.tif` | Standard TIFF, read the same way as OME-TIFF |
| `.czi` | Zeiss CZI, read with `czifile`. Leading axes are reduced by taking index `0` until three axes remain; when the outermost axis has more than one entry, `WARNING: CZI file has multiple scenes. Using only the first one.` is printed |

FOCUS looks for the extensions in the order listed and loads the first file that matches the highest-priority extension present, so an `.ome.tiff` wins over a `.czi` in the same directory. Extension matching is case-insensitive. If no supported file is found, the sample constructor raises `FileNotFoundError`.

!!! note "Channel handling"
    A 2D image becomes `(H, W, 1)`. When the last axis is larger than the smallest of the first two, the smallest axis is moved to the end (channels-first input). Images with more than three channels are cut to the **first three** channels.

    Tissue detection needs 3 channels for its grayscale conversion, so 1- and 2-channel images are promoted for that step only: a single channel is replicated to RGB, two channels gain a zero third channel. This is the same promotion the alignment GUI applies when it builds its display image, so the mask is derived from the composite you see when aligning. The stored pixel data keeps its original channel count.

!!! tip "Dark-background acquisitions: set `background_color: black`"
    Detection handles both polarities (see step `3/5`), but the fill colour does not follow it. On a fluorescence image the contrast stretch pushes tissue close to the maximum, so filling the background with the default white leaves tissue and background indistinguishable. Set `background_color: "black"` for such images.

---

## Directory layout

Place one image file per sample inside `<sample_id>/<modality_name>/`:

```
dataset_root/
├── sample_A/
│   └── he_image/
│       └── section_A.tif
├── sample_B/
│   └── he_image/
│       └── section_B.czi
├── sample_C/
│   └── he_image/
│       └── section_C.qptiff
```

The modality name (e.g. `he_image`) must match the name declared in the FOCUS configuration file.

---

## Preprocessing steps

### 1/5: Load and normalise

The file is read according to its extension, then normalised:

- a 2D array gains a trailing channel axis;
- if the last axis is larger than the smallest of the first two, the smallest axis is transposed to the end;
- pixel values become `float32` in `[0, 1]`: a `float32` array whose maximum is already ≤ 1 is left untouched, an integer array is divided by its **dtype** maximum (`np.iinfo(dtype).max`, so a 12-bit image stored as `uint16` is divided by 65535), and any other float array is divided by its **own** maximum;
- channels beyond the third are dropped.

The source file's dtype is remembered here and decides the output storage dtype in step `5/5`.

### 2/5: Colour enhancement

Runs when `color_enhancement=true`, in this order:

1. **Gamma correction**: `I ← I^gamma`, elementwise, in place. With the default `gamma=0.45` (below 1) midtones are brightened.
2. **Contrast stretching**: the pixels greater than 0 across the **whole image, all channels pooled**, give one pair of percentiles: `contrast_saturation` and `100 − contrast_saturation` (by default the 0.35th and 99.65th). The image is clipped to that interval and linearly rescaled to `[0, 1]`. The same pair is applied to every channel, and the rescale is skipped when the two percentiles are equal.

When more than 9,000,000 pixels are non-zero, the two percentiles are estimated from a strided subsample of those values. The clip and rescale are still applied to every pixel.

### 3/5: Tissue detection and background removal

Detection runs on a proxy of the image downscaled (area interpolation) so it holds at most 9,000,000 pixels; an image already at or below that size is used as is. The scale factor is kept so the mask can be mapped back.

On that proxy:

1. Convert to `uint8` and, when the image has fewer than 3 channels, promote it to 3 (replicated single channel, or a zero-padded third channel).
2. Determine the polarity, then reduce to a grayscale in which **tissue is the bright class**:
    - **Bright background** (always taken for 3-channel input): every pixel that is 0 in all channels is set to white, then the image is converted to grayscale and inverted.
    - **Dark background**: the image is converted to grayscale and used as is, with neither the black-pixel step nor the inversion.

    The polarity is only probed for the promoted 1- and 2-channel images: the median of a border frame (the outer 5% of the shorter side) is compared with the median of the whole grayscale, and the background counts as dark when the border is strictly darker. A scan filled edge to edge with tissue gives equal medians and takes the bright-background path.
3. Clip the grayscale at its `clip_percentile` percentile and apply a 25 × 25 Gaussian blur.
4. Compute Otsu's threshold on the blurred image and apply it to the **unblurred, unclipped** grayscale. The blur therefore shapes only the histogram the threshold value is derived from; the mask itself is cut at full detail.
5. Remove connected components of 50 pixels or fewer, then fill holes.
6. Keep the external contours whose area is at least `min_object_coverage × (proxy height × proxy width)` and fill them; this is the tissue mask. If no contour is found at all, `Warning: No contours found; cannot refine background mask.` is printed and the unrefined mask from sub-step 5 is kept.

When `remove_background=true`, the mask is upsampled back to full resolution with nearest-neighbour interpolation and applied: tissue pixels are kept, every other pixel is set to `background_color` (`white` → 1, `black` → 0, in every channel; any other value raises `ValueError`).

The blur kernel (25 px) and the speck-removal size (50 px) are fixed constants expressed in proxy pixels, not configuration fields.

### 4/5: Crop to tissue

Runs when `crop_to_tissue=true`, using the same mask as step `3/5`, including when `remove_background=false`. The mask's bounding box is scaled back to full-resolution coordinates, expanded by `crop_margin` pixels on each side, and clamped to the image bounds. An empty mask raises `ValueError: The image appears to be entirely background; cannot crop.`

Because the bounding box is read at proxy resolution, its edges carry up to one proxy pixel (`1/scale` full-resolution pixels) of tolerance on top of `crop_margin`.

### 5/5: Write the OME-TIFF pyramid

The number of levels is computed from the final (post-crop) image size so the smallest level holds at most 9,000,000 pixels; it is not configurable. Level `ℓ` is the image scaled by `0.5^ℓ` with area interpolation.

Each level is quantised from `float32` to the storage dtype, then written into a BigTIFF with zlib compression and a predictor matched to that dtype (3 for `float32`, 2 for integers). `uint8` and `uint16` sources pass through, float sources stay `float32`, and any other integer depth (`int16`, `uint32`, …) is stored as `uint16`. Levels are separate top-level IFDs, not SubIFDs, and the OME-XML block describing all of them sits in the description of the first IFD.

A 3-channel image is written interleaved (`photometric='rgb'`, axes `YXC`, one OME channel named `RGB`); any other channel count is written as one `minisblack` plane per channel (axes `YX`, OME channels named `Channel_0` …).

---

## Processing parameters

Set under `processing_settings` for the modality. Absent values fall back to the defaults below.

| Name | Type | Default | Used by | Description |
|------|------|---------|---------|-------------|
| `color_enhancement` | `bool` | `true` | `2/5` | Run gamma correction and contrast stretching |
| `gamma` | `float` | `0.45` | `2/5` | Exponent of `I^gamma`; below 1 brightens, above 1 darkens |
| `contrast_saturation` | `float` | `0.35` | `2/5` | Percentage of non-zero pixels saturated at **each** end of the histogram |
| `remove_background` | `bool` | `true` | `3/5` | Fill non-tissue pixels with `background_color` |
| `background_color` | `str` | `"white"` | `3/5` | `"white"` or `"black"`; any other value raises `ValueError` when background removal runs. Not tied to the detected polarity; pick `"black"` for dark-background acquisitions |
| `clip_percentile` | `int` | `99` | `3/5` | Percentile at which the inverted grayscale is clipped before the blur and Otsu threshold |
| `min_object_coverage` | `float` | `0.01` | `3/5` | Minimum area of a tissue contour, as a fraction of the detection-proxy area |
| `crop_to_tissue` | `bool` | `true` | `4/5` | Crop to the tissue bounding box |
| `crop_margin` | `int` | `250` | `4/5` | Full-resolution pixels added on each side of the bounding box |
| `force_recomputing` | `bool` | `false` | all | Reprocess even when the output file already exists |

Apart from `background_color`, none of these values are checked. They are passed straight to the processing code, so an out-of-range value either raises there (a `clip_percentile` above 100 fails inside `numpy.percentile`) or changes the result, instead of being reported during configuration validation.

!!! note "Not parameters"
    The number of pyramid levels, the 9,000,000-pixel detection-proxy cap, the 25 px Gaussian blur kernel and the 50 px speck-removal size are internal constants. There is no `pyramid_levels`, `gaussian_blur_kernel_size` or `min_object_size` field for this modality.

---

## Caching

Microscopy preprocessing has no intermediate caches. Before doing any work, `process_image()` checks whether the output OME-TIFF exists: if it does and `force_recomputing` is `false`, it prints `Processed image already exists. Using cached results.` and returns that path.

!!! warning "The cache key is the output file, not the settings"
    Changing `gamma`, `crop_margin` or any other setting does not invalidate an existing output. Set `force_recomputing: true` (or delete the file) after changing a setting.

---

## Output

One OME-TIFF per sample:

```
<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Data type | `uint8`/`uint16` sources pass through, float sources stay `float32`, other integer depths become `uint16` |
| Value range | Integer output is `round(I × dtype_max)` clipped to the dtype's range (`[0, 255]`, `[0, 65535]`); `float32` output is written unchanged, on the normalised scale used internally |
| Compression | zlib, predictor 2 (integer) or 3 (`float32`) |
| Container | BigTIFF |
| Pyramid | One top-level IFD group per level, level `ℓ` scaled by `0.5^ℓ`; enough levels for the smallest to hold at most 9,000,000 pixels |
| Metadata | OME-XML for all levels, in the first IFD's description; each level is an `Image` named `ResolutionLevel_<i>` |
| RGB images | Interleaved `photometric='rgb'`, one channel named `RGB` with 3 samples per pixel |
| Single/multi-channel | One `minisblack` plane per channel, named `Channel_<c>` |

No merged file is produced for microscopy; the returned mapping contains one entry per successfully processed sample.

---

## Registration

`microscopy_image` is compatible with two registration types: `feature_extraction`, and `none` (which aligns the modality but leaves it out of the final MuData). The spot-based types (`spot_interpolation`, `spot_aggregation`) and `raman_pixel_interpolation` are rejected during configuration validation.

`feature_extraction` extracts a square patch around each anchor spot's position in image space and encodes it with [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath), a pathology foundation model downloaded from HuggingFace on first use, so `huggingface_token` must be set in the configuration. It runs on a GPU when one is available.

!!! warning "Use `feature_extraction` only on H&E-stained brightfield images"
    Prov-GigaPath is pretrained on brightfield tiles from H&E-stained whole-slide images, so its embeddings represent H&E morphology. Run it only when this modality is an **H&E-stained histological section imaged in brightfield RGB**.

    Nothing in FOCUS checks the stain or the imaging mode. An immunofluorescence, IHC or other-stain image is patched and encoded the same way, and a complete embedding matrix is returned with no error. The values do not describe the tissue. A single-channel image is replicated to RGB first, so it runs to completion rather than failing.

    For any such modality use `registration_type: none`. Preprocessing and alignment still run and the aligned OME-TIFF remains available; only the embedding step is skipped.

**Registration settings:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `patch_size` | `int` | `224` | Side length in pixels of the patch extracted around an anchor spot |
| `background_color` | `str` | `"white"` | Colour used to recognise empty patches. A patch made only of it is not passed through the model and receives an all-zero embedding, keeping one output row per anchor spot |
| `force_recomputing` | `bool` | `false` | Recompute even if a registration output exists |

```yaml
registration_type: feature_extraction
registration_settings:
  patch_size: 224
  background_color: white
```

See [Registration](../pipeline/registration.md) for the algorithm.

---

## Config example

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
      clip_percentile: 99
      min_object_coverage: 0.01
      crop_to_tissue: true
      crop_margin: 250
      force_recomputing: false
    registration_type: feature_extraction
    registration_settings:
      patch_size: 224
      background_color: white
```
