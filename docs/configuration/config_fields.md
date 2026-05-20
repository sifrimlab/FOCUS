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

- `modality_name` (`string`): Modality containing `.geojson` files.
- `file_type` (`string`): Currently only `"geojson"`.

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
- **Description**: Re-run alignment for this modality even if cached alignment outputs are already present. Set to `true` to force re-alignment of this specific reference–target pair without affecting other modalities.

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
  - `"none"`
  - `"feature_extraction"` (microscopy only)
  - `"spot_interpolation"` (MSI, ST, Raman)

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

| Field | Type | Default |
|---|---|---|
| `color_enhancement` | bool | `true` |
| `gamma` | float | `0.45` |
| `contrast_saturation` | float | `0.35` |
| `remove_background` | bool | `true` |
| `background_color` | string | `"white"` |
| `gaussian_blur_kernel_size` | int | `251` |
| `clip_percentile` | int | `99` |
| `min_object_size` | int | `500` |
| `min_object_coverage` | float | `0.01` |
| `crop_to_tissue` | bool | `true` |
| `crop_margin` | int | `250` |
| `pyramid_levels` | int | `4` |
| `force_recomputing` | bool | `false` |

Example:

```json
"processing_settings": {
  "color_enhancement": true,
  "gamma": 0.45,
  "contrast_saturation": 0.35,
  "remove_background": true,
  "background_color": "white",
  "gaussian_blur_kernel_size": 251,
  "clip_percentile": 99,
  "min_object_size": 500,
  "min_object_coverage": 0.01,
  "crop_to_tissue": true,
  "crop_margin": 250,
  "pyramid_levels": 4,
  "force_recomputing": false
}
```

---

## `msi`

| Field | Type | Default |
|---|---|---|
| `mass_tolerance` | int | `10` |
| `frequency_threshold` | float | `0.01` |
| `intensity_normalization` | string | `"tic"` |
| `min_intensity_threshold` | float | `10000.0` |
| `detect_background` | bool | `true` |
| `sample_type` | string | `"tissue"` |
| `recalibration_reference` | dict or null | `null` |
| `lipid_annotation_db` | string or null | `null` |
| `force_recomputing` | bool | `false` |

Example:

```json
"processing_settings": {
  "mass_tolerance": 10,
  "frequency_threshold": 0.01,
  "intensity_normalization": "tic",
  "min_intensity_threshold": 10000.0,
  "detect_background": true,
  "sample_type": "tissue",
  "recalibration_reference": null,
  "lipid_annotation_db": null,
  "force_recomputing": false
}
```

---

## `raman`

| Field | Type | Default |
|---|---|---|
| `savgol_window` | int | `7` |
| `savgol_polyorder` | int | `3` |
| `otsu_threshold_factor` | float | `0.7` |
| `bg_min_area_fraction` | float | `0.05` |
| `min_object_size` | int | `500` |
| `max_workers` | int | `8` |
| `force_recomputing` | bool | `false` |

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
| `min_count_per_spot` | int or null | `null` |
| `max_count_per_spot` | int or null | `null` |
| `min_genes_per_spot` | int or null | `null` |
| `max_genes_per_spot` | int or null | `null` |
| `min_spots_per_gene` | float or null | `null` |
| `min_count_spots_ratio_per_gene` | float or null | `null` |
| `total_counts_normalize` | bool | `false` |
| `log1p_transform` | bool | `false` |
| `force_recomputing` | bool | `false` |

Example:

```json
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
}
```

---

## Registration Settings

### `feature_extraction`

Compatible modality type: `microscopy_image`.

| Field | Type | Default |
|---|---|---|
| `patch_size` | int | `224` |
| `background_color` | string | `"white"` |
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "patch_size": 224,
  "background_color": "white",
  "force_recomputing": false
}
```

### `spot_interpolation`

Compatible modality types: `msi`, `st`, `raman`.

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
        "min_intensity_threshold": 10000.0,
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
      "registration_type": "spot_interpolation",
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
        "gaussian_blur_kernel_size": 251,
        "clip_percentile": 99,
        "min_object_size": 500,
        "min_object_coverage": 0.01,
        "crop_to_tissue": true,
        "crop_margin": 250,
        "pyramid_levels": 4,
        "force_recomputing": false
      },
      "registration_settings": {
        "patch_size": 224,
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
- If `registration_type: "feature_extraction"` is used, `huggingface_token` is set and a CUDA GPU is available.
- `alignment_strategy: "pre_aligned"` is only used where modalities are already co-registered.
- `spatial_annotations.file_type` is `"geojson"` when annotations are enabled.
