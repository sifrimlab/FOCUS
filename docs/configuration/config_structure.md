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
      "alignment_force_recomputing": false,
      "registration_type": "none | feature_extraction | spot_interpolation | raman_pixel_interpolation",
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
| `alignment_force_recomputing` | boolean | Re-run alignment for this modality even when cached outputs exist. Default `false`. |
| `registration_type` | string | `none`, `feature_extraction`, `spot_interpolation`, or `raman_pixel_interpolation`. |
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
| `feature_extraction` | One Prov-GigaPath embedding per reference spot, restricted to the image inside its footprint; brightfield images only (fluorescence is rejected) | `microscopy_image` |
| `spot_interpolation` | Overlap-area registration of the target spots with each anchor footprint, for any combination of spot sizes: MSI intensities are area-weighted means, ST counts are shared by overlap and then normalized on the merged file | `msi`, `st` |
| `raman_pixel_interpolation` | Gaussian-weighted pixel interpolation over the hyperspectral OME-TIFF | `raman` |

---

## Processing Settings by Modality

### `microscopy_image`

```json
{
  "image_type": "auto",
  "z_projection": "auto",
  "crop_to_tissue": false,
  "remove_background": false,
  "pixel_size": null,
  "force_recomputing": false
}
```

### `msi`

```json
{
  "mass_tolerance": 10,
  "intensity_normalization": "tic",
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
  "capture": "sequencing",
  "unit": "spot",
  "normalization": "auto",
  "quality_control": true,
  "remove_controls_and_empty": true,
  "remove_mitochondrial_genes": false,
  "force_recomputing": false
}
```

---

## Registration Settings by Method

### `feature_extraction`

```json
{
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

### `raman_pixel_interpolation`

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
        "intensity_normalization": "tic",
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
      }
    }
  ]
}
```

---

## Validation Rules (High-Level)

- `reference_modality` must match one modality `name` exactly.
- Every sample directory must include every configured modality directory.
- `feature_extraction` requires `microscopy_image` and `huggingface_token`; a CUDA-capable GPU is strongly recommended (CPU fallback).
- `spot_interpolation` is used for `msi` and `st` and needs the reference and target coordinates in µm; `raman` uses `raman_pixel_interpolation`. The former `spot_aggregation` was removed (it is now part of `spot_interpolation`) and fails validation.
- `pre_aligned` is only valid for non-reference modalities when coordinates are already co-registered.
- Compilation to `.h5mu` occurs only when the reference is spot-based (`msi`/`st`) and `perform_registration` is enabled.
