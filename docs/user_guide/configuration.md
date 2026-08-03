# Configuration

FOCUS is driven by a single JSON configuration file. This page explains every field, shows a complete working example, and documents the validation rules that FOCUS enforces before the pipeline starts.

---

## What Is the Config File?

The config file is a JSON document that tells FOCUS:

- Where your data lives (`dataset_path`)
- What modalities are present and how to preprocess each one
- Which modality is the spatial reference
- Whether to run alignment and/or registration
- Which registration strategy to use per modality

**Two ways to create it:**

1. **GUI (recommended)**: The GUI config builder guides you through every field and auto-saves the result to `<dataset_path>/focus_config.json`. You can reload this file in subsequent runs.
2. **Manually**: Write the JSON file yourself and pass it with `focus --config path/to/config.json`. This is convenient for scripting, HPC batch jobs, or when the GUI is not available.

---

## Annotated Full Example

The example below covers all three supported modality types and uses every available top-level and per-modality field. Inline comments explain each field. They are not valid in real JSON, so remove them before use.

```json
{
  "dataset_path": "/data/my_tissue_cohort",
  "reference_modality": "st",
  "perform_alignment": true,
  "perform_registration": true,
  "huggingface_token": "hf_...",
  "spatial_annotations": {
    "file_type": "geojson",
    "modality_name": "microscopy"
  },
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "st",
      "processing_settings": {
        "min_count_per_spot": 200,
        "max_count_per_spot": null,
        "min_genes_per_spot": 50,
        "max_genes_per_spot": null,
        "min_spots_per_gene": null,
        "min_count_spots_ratio_per_gene": null,
        "total_counts_normalize": true,
        "log1p_transform": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "st"
    },
    {
      "alignment_strategy": "manual",
      "name": "msi",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "min_intensity_threshold": 10000,
        "detect_background": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "spot_interpolation",
      "type": "msi"
    },
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": true,
        "crop_to_tissue": true,
        "gamma": 0.45,
        "force_recomputing": false
      },
      "registration_settings": {
        "patch_size": 224,
        "force_recomputing": false
      },
      "registration_type": "feature_extraction",
      "type": "microscopy_image"
    }
  ]
}
```

---

## Top-Level Fields

### `dataset_path` _(required, string)_

Absolute path to the root directory that contains your sample subdirectories. FOCUS reads raw input from here and writes all output back into this tree.

```json
"dataset_path": "/data/my_tissue_cohort"
```

### `reference_modality` _(required, string)_

The `name` of the modality that defines the master spatial coordinate system. All other modalities are aligned and registered onto the coordinate space of this modality. The value must exactly match one modality's `name` field.

```json
"reference_modality": "st"
```

!!! note "Reference modality alignment"
    The reference modality does not undergo alignment; it is the target. Its `alignment_strategy` field is ignored. Set `registration_type` to `"none"` for the reference modality unless you also want to register it onto itself, which is not meaningful.

### `perform_alignment` _(optional, boolean, default: `true`)_

Set to `false` to skip the interactive alignment stage entirely. Useful when:

- All modalities are already co-registered (set `alignment_strategy: "pre_aligned"` on each instead for finer control)
- You only want to run preprocessing

```json
"perform_alignment": false
```

### `perform_registration` _(optional, boolean, default: `true`)_

Set to `false` to stop the pipeline after alignment without running registration or compilation. Requires `perform_alignment: true`.

```json
"perform_registration": false
```

### `huggingface_token` _(optional, string, default: `null`)_

A valid HuggingFace access token. Required only when at least one modality uses `"registration_type": "feature_extraction"` (the Prov-GigaPath model). The token is used to download the model weights from HuggingFace Hub on first use; subsequent runs use the locally cached copy.

```json
"huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### `spatial_annotations` _(optional, object or `null`, default: `null`)_

Declares that GeoJSON annotation files are present and specifies which modality's coordinate space they are defined in. When set, FOCUS transfers polygon labels from the annotation file onto spots of the registered modalities.

```json
"spatial_annotations": {
  "modality_name": "microscopy",
  "file_type": "geojson"
}
```

| Field | Description |
|-------|-------------|
| `modality_name` | The `name` of the modality whose directory contains the `.geojson` files. |
| `file_type` | Currently only `"geojson"` is supported. |

---

## Per-Modality Fields

Each entry in the `modalities` array describes one modality. All modalities share the same structure.

### `name` _(required, string)_

A unique identifier for this modality. Must exactly match the name of the modality's directory inside each sample folder. Used throughout the pipeline output as a file name prefix and as the modality key in the final MuData object.

### `type` _(required, string)_

The modality type. Must be one of:

| Value | Modality |
|-------|----------|
| `"microscopy_image"` | Fluorescence or brightfield microscopy |
| `"msi"` | Mass Spectrometry Imaging |
| `"raman"` | Raman spectroscopy imaging |
| `"st"` | Spatial transcriptomics |

### `alignment_strategy` _(optional, string, default: `"manual"`)_

How the modality's coordinate system is aligned to the reference.

| Value | Behaviour |
|-------|-----------|
| `"manual"` | Interactive visual alignment via the alignment GUI. This is the most accurate option and the default. |
| `"pre_aligned"` | Skip alignment for this modality; FOCUS assumes its reference's spot coordinates are already expressed in this modality's coordinate space. Requires the reference to be spot-based (`st` or `msi`). The target can be any modality type. At most one non-reference modality may use this strategy per pipeline run. |

### `registration_type` _(optional, string, default: `"none"`)_

The computational method used to map this modality's features onto the reference coordinate system after alignment.

| Value | Behaviour | Compatible modality types |
|-------|-----------|--------------------------|
| `"none"` | No registration. This modality is aligned but **not** included in the final MuData output. | All |
| `"spot_interpolation"` | Gaussian-weighted average of the spot-based data in each anchor footprint. Runs on CPU. | `msi`, `st` |
| `"spot_aggregation"` | Equal-weight **sum** of the spot-based data in each anchor footprint (no normalization); accumulates signal for subcellular-resolution data (e.g. Visium HD). Runs on CPU. | `msi`, `st` |
| `"raman_pixel_interpolation"` | Gaussian-weighted interpolation over the hyperspectral OME-TIFF pixels, each pixel acting as a spot. Runs on CPU. | `raman` |
| `"feature_extraction"` | Prov-GigaPath patch embeddings (deep learning). **Only for H&E-stained brightfield RGB images**: the model is pretrained on H&E tiles and FOCUS does not check the stain, so other image types yield embeddings that carry no morphological meaning; use `"none"` for those. Requires an NVIDIA GPU and a HuggingFace token. | `microscopy_image` |

### `processing_settings` _(required, object)_

Modality-specific preprocessing parameters. See the per-type sections below.

### `registration_settings` _(optional, object, default: `{}`)_

Additional settings for the chosen registration method. May be an empty object when `registration_type` is `"none"`.

---

## Processing Settings Reference

=== "Spatial Transcriptomics (`st`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `min_count_per_spot` | int or null | Minimum total UMI count to retain a spot. |
    | `max_count_per_spot` | int or null | Maximum total UMI count (outlier removal). |
    | `min_genes_per_spot` | int or null | Minimum number of detected genes per spot. |
    | `max_genes_per_spot` | int or null | Maximum number of detected genes per spot. |
    | `min_spots_per_gene` | float or null | Minimum **fraction** of a sample's spots that must express a gene for that sample to count as passing. Must satisfy `0 < value < 1`. Dataset-level only. Default `null`. |
    | `min_count_spots_ratio_per_gene` | float or null | Minimum ratio of a gene's total counts to the number of spots expressing it, per sample. Must be `> 0`. Dataset-level only. Default `null`. |
    | `remove_mitochondrial_genes` | bool | Drop the genes flagged in `.var['mt']` (case-insensitive `MT-`/`MT.` name prefix) from the feature set. Applied per sample, before merging. Default `false`. |
    | `total_counts_normalize` | bool | Normalize each spot to a total count of 10,000. Default `false`. |
    | `log1p_transform` | bool | Apply log1p transformation after normalisation. Default `false`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

    Both gene-level filters are evaluated per sample, and a gene survives when it passes in at least one sample; with both thresholds set it must satisfy each in at least one sample. Every filtering and normalisation step is opt-in, so with defaults `.X` holds the raw counts from the input file.

=== "Mass Spectrometry Imaging (`msi`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `mass_tolerance` | int | m/z clustering tolerance in ppm. Must be an integer. A float raises `ValueError`. Default `10`. |
    | `frequency_threshold` | float | Minimum fraction of the maximum cluster weight for an m/z to enter the per-sample backbone. Default `0.01`. |
    | `intensity_normalization` | string | Normalization method (per sample and per ion mode): `"none"` (default), `"tic"`, `"log"`, `"clr"`, or `"tic_mean_scaled"` (rescales each spectrum to the mean total ion current over that sample's spots for that ion mode, preserving absolute scale; not comparable across samples). |
    | `min_intensity_threshold` | float | Minimum *peak* intensity for a peak to be used when estimating m/z recalibration offsets. Does not mask or filter spots. Default `10000.0`. |
    | `detect_background` | bool | Detect and flag off-tissue background spots in `obs["foreground"]` (all spots are still written). Only effective when `lipid_annotation_db` is also set. Default `false`. |
    | `sample_type` | string | Background-detection strategy: `"tissue"` (GMM + BIC, default) or `"microgrid"` (Otsu with a 25th-percentile floor). |
    | `recalibration_reference` | dict or null | Pre-computed per-ion-mode reference m/z arrays. Computed from the dataset when `null`. Default `null`. |
    | `lipid_annotation_db` | string or null | Path to a CSV or JSON lipid database with columns `db_name`, `ionized_mass`, `ion_mode`. Default `null`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

=== "Microscopy Image (`microscopy_image`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `color_enhancement` | bool | Apply gamma correction and contrast stretching. Default `true`. |
    | `gamma` | float | Exponent of the power-law intensity correction; below `1.0` brightens. Default `0.45`. |
    | `contrast_saturation` | float | Percentage of non-zero pixels saturated at each end of the histogram before the stretch. Default `0.35`. |
    | `remove_background` | bool | Fill off-tissue pixels with `background_color`. Default `true`. |
    | `background_color` | string | `"white"` or `"black"`; fill colour for removed background. Default `"white"`. |
    | `clip_percentile` | int | Percentile at which the inverted grayscale is clipped before the Otsu threshold. Default `99`. |
    | `min_object_coverage` | float | Minimum area of a tissue contour, as a fraction of the detection-proxy area. Default `0.01`. |
    | `crop_to_tissue` | bool | Crop the output image around the tissue. Uses the same tissue mask as background removal. Default `true`. |
    | `crop_margin` | int | Pixels added on each side of the tissue bounding box. Default `250`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

    Tissue detection works on any channel count: 1- and 2-channel images are promoted to 3 channels for the grayscale conversion, and their background polarity is detected automatically, so fluorescence acquisitions are segmented as well as brightfield ones. `background_color` does not follow that detection. Set it to `"black"` for dark-background images, otherwise the white fill leaves tissue and background indistinguishable.

=== "Raman Spectroscopy Imaging (`raman`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `max_workers` | int | Threads used to correct spectral channels with BaSiC, and `joblib` workers used for spectral cleaning. Default `8`. |
    | `savgol_window` | int | Savitzky-Golay window length, in channels, for spectral smoothing. Default `7`. |
    | `savgol_polyorder` | int | Polynomial order for the Savitzky-Golay filter; must be smaller than `savgol_window`. Default `3`. |
    | `bg_min_area_fraction` | float | Minimum area of a tissue contour, as a fraction of the segmentation mosaic area, for it to be kept. Default `0.05`. |
    | `otsu_threshold_factor` | float | Multiplier applied to the Otsu threshold for tissue segmentation; below `1.0` it keeps more pixels as tissue. Default `0.7`. |
    | `min_object_size` | int | Connected components of this many pixels or fewer are removed from the tissue mask. Default `500`. |
    | `force_recomputing` | bool | Reprocess even if the output or an intermediate `.npy` cache already exists. Default `false`. |

    All five Raman steps (LIF loading, BaSiC correction, background removal, spectral cleaning, ASHLAR stitching) always run; these parameters only change how they behave.

---

## Config Validation

FOCUS validates the entire configuration before running any processing. All errors are reported immediately so you can fix them without waiting for a long run to fail partway through.

Common validation errors and their causes:

| Error | Cause |
|-------|-------|
| `dataset_path not found` | The path does not exist or is not readable by the current user. |
| `reference_modality not found in declared modalities` | The value of `reference_modality` does not match any modality's `name`. |
| `perform_registration requires perform_alignment to be true` | You set `perform_registration: true` but `perform_alignment: false`. |
| `huggingface_token is required` | A modality uses `feature_extraction` but `huggingface_token` is null or absent. |
| `Registration type not compatible with modality type` | e.g., `feature_extraction` assigned to an `msi` modality. |
| `Missing modality directory for sample` | A sample directory is missing one of the declared modality subdirectories. |
| `Duplicate modality names` | Two modalities share the same `name` value. |
| `pre_aligned cannot be set on reference modality` | The reference modality must not use `alignment_strategy: "pre_aligned"`. |

!!! tip "Using the GUI to avoid errors"
    The GUI config builder performs live validation as you fill in each field and highlights incompatible combinations before you save the file. Using the GUI at least once to generate an initial config is recommended, even if you subsequently edit the JSON manually.

!!! note "Null fields"
    Fields that accept `null` (e.g., filter thresholds) are optional. Setting them to `null` disables the corresponding filter. Omitting them entirely is equivalent to setting them to `null`.
