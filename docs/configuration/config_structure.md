# Configuration File Structure

## Overview

FOCUS uses one JSON configuration file to define preprocessing, alignment, registration, and optional compilation.

This page documents the **canonical structure** and the set of config fields that are described in the curated docs under:

- `docs/overview.md`
- `docs/user_guide/`
- `docs/modalities/`
- `docs/pipeline/`

---

## Canonical JSON Structure

```json
{
  "dataset_path": "string",
  "reference_modality": "string",
  "perform_alignment": true,
  "alignment_force_recomputing": false,
  "perform_registration": true,
  "huggingface_token": null,
  "spatial_annotations": {
    "modality_name": "string",
    "file_type": "geojson"
  },
  "modalities": [
    {
      "name": "string",
      "type": "microscopy_image | msi | raman | st",
      "alignment_strategy": "manual | pre_aligned",
      "registration_type": "none | feature_extraction | spot_interpolation",
      "processing_settings": {},
      "registration_settings": {}
    }
  ]
}
```

`spatial_annotations`, `huggingface_token`, and `registration_settings` are optional. All other fields are expected in typical pipeline runs.

---

## Top-Level Fields

| Field | Type | Description |
|---|---|---|
| `dataset_path` | string | Absolute path to the dataset root containing sample directories. |
| `reference_modality` | string | Modality `name` that defines the shared coordinate system. |
| `perform_alignment` | boolean | Enable/disable alignment stage globally. |
| `alignment_force_recomputing` | boolean | Re-run alignment even when cached aligned outputs exist. |
| `perform_registration` | boolean | Enable/disable registration stage globally. |
| `huggingface_token` | string or null | Required when any modality uses `registration_type: "feature_extraction"`. |
| `spatial_annotations` | object or null | GeoJSON annotation transfer settings. |
| `modalities` | array | List of per-modality configurations. |

### `spatial_annotations` object

```json
{
  "spatial_annotations": {
    "modality_name": "microscopy",
    "file_type": "geojson"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `modality_name` | string | Modality whose sample directories contain the `.geojson` files. |
| `file_type` | string | Annotation file format; currently `"geojson"`. |

---

## Per-Modality Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Unique modality identifier; must match sample subdirectory name exactly. |
| `type` | string | One of `microscopy_image`, `msi`, `raman`, `st`. |
| `alignment_strategy` | string | `manual` or `pre_aligned`. |
| `registration_type` | string | `none`, `feature_extraction`, or `spot_interpolation`. |
| `processing_settings` | object | Modality-specific preprocessing settings. |
| `registration_settings` | object | Registration-specific settings (empty object allowed). |

### Alignment strategies

| Value | Meaning |
|---|---|
| `manual` | Interactive GUI alignment. |
| `pre_aligned` | Skip GUI and assume target already shares reference coordinates. |

### Registration types

| Value | Meaning | Compatible modality type(s) |
|---|---|---|
| `none` | Skip registration for this modality | all |
| `feature_extraction` | Patch embedding registration (Prov-GigaPath) | `microscopy_image` |
| `spot_interpolation` | Gaussian-weighted spot interpolation | `msi`, `st`, `raman` |

---

## Processing Settings by Modality

### `microscopy_image`

```json
{
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

### `msi`

```json
{
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

### `raman`

```json
{
  "savgol_window": 7,
  "savgol_polyorder": 3,
  "otsu_threshold_factor": 0.7,
  "bg_min_area_fraction": 0.05,
  "min_object_size": 500,
  "max_workers": 8,
  "force_recomputing": false
}
```

### `st`

```json
{
  "min_count_per_spot": null,
  "max_count_per_spot": null,
  "min_genes_per_spot": null,
  "max_genes_per_spot": null,
  "min_spots_per_gene": null,
  "min_count_spots_ratio_per_gene": null,
  "total_counts_normalize": false,
  "log1p_transform": false,
  "force_recomputing": false
}
```

---

## Registration Settings by Method

### `feature_extraction`

```json
{
  "patch_size": 224,
  "min_max_rescale": true,
  "background_color": "white",
  "force_recomputing": false
}
```

### `spot_interpolation`

```json
{
  "force_recomputing": false
}
```

---

## Complete Example (All Four Modalities)

```json
{
  "dataset_path": "/data/my_tissue_cohort",
  "reference_modality": "st",
  "perform_alignment": true,
  "alignment_force_recomputing": false,
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
        "min_max_rescale": true,
        "background_color": "white",
        "force_recomputing": false
      }
    }
  ]
}
```

---

## Validation Rules (High-Level)

- `reference_modality` must match one modality `name` exactly.
- Every sample directory must include every configured modality directory.
- `feature_extraction` requires `microscopy_image`, CUDA-capable GPU, and `huggingface_token`.
- `spot_interpolation` is used for `msi`, `st`, and currently `raman`.
- `pre_aligned` is only valid for non-reference modalities when coordinates are already co-registered.
- Compilation to `.h5mu` occurs only when reference is spot-based (`msi`/`st`) and at least one modality runs registration.
