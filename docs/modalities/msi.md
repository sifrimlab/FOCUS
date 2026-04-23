# Mass Spectrometry Imaging (MSI / Lipidomics)

## Overview

Mass Spectrometry Imaging (MSI) provides spatially resolved molecular profiles — most commonly lipid distributions — across tissue sections. FOCUS reads the imzML/IBD file format produced by Bruker instruments and compatible exporters. Both positive and negative ion modes are supported, either independently or as a paired dual-mode acquisition from the same tissue section.

The preprocessing pipeline parses the imzML metadata, corrects any instrument rotation error, optionally aligns dual ion mode acquisitions, builds a consensus m/z grid across all spectra and samples, interpolates each spectrum onto that grid, detects tissue versus background spots, normalises intensities, and outputs an AnnData object per sample plus a merged multi-sample dataset.

---

## Input Format

MSI data is stored as two paired files:

| File | Role |
|------|------|
| `.imzML` | XML metadata: pixel grid, scan settings, per-spectrum binary offsets and data types |
| `.ibd` | Binary file: raw m/z arrays and intensity arrays, indexed by the offsets in the imzML |

FOCUS automatically locates the `.imzML` file in the sample directory and derives the `.ibd` path by replacing the extension.

---

## Directory Layout

=== "Single ion mode"

    ```
    dataset_root/
    ├── sample_A/
    │   └── lipidomics/
    │       ├── data.imzML
    │       └── data.ibd
    ```

=== "Dual ion mode (positive + negative)"

    ```
    dataset_root/
    ├── sample_A/
    │   └── lipidomics/
    │       ├── pos/
    │       │   ├── data.imzML
    │       │   └── data.ibd
    │       └── neg/
    │           ├── data.imzML
    │           └── data.ibd
    ```

When `double_ion_mode` is enabled, FOCUS expects both `pos/` and `neg/` subdirectories.

!!! tip "File naming"
    Any filename is accepted; FOCUS loads the first `.imzML` file found in the directory.

---

## Preprocessing Steps

1. **Parse imzML metadata** — the imzML XML is parsed to extract pixel grid coordinates, physical stage coordinates (µm), raster size, per-spectrum binary offsets, and data types (`float32` or `float64`). Physical coordinates are read from `3DPositionX/Y` user parameters when present; pixel indices are used as fallback.

2. **Rotation correction** — a linear regression is fit to the stage coordinates of the most densely sampled pixel column, and the resulting slope angle is used to de-rotate all physical coordinates to align the scan with the Cartesian axes.

3. **Dual ion mode alignment** — when both ion modes are present, unpaired spots (acquisition artifacts) are removed by set intersection. An affine transformation is fitted from positive to negative mode physical coordinates; the transformed positive coordinates are averaged with the negative coordinates to yield the consensus spot centre positions.

4. **M/z calibration** — if a lipid annotation database is provided, up to five high-confidence reference peaks (selected by cross-sample frequency and intensity) are identified per ion mode and used to compute per-row m/z offsets. Each spectrum's m/z array is shifted by its row-wise mean offset to correct for spatial mass drift.

5. **Consensus m/z grid construction** — all m/z values across all spectra and all samples are pooled, rounded to six decimal places, and clustered using a sliding-window weighted-centroid algorithm within `mass_tolerance` ppm. Clusters present in fewer than `frequency_threshold × max_cluster_count` spectra are discarded. Clustering is parallelised across CPU cores with chunked overlap merging.

6. **Intensity interpolation** — each spectrum is resampled onto the consensus m/z grid using inverse-distance weighting: for each original peak, its intensity is distributed among all reference bins falling within `mass_tolerance` ppm, weighted by 1/(ppm_distance + ε). The result is a dense `(N_spots, N_mz)` matrix.

7. **Tissue / background detection** — when `detect_background=True`, three spectral complexity features are computed per spot: Shannon entropy, peak count, and log(TIC). An optional 4th feature (annotation DB hit ratio) is added when a lipid database is provided. For `sample_type="tissue"`, a 2-component Gaussian Mixture Model is fit; BIC determines whether the distribution is unimodal (all spots kept) or bimodal (posterior ≥ 0.5 classifies tissue). Morphological hole-filling and binary opening clean up the spatial mask. For `sample_type="microgrid"`, Otsu thresholding with a 25th-percentile floor is used and spatial cleanup is disabled.

8. **Intensity normalisation** — supported methods are TIC (divide each spectrum by its total ion current), log (log-transform), or none. Applied after background detection.

9. **Format as AnnData** — the interpolated matrix, spatial coordinates, and metadata are assembled into an AnnData object and saved as a gzip-compressed `.h5ad` file. Ion mode is encoded in `.var` and a per-mode merged dataset is written.

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `mass_tolerance` | `int` | `10` | Mass tolerance in ppm for m/z clustering and interpolation | Positive integer |
| `frequency_threshold` | `float` | `0.01` | Minimum relative cluster frequency to retain an m/z in the consensus grid | `0.0` – `1.0` |
| `intensity_normalization` | `str` | `"tic"` | Intensity normalisation method | `"tic"`, `"log"`, `"none"` |
| `min_intensity_threshold` | `float` | `10000.0` | Minimum peak intensity considered valid during m/z recalibration | Non-negative float |
| `detect_background` | `bool` | `True` | Detect and exclude background spots from the output | `True`, `False` |
| `sample_type` | `str` | `"tissue"` | Tissue architecture type; controls background detection strategy | `"tissue"`, `"microgrid"` |
| `recalibration_reference` | `dict` or `None` | `None` | Pre-computed per-ion-mode reference m/z arrays; computed from the dataset when `None` | `None` or `{MsiIonMode: np.ndarray}` |
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database (columns: `db_name`, `ionized_mass`, `ion_mode`) | File path or `None` |
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist | `True`, `False` |

!!! note "detect_background default"
    In `process_dataset`, `detect_background` defaults to `True`. The value listed above reflects the function signature. Set to `False` if all spots are known to be tissue (e.g. homogeneous cell cultures) or to disable the GMM/Otsu step for speed.

---

## Registration

!!! warning "Only `spot_interpolation` is compatible"
    MSI is a spot-based modality. `feature_extraction` is **not** compatible.

`spot_interpolation` performs Gaussian-weighted interpolation: for each anchor spot (from the reference modality), all MSI spots that fall within the anchor's spatial footprint are averaged with Gaussian weights proportional to distance. The result is a new feature vector at each anchor position.

```yaml
registration_type: spot_interpolation
```

No additional registration settings are required for MSI.

---

## Output

### Per-sample AnnData

Path: `<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.h5ad`

| Slot | Content |
|------|---------|
| `.X` | Interpolated intensity matrix `(N_spots, N_mz)`, sparse or dense `float32`/`float64` |
| `.var_names` | Consensus m/z values (float, formatted as string) |
| `.var['ion_mode']` | Ion mode for each m/z (`"pos"` or `"neg"`) |
| `.var['annotation']` | Lipid annotation string (`;`-separated hits, or `"Unannotated"`) |
| `.obsm['spatial']` | Physical spot coordinates in µm, shape `(N_spots, 2)`, `float32` |
| `.obs['sample_id']` | Sample identifier |
| `.uns['spot_size']` | Raster pixel size `[x_µm, y_µm]` |
| `.uns['foreground_mask']` | Boolean mask identifying tissue spots |

### Merged dataset

Path: `merged/preprocessing/<modality_name>_merged_processed.h5ad`

Contains concatenated data from all samples with the same slot structure.

---

## Config Example

```yaml
modalities:
  - name: lipidomics
    type: msi
    processing_settings:
      lipid_annotation_db: resources/lipid_db.csv
      mass_tolerance: 10
      frequency_threshold: 0.01
      intensity_normalization: tic
      min_intensity_threshold: 10000.0
      detect_background: true
      sample_type: tissue
    registration_type: spot_interpolation
```

=== "Dual ion mode"

    ```yaml
    modalities:
      - name: lipidomics
        type: msi
        processing_settings:
          lipid_annotation_db: resources/lipid_db.csv
          mass_tolerance: 10
          frequency_threshold: 0.01
          intensity_normalization: tic
          min_intensity_threshold: 10000.0
          detect_background: true
          sample_type: tissue
        registration_type: spot_interpolation
    ```

    Set `double_ion_mode: true` in the dataset-level sample configuration and provide `pos/` and `neg/` subdirectories per sample.
