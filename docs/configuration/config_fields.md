# Configuration Field Reference

This page documents all configuration fields that are referenced by the curated user, modality, and pipeline documentation.

---

## Top-Level Fields

### `dataset_path`

- **Type**: `string`
- **Required**: Yes
- **Description**: Absolute path to the dataset root containing sample directories.

Example:

```json
"dataset_path": "/data/my_tissue_cohort"
```

---

### `reference_modality`

- **Type**: `string`
- **Required**: Yes
- **Description**: Name of the modality that defines the shared output coordinate system.
- **Constraint**: Must exactly match one modality `name`.

Example:

```json
"reference_modality": "st"
```

---

### `perform_alignment`

- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Globally enable or disable alignment.

Example:

```json
"perform_alignment": true
```

---

### `perform_registration`

- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Globally enable or disable registration.

Example:

```json
"perform_registration": true
```

---

### `huggingface_token`

- **Type**: `string` or `null`
- **Required**: No
- **Default**: `null`
- **Description**: HuggingFace token used to download Prov-GigaPath for `feature_extraction`.
- **Required when**: Any modality uses `registration_type: "feature_extraction"`.

Example:

```json
"huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

---

### `spatial_annotations`

- **Type**: `object` or `null`
- **Required**: No
- **Default**: `null`
- **Description**: Enable spatial annotation transfer from per-sample GeoJSON files.

Structure:

```json
"spatial_annotations": {
  "modality_name": "microscopy",
  "file_type": "geojson"
}
```

Fields:

- `modality_name` (`string`): Name of a declared modality whose per-sample directory contains the `.geojson` files. Exactly one `.geojson` file must be present per sample.
- `file_type` (`string`): Currently only `"geojson"`.

!!! warning "Alignment required for non-reference annotation modalities"
    If `modality_name` is **not** the reference modality, `perform_alignment` must be `true`. Transferring annotations from a non-reference modality relies on the aligned coordinates (`obsm['{modality_name}_spatial']`) that the alignment stage produces; validation fails otherwise.

---

### `modalities`

- **Type**: `array`
- **Required**: Yes
- **Description**: List of modality definitions.

Each modality object includes:

- `name`
- `type`
- `alignment_strategy`
- `registration_type`
- `processing_settings`
- `registration_settings`

---

## Per-Modality Fields

### `name`

- **Type**: `string`
- **Required**: Yes
- **Description**: Unique modality identifier.
- **Constraint**: Must match the modality folder name in every sample.

Example:

```json
"name": "msi"
```

---

### `type`

- **Type**: `string`
- **Required**: Yes
- **Allowed values**:
  - `"microscopy_image"`
  - `"msi"`
  - `"raman"`
  - `"st"`

Example:

```json
"type": "microscopy_image"
```

---

### `alignment_strategy`

- **Type**: `string`
- **Required**: No
- **Default**: `"manual"`
- **Allowed values**:
  - `"manual"`
  - `"pre_aligned"`

Example:

```json
"alignment_strategy": "manual"
```

---

### `alignment_force_recomputing`

- **Type**: `boolean`
- **Required**: No
- **Default**: `false`
- **Description**: Re-run alignment for this modality even if cached alignment outputs are already present. Set to `true` to force re-alignment of this specific reference-target pair without affecting other modalities.

Example:

```json
"alignment_force_recomputing": false
```

---

### `registration_type`

- **Type**: `string`
- **Required**: No
- **Default**: `"none"`
- **Allowed values**:
  - `"none"` (any modality; skips registration)
  - `"feature_extraction"` (`microscopy_image` only, and only for H&E-stained brightfield RGB images; see [Registration Settings](#feature_extraction))
  - `"spot_interpolation"` (`msi`, `st`)
  - `"spot_aggregation"` (`msi`, `st`)
  - `"raman_pixel_interpolation"` (`raman`)
- Compatibility is enforced: an incompatible `registration_type`/modality-type pairing raises a validation error.

Example:

```json
"registration_type": "spot_interpolation"
```

---

### `processing_settings`

- **Type**: `object`
- **Required**: Yes
- **Description**: Preprocessing parameters specific to the modality `type`.

---

### `registration_settings`

- **Type**: `object`
- **Required**: No
- **Default**: `{}`
- **Description**: Registration-method-specific settings.

---

## Processing Settings by Modality

## `microscopy_image`

| Field | Type | Default | Step |
|---|---|---|---|
| `color_enhancement` | bool | `true` | Colour enhancement |
| `gamma` | float | `0.45` | Colour enhancement |
| `contrast_saturation` | float | `0.35` | Colour enhancement |
| `remove_background` | bool | `true` | Background removal |
| `background_color` | string | `"white"` | Background removal |
| `clip_percentile` | int | `99` | Background removal |
| `min_object_coverage` | float | `0.01` | Background removal |
| `crop_to_tissue` | bool | `true` | Cropping |
| `crop_margin` | int | `250` | Cropping |
| `force_recomputing` | bool | `false` | All |

- `gamma`: exponent of `I^gamma` on the image normalised to `[0, 1]`. Below `1.0` it brightens midtones, above `1.0` it darkens them.
- `contrast_saturation`: a **percentage**. The `contrast_saturation` and `100 − contrast_saturation` percentiles of the non-zero pixels become the clip bounds, which are then rescaled to `[0, 1]`. Those percentiles are computed once over the whole image, with all channels pooled. The default `0.35` saturates 0.35% of those pixels at each end.
- `background_color`: `"white"` or `"black"`. Any other value raises `ValueError` when background removal runs.
- `clip_percentile`: percentile at which the inverted grayscale is clipped before the Gaussian blur that feeds the Otsu threshold.
- `min_object_coverage`: a tissue contour is kept when its area is at least this fraction of the detection-proxy area. The proxy is the image downscaled to at most 9 megapixels.
- `crop_margin`: full-resolution pixels added on each side of the tissue bounding box, clamped to the image bounds.
- `crop_to_tissue`: uses the same tissue mask as background removal, so enabling it alone still runs the full Otsu detection.
- `force_recomputing`: microscopy keeps no intermediate caches. The only cache is the output OME-TIFF itself, which is reused whenever it exists and this flag is false.

The number of OME-TIFF pyramid levels is not a configurable field; it is computed automatically from the final image dimensions. The tissue-detection tuning is not configurable either: the 9-megapixel proxy cap, the 25 px Gaussian blur kernel and the 50 px speck-removal size are internal constants.

Except for `background_color`, these fields are not checked: an out-of-range value reaches the processing code, where it either raises (a `clip_percentile` above 100 fails inside `numpy.percentile`) or changes the result. The out-of-range value is never reported during configuration validation.

Example:

```json
"processing_settings": {
  "color_enhancement": true,
  "gamma": 0.45,
  "contrast_saturation": 0.35,
  "remove_background": true,
  "background_color": "white",
  "clip_percentile": 99,
  "min_object_coverage": 0.01,
  "crop_to_tissue": true,
  "crop_margin": 250,
  "force_recomputing": false
}
```

---

## `msi`

| Field | Type | Default |
|---|---|---|
| `mass_tolerance` | int | `10` |
| `frequency_threshold` | float | `0.01` |
| `intensity_normalization` | string | `"none"` |
| `detect_background` | bool | `false` |
| `sample_type` | string | `"tissue"` |
| `resolution` | string | `"standard"` |
| `recalibration_reference` | dict, string or null | `null` |
| `internal_standard_mz` | dict, string or null | `null` |
| `lipid_annotation_db` | string or null | `null` |
| `force_recomputing` | bool | `false` |

- `intensity_normalization` allowed values: `"none"`, `"tic"`, `"log"`, `"clr"`, `"tic_mean_scaled"`, `"median"`, `"internal_standard"` (all applied per sample and per ion mode). `"tic"` makes each spectrum sum to 1; `"tic_mean_scaled"` rescales each spectrum to the mean total ion current over that sample's spots for that ion mode, preserving absolute intensity scale (values are therefore not comparable across samples); `"median"` divides each spectrum by the median of its nonzero features; `"internal_standard"` divides each spectrum by the summed intensity of the internal standards and requires `internal_standard_mz`.
- `recalibration_reference` and `internal_standard_mz`: per-ion-mode m/z values, as a mapping (`{"pos": [760.585, 798.541], "neg": [885.550]}`) or as the string `"pos: 760.585, 798.541; neg: 885.550"`. `recalibration_reference` replaces the automatic reference selection; `internal_standard_mz` lists sprayed internal standards, which are always used as recalibration references.
- `sample_type` allowed values: `"tissue"`, `"microgrid"`. `"tissue"` separates a contiguous section from background with a GMM + BIC model and morphological cleanup. `"microgrid"` treats the slide as a dominant background with sparse cells of one or a few spots, and detects cells as local outliers against a background ion model.
- `resolution` allowed values: `"standard"` (default, multicellular pixels), `"sub_cellular"` (weak high-resolution pixels). It sets the spatial neighbourhood of the alignment clustering (1 or 2 rings).
- `lipid_annotation_db`: path to a CSV or JSON file with columns `db_name`, `ionized_mass`, `ion_mode`.
- `detect_background` only takes effect when `lipid_annotation_db` is also set. Without a database the detection step is skipped and every spot is flagged foreground.
- `mass_tolerance` must be an integer; a float value raises `ValueError` during processing.
- `frequency_threshold`: minimum presence of an m/z backbone feature (the share of spectra holding it above the background noise), as a fraction of the most present feature of a sample. A feature passing in one sample is kept. It controls the number of features.

The example below enables TIC normalization and background detection explicitly; these are not the defaults.

Example:

```json
"processing_settings": {
  "mass_tolerance": 10,
  "frequency_threshold": 0.01,
  "intensity_normalization": "tic",
  "detect_background": true,
  "sample_type": "tissue",
  "recalibration_reference": null,
  "lipid_annotation_db": null,
  "force_recomputing": false
}
```

---

## `raman`

| Field | Type | Default | Step |
|---|---|---|---|
| `savgol_window` | int | `7` | Spectral cleaning |
| `savgol_polyorder` | int | `3` | Spectral cleaning |
| `otsu_threshold_factor` | float | `0.7` | Background removal |
| `bg_min_area_fraction` | float | `0.05` | Background removal |
| `min_object_size` | int | `500` | Background removal |
| `max_workers` | int | `8` | BaSiC correction, spectral cleaning |
| `force_recomputing` | bool | `false` | All |

- `savgol_window` / `savgol_polyorder`: window length in channels and polynomial order of the Savitzky-Golay filter in the RamanSPy cleaning pipeline. SciPy raises `ValueError` when the order is not smaller than the window, or when the window is longer than a scan's channel block. The other three cleaning stages (Whitaker-Hayes despiking, IASLS baseline, min-max normalisation) use their library defaults and are not configurable.
- `otsu_threshold_factor`: the Otsu threshold computed on the segmentation preview mosaic is multiplied by this value before binarisation. Below `1.0` it lowers the threshold, keeping more pixels as tissue.
- `bg_min_area_fraction`: tissue contours are kept when their area is at least this fraction of the preview mosaic area.
- `min_object_size`: connected components of this many pixels or fewer are removed from the mask (`skimage.morphology.remove_small_objects(..., max_size=...)`, inclusive).
- `max_workers`: number of threads used to correct spectral channels with BaSiC, and number of `joblib` workers used to clean tile × scan work units.
- `force_recomputing`: also invalidates the per-step `.npy` caches (`basic_corrected_tiles.npy`, `segmented_tiles.npy`, `raman_corrected_tiles.npy`), which are otherwise reused whenever present.

No step of the Raman pipeline can be switched off from the configuration; these fields only change how each step behaves.

Example:

```json
"processing_settings": {
  "savgol_window": 7,
  "savgol_polyorder": 3,
  "otsu_threshold_factor": 0.7,
  "bg_min_area_fraction": 0.05,
  "min_object_size": 500,
  "max_workers": 8,
  "force_recomputing": false
}
```

---

## `st`

| Field | Type | Default |
|---|---|---|
| `capture` | string | `"sequencing"` |
| `unit` | string | `"spot"` |
| `normalization` | string | `"auto"` |
| `quality_control` | bool | `true` |
| `remove_mitochondrial_genes` | bool | `false` |
| `force_recomputing` | bool | `false` |

- `capture`: `"sequencing"` (Visium, Visium HD, Stereo-seq, Slide-seq) or `"imaging"` (Xenium, MERFISH, CosMx). Sets the gene rule (sequencing: detected in at least 20 spots of one sample; imaging: every detected gene) and the `auto` normalization.
- `unit`: `"spot"` (multicellular spots), `"bin"` (sub-cellular square bins or beads) or `"cell"` (segmented cells). Sets the outlier rule of `quality_control` and the clustering preset of the alignment colours.
- `normalization`: `"auto"`, `"none"`, `"log_normalize"`, `"cp10k_log"`, `"size_factors"` or `"area_log_normalize"`, applied once to the merged dataset. Any value but `"none"` keeps the counts in `.layers['raw']`. See [ST normalization](../modalities/transcriptomics.md#normalization).
- `quality_control`: removes the outlier spots of the unit's rule (local outliers for spots, global outliers for cells, none for bins). Out-of-tissue and zero-count spots and control features are removed in every case.
- `remove_mitochondrial_genes`: when `true`, drops mitochondrial genes (flagged by a case-insensitive `MT-`/`MT.` name prefix) from the feature set. Applied per sample, after QC metrics are computed, so `pct_counts_mt` still describes the matrix before removal.
- The settings `resolution`, `min_count_per_spot`, `max_count_per_spot`, `min_genes_per_spot`, `max_genes_per_spot`, `min_spots_per_gene`, `min_count_spots_ratio_per_gene`, `total_counts_normalize` and `log1p_transform` were removed; a configuration that sets one fails validation and names the replacement.

Example:

```json
"processing_settings": {
  "capture": "sequencing",
  "unit": "bin",
  "normalization": "auto",
  "quality_control": true,
  "remove_mitochondrial_genes": false,
  "force_recomputing": false
}
```

### `spot_interpolation`

Compatible modality types: `msi`, `st`.

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

### `spot_aggregation`

Compatible modality types: `msi`, `st`. Sums the target spots inside each anchor footprint instead of averaging them. A normalized ST target is summed on its raw counts and the merged registered file is normalized once with the target's method. Intended for subcellular-resolution data (e.g. Visium HD). See [Registration](../pipeline/registration.md#spot_aggregation).

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

### `raman_pixel_interpolation`

Compatible modality type: `raman`. Uses the same Gaussian footprint interpolation as `spot_interpolation`, applied to the pixels of the hyperspectral OME-TIFF. See [Registration](../pipeline/registration.md#raman_pixel_interpolation).

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

---

## Consistent End-to-End Example

```json
{
  "dataset_path": "/data/my_tissue_cohort",
  "reference_modality": "st",
  "perform_alignment": true,
  "perform_registration": true,
  "huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "spatial_annotations": {
    "modality_name": "microscopy",
    "file_type": "geojson"
  },
  "modalities": [
    {
      "name": "st",
      "type": "st",
      "alignment_strategy": "manual",
      "registration_type": "none",
      "processing_settings": {
        "capture": "sequencing",
        "unit": "spot",
        "normalization": "auto",
        "force_recomputing": false
      },
      "registration_settings": {}
    },
    {
      "name": "msi",
      "type": "msi",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "spot_interpolation",
      "processing_settings": {
        "mass_tolerance": 10,
        "frequency_threshold": 0.01,
        "intensity_normalization": "tic",
        "detect_background": true,
        "sample_type": "tissue",
        "recalibration_reference": null,
        "lipid_annotation_db": "resources/lipid_db.csv",
        "force_recomputing": false
      },
      "registration_settings": {
        "force_recomputing": false
      }
    },
    {
      "name": "raman",
      "type": "raman",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "raman_pixel_interpolation",
      "processing_settings": {
        "savgol_window": 7,
        "savgol_polyorder": 3,
        "otsu_threshold_factor": 0.7,
        "bg_min_area_fraction": 0.05,
        "min_object_size": 500,
        "max_workers": 8,
        "force_recomputing": false
      },
      "registration_settings": {
        "force_recomputing": false
      }
    },
    {
      "name": "microscopy",
      "type": "microscopy_image",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "feature_extraction",
      "processing_settings": {
        "color_enhancement": true,
        "gamma": 0.45,
        "contrast_saturation": 0.35,
        "remove_background": true,
        "background_color": "white",
        "clip_percentile": 99,
        "min_object_coverage": 0.01,
        "crop_to_tissue": true,
        "crop_margin": 250,
        "force_recomputing": false
      },
      "registration_settings": {
        "background_color": "white",
        "force_recomputing": false
      }
    }
  ]
}
```

---

## Validation Checklist

- `dataset_path` exists and is readable/writable.
- `reference_modality` matches one declared modality `name`.
- Every sample directory contains every declared modality subdirectory.
- If `registration_type: "feature_extraction"` is used, `huggingface_token` is set; a CUDA GPU is strongly recommended (the CPU fallback is impractically slow for real images).
- `alignment_strategy: "pre_aligned"` is only used where modalities are already co-registered.
- `spatial_annotations.file_type` is `"geojson"` when annotations are enabled.
