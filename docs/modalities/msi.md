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

## Reserved Directories

Within `<dataset_path>`, certain directory names are reserved and treated specially by FOCUS:

| Directory | Purpose |
|-----------|---------|
| `merged/` | Stores merged outputs from all pipeline stages (preprocessing, alignment, registration, compilation). FOCUS creates and manages this directory automatically. |
| `resources/` | User-controlled directory for storing additional resources and reference files needed for this dataset (e.g., lipid annotation databases, custom scripts, supplementary data). FOCUS ignores this directory when discovering samples. |
| `plots/` | User-controlled directory for storing plots and visualizations generated during downstream analysis. FOCUS ignores this directory when discovering samples. |

All other directories at `<dataset_path>/` are treated as sample directories, with the directory name becoming the `sample_id` in all outputs.

---

## Lipid Annotation Database

### Format

The lipid annotation database is a **tabular** file — CSV or JSON — with one row per ionized lipid species. It must contain the following three columns:

| Column | Type | Description |
|--------|------|-------------|
| `db_name` | string | Lipid name or identifier |
| `ionized_mass` | float | Theoretical ionized (m/z) mass of the species |
| `ion_mode` | string | Ion mode of the species: `pos` or `neg` |

CSV example:

```csv
db_name,ionized_mass,ion_mode
Phosphatidylcholine(32:0),734.569,pos
Phosphatidylcholine(34:1),760.584,pos
Sphingomyelin(d18:1/16:0),703.598,pos
Phosphatidylethanolamine(36:2),764.536,neg
Phosphatidylserine(38:4),834.525,neg
```

JSON equivalent (a list of records, or any orientation `pandas.read_json` accepts):

```json
[
  {"db_name": "Phosphatidylcholine(32:0)", "ionized_mass": 734.569, "ion_mode": "pos"},
  {"db_name": "Phosphatidylcholine(34:1)", "ionized_mass": 760.584, "ion_mode": "pos"},
  {"db_name": "Phosphatidylethanolamine(36:2)", "ionized_mass": 764.536, "ion_mode": "neg"}
]
```

### Location

The annotation database can be stored:

1. **In the dataset's `resources/` folder** (recommended for convenience):
   ```
   dataset_root/
   ├── resources/
   │   └── lipid_annotation_db.json
   ├── sample_A/
   │   └── lipidomics/
   │       ├── pos/
   │       │   ├── data.imzML
   │       │   └── data.ibd
   │       └── neg/
   │           ├── data.imzML
   │           └── data.ibd
   ```

2. **At any system path**: Configure the full path in the `lipid_annotation_db` field of your FOCUS config.

### Configuration

In your FOCUS configuration file, specify the database location via the `lipid_annotation_db` parameter:

```yaml
modalities:
  - name: lipidomics
    type: msi
    processing_settings:
      lipid_annotation_db: resources/lipid_annotation_db.json  # Path relative to dataset_root
      # ... other settings
```

If the database is omitted or set to `None`, FOCUS will process the data without annotation.

---

## Preprocessing Steps

1. **Parse imzML metadata** — the imzML XML is parsed to extract pixel grid coordinates, physical stage coordinates (µm), raster size, per-spectrum binary offsets, and data types (`float32` or `float64`). Physical coordinates are read from `3DPositionX/Y` user parameters when present; pixel indices are used as fallback.

2. **Rotation correction** — a linear regression is fit to the stage coordinates of the most densely sampled pixel column, and the resulting slope angle is used to de-rotate all physical coordinates to align the scan with the Cartesian axes.

3. **Dual ion mode alignment** — when both ion modes are present, unpaired spots (acquisition artifacts) are removed by set intersection. An affine transformation is fitted from positive to negative mode physical coordinates; the transformed positive coordinates are averaged with the negative coordinates to yield the consensus spot centre positions.

4. **M/z calibration** — if a lipid annotation database is provided, up to five high-confidence reference peaks (selected by cross-sample frequency and intensity) are identified per ion mode and used to compute per-row m/z offsets. Each spectrum's m/z array is shifted by its row-wise mean offset to correct for spatial mass drift.

5. **Consensus m/z grid construction** — all m/z values across all spectra and all samples are pooled, rounded to six decimal places, and clustered using a sliding-window weighted-centroid algorithm within `mass_tolerance` ppm. Clusters present in fewer than `frequency_threshold × max_cluster_count` spectra are discarded. Clustering is parallelised across CPU cores with chunked overlap merging.

6. **Intensity interpolation** — each spectrum is resampled onto the consensus m/z grid using inverse-distance weighting: for each original peak, its intensity is distributed among all reference bins falling within `mass_tolerance` ppm, weighted by 1/(ppm_distance + ε). The result is a dense `(N_spots, N_mz)` matrix.

7. **Tissue / background detection** — when `detect_background=True`, three spectral complexity features are computed per spot: Shannon entropy, peak count, and log(TIC). An optional 4th feature (annotation DB hit ratio) is added when a lipid database is provided. For `sample_type="tissue"`, a 2-component Gaussian Mixture Model is fit; BIC determines whether the distribution is unimodal (all spots kept) or bimodal (posterior ≥ 0.5 classifies tissue). Morphological hole-filling and binary opening clean up the spatial mask. For `sample_type="microgrid"`, Otsu thresholding with a 25th-percentile floor is used and spatial cleanup is disabled.

8. **Intensity normalisation** — supported methods are `tic` (divide each spectrum by its total ion current, so each spectrum sums to 1), `log` (log(1 + x) transform), `clr` (sparsity-preserving centered log-ratio — log-centers each spectrum over its nonzero entries only, leaving structural zeros at 0), `global_scaling` (rescale each spectrum to the **mean** total ion current of its ion mode — like `tic` but preserves an interpretable absolute intensity scale instead of forcing a sum of 1), or `none`. All methods are applied independently per ion mode. Applied after background detection. The unnormalised interpolated intensities are preserved in `.layers['raw']`.

9. **Per-sample Leiden clustering** — PCA (up to 50 components) → neighbor graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`) is computed on the normalised matrix and stored in `.obs['leiden']`. The PCA embedding and neighbor graph are then discarded (only `.obs['leiden']` is kept) to minimise file size.

10. **Format as AnnData** — the interpolated matrix, spatial coordinates, and metadata are assembled into an AnnData object and saved as a gzip-compressed `.h5ad` file. Ion mode is encoded in `.var`, and all samples are concatenated into a single merged dataset (inner join on m/z features, gzip-compressed).

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `mass_tolerance` | `int` | `10` | Mass tolerance in ppm for m/z clustering and interpolation | Positive integer |
| `frequency_threshold` | `float` | `0.01` | Minimum relative cluster frequency to retain an m/z in the consensus grid | `0.0` – `1.0` |
| `intensity_normalization` | `str` | `"none"` | Intensity normalisation method (applied per ion mode) | `"none"`, `"tic"`, `"log"`, `"clr"`, `"global_scaling"` |
| `min_intensity_threshold` | `float` | `10000.0` | Minimum peak intensity considered valid during m/z recalibration | Non-negative float |
| `detect_background` | `bool` | `False` | Detect and flag background spots in the output (`obs["foreground"]`); all spots are still written | `True`, `False` |
| `sample_type` | `str` | `"tissue"` | Tissue architecture type; controls background detection strategy | `"tissue"`, `"microgrid"` |
| `recalibration_reference` | `dict` or `None` | `None` | Pre-computed per-ion-mode reference m/z arrays; computed from the dataset when `None` | `None` or `{MsiIonMode: np.ndarray}` |
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database (columns: `db_name`, `ionized_mass`, `ion_mode`) | File path or `None` |
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist | `True`, `False` |

!!! note "Config defaults vs. direct API calls"
    The defaults above are the values applied when running through the configuration file (the pipeline's settings extractor). When calling `MsiDataset.process_dataset()` directly in Python, two signature defaults differ: `intensity_normalization` defaults to `"tic"` and `detect_background` defaults to `True`. Enable `detect_background` when you want a tissue/background flag; disable it (the config default) if all spots are known to be tissue or to skip the GMM/Otsu step for speed.

---

## Registration

!!! warning "Spot-based registration only"
    MSI is a spot-based modality, so it supports `spot_interpolation` and `spot_aggregation`. `feature_extraction` is **not** compatible.

`spot_interpolation` performs Gaussian-weighted interpolation: for each anchor spot (from the reference modality), all MSI spots that fall within the anchor's spatial footprint are averaged with Gaussian weights proportional to distance. The result is a new feature vector at each anchor position.

```yaml
registration_type: spot_interpolation
```

`spot_aggregation` uses the same footprint but **sums** the MSI spots inside it instead of averaging them — equal weight, no Gaussian kernel, and no normalization. This accumulates signal under each anchor spot rather than diluting it, which is intended for subcellular-resolution data. See [Registration](../pipeline/registration.md#spot_aggregation).

```yaml
registration_type: spot_aggregation
```

No additional registration settings are required for MSI.

---

## Output

### Per-sample AnnData

Path: `<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.h5ad`

| Slot | Content |
|------|---------|
| `.X` | Normalised interpolated intensity matrix `(N_spots, N_mz)`, sparse CSR `float32` |
| `.layers['raw']` | Interpolated intensities before normalisation, sparse CSR `float32` |
| `.var['mz']` | Consensus m/z values (float32) |
| `.var['mz_mode']` | Ion mode for each m/z (`"pos"` or `"neg"`) |
| `.var['lipid_annotation']` | Lipid annotation string (`;`-separated hits, or `"Unannotated"`) |
| `.obsm['spatial']` | Physical spot coordinates in µm, shape `(N_spots, 2)`, `float32` |
| `.obsm['raster_coordinates']` | Raster cell bounding-box corners in µm, shape `(N_spots, 2, 2)` as `[[x1, y1], [x2, y2]]` |
| `.obs['sample_id']` | Sample identifier |
| `.obs['foreground']` | Boolean mask identifying tissue (`True`) vs. background (`False`) spots (present when `detect_background` is enabled) |
| `.obs['leiden']` | Per-sample Leiden cluster labels |
| `.uns['spot_size']` | Raster pixel size `[x_µm, y_µm]`. **Automatically read from `.imzML` metadata.** If metadata is unavailable, defaults to `[1.0, 1.0]` µm. In dual ion mode the size is taken from the positive mode (both modes are expected to share the same raster size). In the merged file this becomes a dict keyed by `sample_id`. |

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
