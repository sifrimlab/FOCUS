# Microscopy Images

## Overview

Microscopy provides high-resolution, whole-sample spatial context. FOCUS reads one acquisition per sample (brightfield H&E, IHC or special stains; widefield, confocal or multiplexed fluorescence) and writes one lossless, tiled OME-TIFF pyramid per sample. The stored level-0 pixels are the source pixels: same sample type, every channel, no contrast change. Channel names, colours, wavelengths, objective and pixel size are carried into the OME-XML. Contrast is stored as display metadata and applied only when the image is shown.

Two settings describe the acquisition: `image_type` (brightfield or fluorescence, read from the metadata by default) and `z_projection` (how a Z-stack becomes one plane). They select the tissue detection, the background fill, the display rendering and the codec, so no numeric threshold is configured. Cropping to the tissue and removing the background are opt-in.

The algorithms and their sources are described in [Microscopy Preprocessing Methods](../scientific/microscopy_methods.md) (method version `microscopy-3`).

!!! note "Per-sample failures do not stop the run"
    `MicroscopyImageDataset.process_dataset()` processes one sample at a time. When a sample raises, the warning `Sample skipped: <error>` is reported, that sample is left out of the returned `{sample_id: path}` mapping, and processing continues with the next sample. A sample folder with no supported image (`FileNotFoundError`) or with several (`ValueError`) is detected while the sample objects are constructed, before processing starts, and aborts the modality.

---

## Supported input formats

| Format | Extensions | Reader |
|--------|------------|--------|
| OME-TIFF, BigTIFF | `.ome.tiff`, `.ome.tif`, `.ome.btf`, `.tiff`, `.tif`, `.btf` | tifffile |
| Akoya qpTIFF | `.qptiff` | tifffile |
| Aperio SVS, Hamamatsu NDPI, Leica SCN, Ventana BIF | `.svs`, `.ndpi`, `.scn`, `.bif` | tifffile |
| Zeiss CZI | `.czi` | pylibCZIrw |
| Nikon ND2 | `.nd2` | nd2 |

Extension matching is case-insensitive and hidden files are ignored. Formats outside this list (for example MIRAX `.mrxs` or Olympus `.vsi`) are converted to OME-TIFF first, with `bfconvert` from Bio-Formats or the QuPath OME-TIFF export.

Every reader exposes the acquisition lazily, so pixels are read tile by tile and memory does not scale with the slide. Auxiliary images (label, macro, thumbnail, overview, map, preview) are never chosen; among the other series or scenes the one with the most pixels is the acquisition, and other acquisition-sized series are reported. Axes come from the reader metadata; a time series keeps `T = 0` with a warning, and a Z-stack is reduced by `z_projection`. See [Input contract](../scientific/microscopy_methods.md#2-input-contract) and [Time and depth](../scientific/microscopy_methods.md#4-time-and-depth).

!!! note "Pixel size"
    The pixel size is read from the file metadata (OME, Aperio `MPP`, ImageJ, TIFF resolution tags, CZI `Scaling`, calibrated ND2 voxel size). Values outside 0.01 to 100 µm and inch-based screen or print defaults (72, 96, 300 dpi and similar) are rejected. When the file has no valid value, the `pixel_size` setting is used. Without either, the run continues with a warning: tissue areas and the crop margin fall back to pixel values, and `feature_extraction` cannot choose its encoding level. See [Pixel size](../scientific/microscopy_methods.md#24-pixel-size).

---

## Directory layout

Place exactly one supported image file per sample inside `<sample_id>/<modality_name>/`. A folder with several supported images raises `ValueError`, so a sample never uses one of two acquisitions silently.

```
dataset_root/
├── sample_A/
│   └── he_image/
│       └── section_A.svs
├── sample_B/
│   └── he_image/
│       └── section_B.czi
├── sample_C/
│   └── he_image/
│       └── section_C.ome.tiff
```

The modality name (e.g. `he_image`) must match the name declared in the FOCUS configuration file.

---

## Preprocessing steps

Each sample runs the steps below, reported on the console and in the GUI. A cached output is reported as such and no step runs.

| Step | What it does |
|------|--------------|
| `1/5` Reading image | Opens the source lazily and reports its format, size, channels, sample type and any reader notes (series chosen, axes interpreted, alpha dropped) |
| `2/5` Resolving image type | Resolves the pixel size, `image_type` (with its evidence) and the Z plane |
| `3/5` Detecting tissue | Detects the tissue on a canvas of about 8 µm per pixel and reports its coverage; derives the display windows from the tissue pixels |
| `4/5` Cropping to tissue or Removing background | Runs only when `crop_to_tissue` or `remove_background` is enabled; the line reads `Cropping to tissue` when cropping is on, otherwise `Removing background` |
| `5/5` Writing OME-TIFF | Stages the pyramid, digests the level-0 pixels and writes the file atomically |

Tissue is always detected, because its outline is stored as an OME ROI and its pixels set the display windows. Detection never changes pixels. The detection rules (HSV saturation and Otsu for brightfield, per-channel scaling and the triangle method for fluorescence, a 0.05 mm² minimum piece capped at a tenth of the largest piece) are described in [Tissue](../scientific/microscopy_methods.md#5-tissue).

### Image types

`image_type: auto` is resolved from the configuration, then the acquisition metadata (acquisition mode, contrast method, illumination, emission wavelength, fluorophore, whole-slide brightfield scanner format), then the channel layout (two or more non-RGB channels are fluorescence), then the image polarity (a dark border is fluorescence). The last rule raises a warning that asks for an explicit `image_type`. The evidence is recorded in the output. See [Image type](../scientific/microscopy_methods.md#3-image-type).

| | `brightfield` | `fluorescence` |
|---|---|---|
| `z_projection: auto` | Best-focus plane | Maximum-intensity projection |
| Tissue detection | HSV saturation, median blur, Otsu | Per-channel scaling, channel maximum, triangle |
| Background fill (`remove_background`) | Maximum of the sample type (white) | 0 |
| Display rendering | Identity (RGB) or windowed gray | Per-channel window and colour |
| Lossless codec | JPEG 2000 reversible (8-bit), zstd otherwise | zstd |
| `feature_extraction` registration | Allowed | Rejected |

### Crop and fill

`crop_to_tissue` crops to the bounding box of the tissue mask plus a fixed margin of 100 µm (250 px without a pixel size), clamped to the image. Pixels inside the box are unchanged, and the box origin is recorded as the crop offset. `remove_background` sets every pixel outside the tissue mask to the fill of the image type, so a fluorescence image is never filled with white. Both are off by default, so the output keeps the whole acquisition; the fill in particular changes the values that feature extraction and any quantitative reading use. See [Crop and fill](../scientific/microscopy_methods.md#53-crop-and-fill-opt-in).

### Display

Contrast is stored, not applied. Each channel carries a name, a colour and a window in native values: identity windows for RGB brightfield, otherwise 0.35% of the tissue pixels saturated at each end. Colours come from the metadata, else from the emission wavelength or a nuclear stain name, else the ImageJ composite order. The alignment GUI renders multichannel images as a coloured composite of these windows, with one toggle per channel; the first four channels are visible by default. See [Display rendering](../scientific/microscopy_methods.md#6-display-rendering).

---

## Processing parameters

Set under `processing_settings` for the modality. Absent values fall back to the defaults below.

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `image_type` | `str` | `"auto"` | `"auto"` (from the metadata), `"brightfield"` (H&E, IHC, special stains) or `"fluorescence"` (IF, confocal, multiplexed) |
| `z_projection` | `str` | `"auto"` | `"auto"` (maximum projection for fluorescence, best focus for brightfield), `"max"`, `"best_focus"` or `"middle"` |
| `crop_to_tissue` | `bool` | `false` | Crop to the tissue bounding box plus 100 µm |
| `remove_background` | `bool` | `false` | Set every pixel outside the tissue to the fill of the image type |
| `pixel_size` | `float` or `null` | `null` | µm per pixel, used only when the file metadata has none; a metadata value that differs by more than 5% is reported |
| `force_recomputing` | `bool` | `false` | Reprocess even when the output carries the current fingerprint |

Configuration validation rejects an `image_type` or `z_projection` outside these values, a `pixel_size` that is not a positive number, and `image_type: "fluorescence"` combined with `registration_type: "feature_extraction"`.

!!! warning "Removed settings"
    `color_enhancement`, `gamma`, `contrast_saturation` (contrast is display-only and automatic), `background_color` (the fill follows `image_type`), `min_object_coverage`, `clip_percentile` (tissue detection is calibrated in µm) and `crop_margin` (a fixed 100 µm) are no longer microscopy processing settings. A configuration that sets one raises `ValueError` naming the reason. The `background_color` key of `registration_settings` is a different setting and remains valid (see [Registration](#registration)).

---

## Caching

Microscopy preprocessing has no intermediate caches. The output is reused when it carries the current fingerprint and `force_recomputing` is `false`. The fingerprint covers the method version, the SHA-256 of the whole source file, the sample and modality names, the settings, the calibration constants and the versions of the imaging libraries and the format reader. The source SHA-256 stored in the output is reused while the source path, size and modification time are unchanged, so an unchanged source is not re-hashed. Outputs are written to a temporary file and renamed into place.

A regenerated image whose level-0 pixels are identical to the file it replaces keeps the alignments made on that file valid. When the pixels differ, a warning reports that alignments made on the previous image must be redone (see [Alignment](../pipeline/alignment.md)). See [Cache key and regeneration](../scientific/microscopy_methods.md#8-cache-key-and-regeneration).

!!! note "Upgrading from `microscopy-2`"
    The method version is part of the fingerprint, so every microscopy output is rebuilt once. Outputs of `microscopy-2` stored contrast-enhanced pixels by default, so manual alignments made on them must be redone once.

---

## Output

One OME-TIFF per sample:

```
<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Pixels | Level 0 holds the source pixels after the optional crop and fill: the source sample type (`uint8`, `uint16`, `int16`, `uint32`, `float32` and the other OME pixel types) and every channel |
| Layout | One OME-TIFF series, 512 x 512 tiles; one page per channel, or one interleaved page for RGB |
| Pyramid | Lower levels stored as SubIFDs, each halving both sides with a 2 x 2 area average, until the image fits in one tile |
| Compression | Lossless: JPEG 2000 reversible for 8-bit brightfield, zstd otherwise (horizontal predictor for integer images); readable by tifffile and Bio-Formats 8 (QuPath 0.7) |
| Channel metadata | Name, colour, excitation and emission wavelengths, fluorophore, illumination type, acquisition mode and contrast method, when the source records them |
| Pixel size | `PhysicalSizeX/Y` in µm when known, plus TIFF resolution tags on every page |
| Tissue outline | OME ROI `tissue`, one polygon per tissue piece in level-0 pixels |
| Provenance | `MapAnnotation` (namespace `focus`): fingerprint, input signature, pixel digest, crop offset, method, pixel-size source, image type and evidence, Z projection, source series, background fill, display windows (`focus_display`) and, after a pixel-identical regeneration, the superseded signature. See [OME-XML](../scientific/microscopy_methods.md#73-ome-xml) and [Data Schemas](../api/data_types.md#1-preprocessing-outputs) |

A source compressed with lossy JPEG (for example an SVS) is decoded once and stored lossless, so the output is larger than the source. No merged file is produced for microscopy; the returned mapping contains one entry per successfully processed sample.

---

## Registration

`microscopy_image` is compatible with three registration types: `feature_extraction` (brightfield), `pixel_composition` (fluorescence), and `none` (which aligns the modality but leaves it out of the final MuData). The spot-based types (`spot_interpolation`) and `raman_pixel_interpolation` are rejected during configuration validation.

`pixel_composition` clusters the pixels of a multichannel fluorescence image into phenotypes with Pixie (Gaussian smoothing, per-pixel and per-channel normalization, self-organizing map, consensus metaclustering) and gives each anchor spot the fraction of its footprint occupied by each phenotype. See [Registration](../pipeline/registration.md#pixel_composition).

`feature_extraction` gives each anchor spot one embedding of the image area inside its footprint (the mean of the encoder's patch tokens it overlaps) and, optionally, the embedding of the image patch around it, computed with [Prov-GigaPath](https://huggingface.co/prov-gigapath/prov-gigapath), a pathology foundation model downloaded from HuggingFace on first use, so `huggingface_token` must be set in the configuration. The image is encoded at the stored pyramid level closest to the model's 0.5 µm per pixel, using the pixel size recorded at preprocessing. It runs on a GPU when one is available and otherwise on the CPU.

!!! warning "Use `feature_extraction` only on H&E-stained brightfield images"
    Prov-GigaPath is pretrained on brightfield tiles from H&E-stained whole-slide images, so its embeddings represent H&E morphology. `feature_extraction` applies to brightfield images only. An image recorded as `fluorescence` is rejected: at configuration time when `image_type` is `"fluorescence"`, and at registration time from the `focus_image_type` recorded in the preprocessed image (for example when `image_type: auto` resolved to fluorescence).

    The stain of a brightfield image is not checked. An IHC or other-stain brightfield image is encoded the same way, and a complete embedding matrix is returned with no error; the values do not describe the tissue. A single-channel image is replicated to RGB first, so it runs to completion rather than failing.

    For any such brightfield modality use `registration_type: none`; for fluorescence use `pixel_composition`. Preprocessing and alignment still run and the aligned OME-TIFF remains available; only the embedding step is skipped.

**Registration settings:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `background_color` | `str` | `"white"` | Colour used to recognise empty image areas when the image was not filled. An encoder token whose pixels match it in at least 99% of positions is never read; an anchor spot whose footprint holds only such tokens receives an all-zero embedding, keeping one output row per anchor spot. When preprocessing filled the background (`remove_background: true`), the fill recorded in the image (`focus_background_fill`) is used instead: 0 counts as black, any other value as white |
| `patch_embeddings` | `bool` | `false` | Also store, for every anchor spot, the embedding of the 224 px image patch around it (`obsm['patch_embedding']`), the patch centre in the reference frame (`obsm['patch_spatial']`) and the patch index (`obs['patch_id']`). The patch embedding is coarser than the spot embedding but much less noisy for small spots |
| `force_recomputing` | `bool` | `false` | Recompute even if the registration output matches its inputs |

```yaml
registration_type: feature_extraction
registration_settings:
  background_color: white
```

See [Registration](../pipeline/registration.md) for the algorithm.

---

## Config example

=== "H&E brightfield (feature extraction)"

    ```yaml
    modalities:
      - name: he_image
        type: microscopy_image
        processing_settings:
          image_type: brightfield
          z_projection: auto
          crop_to_tissue: false
          remove_background: false
          pixel_size: null
          force_recomputing: false
        registration_type: feature_extraction
        registration_settings:
          background_color: white
    ```

=== "Multiplexed fluorescence"

    ```yaml
    modalities:
      - name: if_image
        type: microscopy_image
        processing_settings:
          image_type: fluorescence
          z_projection: max
          crop_to_tissue: true
        registration_type: none
    ```
