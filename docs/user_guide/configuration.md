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
        "capture": "sequencing",
        "unit": "spot",
        "normalization": "auto",
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
        "image_type": "brightfield",
        "z_projection": "auto",
        "crop_to_tissue": false,
        "remove_background": false,
        "pixel_size": null,
        "force_recomputing": false
      },
      "registration_settings": {
        "background_color": "white",
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
| `"spot_interpolation"` | Overlap-area registration of the spot-based data with each anchor footprint, for any combination of spot sizes (ST counts are shared by overlap, MSI intensities averaged by overlap area). Needs reference and target coordinates in µm. Runs on CPU. | `msi`, `st` |
| `"raman_pixel_interpolation"` | Gaussian-weighted interpolation over the hyperspectral OME-TIFF pixels, each pixel acting as a spot. Runs on CPU. | `raman` |
| `"pixel_composition"` | Fraction of each reference footprint occupied by each pixel phenotype of a dataset-level codebook (Raman: endmembers of linear spectral unmixing; fluorescence: Pixie pixel clusters), plus the mean pixel values in `obsm['channel_mean']` and a per-sample label map. Brightfield images are rejected. Runs on CPU. | `raman`, `microscopy_image` |
| `"feature_extraction"` | One Prov-GigaPath embedding (deep learning) per reference spot, the mean of the encoder tokens inside its footprint, and optionally the embedding of the image patch around it. **Only for H&E-stained brightfield images**: the model is pretrained on H&E tiles. Fluorescence images are rejected (at validation when `image_type` is `"fluorescence"`, at registration from the image type recorded by preprocessing); the stain of a brightfield image is not checked, so other stains yield embeddings that carry no morphological meaning. Use `"none"` for those. Requires a HuggingFace token; runs on an NVIDIA GPU when available, otherwise on the CPU (impractically slow for real images). | `microscopy_image` |

### `processing_settings` _(required, object)_

Modality-specific preprocessing parameters. See the per-type sections below.

### `registration_settings` _(optional, object, default: `{}`)_

Additional settings for the chosen registration method. May be an empty object when `registration_type` is `"none"`. Every method accepts `force_recomputing`; `feature_extraction` also accepts `background_color` (`"white"` or `"black"`), used for images that preprocessing did not fill, and `patch_embeddings` (`true` or `false`, default `false`), which also stores the embedding of the image patch around each spot. See [Configuration Fields](../configuration/config_fields.md#registration-settings).

---

## Processing Settings Reference

=== "Spatial Transcriptomics (`st`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `capture` | string | `"sequencing"` (default; Visium, Visium HD, Stereo-seq, Slide-seq) or `"imaging"` (Xenium, MERFISH, CosMx). Sets the gene rule and the `auto` normalization. |
    | `unit` | string | `"spot"` (default; multicellular spots), `"bin"` (sub-cellular bins or beads) or `"cell"` (segmented cells). Sets the outlier rule and the clustering preset. |
    | `normalization` | string | `"auto"` (default), `"none"`, `"log_normalize"`, `"cp10k_log"`, `"size_factors"` or `"area_log_normalize"`, applied once to the merged dataset. |
    | `quality_control` | bool | Remove the outlier spots of the unit's rule. Out-of-tissue spots are removed in every case. Default `true`. |
    | `remove_controls_and_empty` | bool | Remove the control features (probes, codewords) and the zero-count spots. `false` keeps both. Default `true`. |
    | `remove_mitochondrial_genes` | bool | Drop the genes flagged in `.var['mt']` (case-insensitive `MT-`/`MT.` name prefix) from the feature set. Applied per sample, before merging. Default `false`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

    The settings `resolution`, `min_count_per_spot`, `max_count_per_spot`, `min_genes_per_spot`, `max_genes_per_spot`, `min_spots_per_gene`, `min_count_spots_ratio_per_gene`, `total_counts_normalize` and `log1p_transform` were removed and fail validation. See [Spatial Transcriptomics](../modalities/transcriptomics.md#processing-parameters).

=== "Mass Spectrometry Imaging (`msi`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `mass_tolerance` | number | Mass tolerance in ppm (integer or decimal): feature detection, backbone merge span, binning reach and annotation window. Default `10`. |
    | `sample_type` | string | Experiment type, which calibrates foreground detection: `"tissue"` (clean background, default), `"tissue_noisy"` (noisy background), `"cell_culture"` (gradient domains) or `"microgrid"` (isolated single cells). |
    | `intensity_normalization` | string | Row normalization at a fixed scale shared by every sample (per sample and per ion mode): `"none"` (default), `"tic"` (rows sum to the number of features), `"median"` (median of the nonzero features is 1), `"rms"` (root mean square is 1) or `"internal_standard"` (standard intensity is 1; requires `internal_standard_mz`). |
    | `recalibration_reference` | dict, string or null | Per-ion-mode reference m/z values, as `{"pos": [760.585, 798.541]}` or `"pos: 760.585, 798.541; neg: 885.550"`. Selected from the dataset when `null`. Default `null`. |
    | `internal_standard_mz` | dict, string or null | m/z of internal standards sprayed over the sections, per ion mode (same forms as `recalibration_reference`). Always used as recalibration references and required by `"internal_standard"` normalization. Default `null`. |
    | `lipid_annotation_db` | string or null | Path to a CSV or JSON lipid database with columns `db_name`, `ionized_mass`, `ion_mode`. Default `null`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

    The settings `detect_background`, `frequency_threshold` and `min_intensity_threshold`, and the normalizations `"log"`, `"clr"` and `"tic_mean_scaled"`, were removed and fail validation with the reason. See [MSI](../modalities/msi.md#processing-parameters).

=== "Microscopy Image (`microscopy_image`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `image_type` | string | `"auto"` (default; read from the acquisition metadata), `"brightfield"` (H&E, IHC, special stains) or `"fluorescence"` (IF, confocal, multiplexed). Selects the tissue detection, the `auto` Z projection, the background fill, the display rendering and the codec. |
    | `z_projection` | string | `"auto"` (default; maximum projection for fluorescence, best focus for brightfield), `"max"`, `"best_focus"` or `"middle"`. How a Z-stack becomes one plane. |
    | `crop_to_tissue` | bool | Crop the output to the tissue bounding box plus a fixed 100 µm margin. Default `false`. |
    | `remove_background` | bool | Set every pixel outside the tissue to the fill of the image type (white for brightfield, 0 for fluorescence). Default `false`. |
    | `pixel_size` | float or null | µm per pixel, used only when the file metadata has none. Default `null`. |
    | `force_recomputing` | bool | Reprocess even if output already exists. Default `false`. |

    The output stores the source pixels losslessly (sample type, every channel, no contrast change); contrast is stored as display windows that the alignment GUI applies. Tissue is always detected, for the stored outline and the display windows. The settings `color_enhancement`, `gamma`, `contrast_saturation`, `background_color`, `min_object_coverage`, `clip_percentile` and `crop_margin` were removed and fail validation. See [Microscopy Images](../modalities/microscopy.md#processing-parameters).

=== "Raman Spectroscopy Imaging (`raman`)"

    | Parameter | Type | Description |
    |-----------|------|-------------|
    | `denoise` | bool | Project every pixel spectrum of a multi-channel acquisition on the spectral components that are real at the cellular scale (4.5 µm). Default `true`. |
    | `remove_offset` | bool | Subtract from each averaged spectrum (spot mean, spot-sized block) its minimum over the spectral window, the near-flat non-resonant background. Applied by registration; the stored pixels are not changed. Default `false`. |
    | `normalization` | string | `"none"` (default, concentration-linear spot spectra) or `"area"`: registration divides each averaged spectrum by its mean over the acquisition's channels; the stored pixels are not changed. |
    | `force_recomputing` | bool | Reprocess even if the output carries the current fingerprint. Default `false`. |

    The output always stores linear intensities. `remove_offset` and `normalization` are recorded per image and applied by registration to spectra averaged over many pixels, because on a single noisy pixel the minimum and the mean are biased by an amount that follows the local illumination. The settings `savgol_window`, `savgol_polyorder`, `bg_min_area_fraction`, `otsu_threshold_factor`, `min_object_size` and `max_workers` were removed and fail validation. See [Raman](../modalities/raman.md#processing-parameters).

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
| `these microscopy settings were removed` | A `microscopy_image` modality sets a key removed in `microscopy-3` (e.g. `gamma`, `color_enhancement`); the message gives the reason. |
| `feature_extraction ... does not apply to fluorescence images` | A `microscopy_image` modality with `image_type: "fluorescence"` uses `registration_type: "feature_extraction"`. |
| `Missing modality directory for sample` | A sample directory is missing one of the declared modality subdirectories. |
| `Duplicate modality names` | Two modalities share the same `name` value. |
| `pre_aligned cannot be set on reference modality` | The reference modality must not use `alignment_strategy: "pre_aligned"`. |

!!! tip "Using the GUI to avoid errors"
    The GUI config builder performs live validation as you fill in each field and highlights incompatible combinations before you save the file. Using the GUI at least once to generate an initial config is recommended, even if you subsequently edit the JSON manually.

!!! note "Null fields"
    Fields that accept `null` (e.g., filter thresholds) are optional. Setting them to `null` disables the corresponding filter. Omitting them entirely is equivalent to setting them to `null`.
