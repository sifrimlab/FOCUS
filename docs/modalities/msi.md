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

FOCUS automatically locates the `.imzML` file in each ion mode directory and pairs it with the `.ibd` file of the same base name in the same directory.

---

## Directory Layout

The `.imzML` / `.ibd` files always live in a `pos/` or `neg/` subdirectory of the modality folder, never directly in it — one subdirectory per acquired ion mode.

=== "Single ion mode"

    ```
    dataset_root/
    ├── sample_A/
    │   └── lipidomics/
    │       └── pos/
    │           ├── data.imzML
    │           └── data.ibd
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

FOCUS decides a sample's ion modes from the **files** it finds, not from the directory structure: an ion mode counts as acquired when its subdirectory holds a complete `.imzML` + `.ibd` pair. An ion mode subdirectory that holds neither file is read as "this polarity was not acquired" and is ignored — so the empty `neg/` (or `pos/`) folder that the GUI scaffolds for every MSI sample needs no cleanup. Ion modes can differ from sample to sample within one dataset.

Two cases are errors: a sample with no complete pair in either subdirectory, and a subdirectory holding an incomplete acquisition (an `.imzML` without its `.ibd`, an `.ibd` without its `.imzML`, or files whose base names do not match). Both fail during configuration validation, before any processing starts.

!!! tip "File naming"
    Any base name is accepted, as long as the `.imzML` and `.ibd` share it. If a directory holds several acquisitions, FOCUS uses the alphabetically first `.imzML` that has a matching `.ibd`.

---

## Reserved Directories

Within `<dataset_path>`, certain directory names are reserved and treated specially by FOCUS:

| Directory | Purpose |
|-----------|---------|
| `merged/` | Stores merged outputs from all pipeline stages (preprocessing, alignment, registration, compilation). FOCUS creates and manages this directory automatically. |
| `resources/` | User-controlled directory for storing additional resources and reference files needed for this dataset (e.g., lipid annotation databases, custom scripts, supplementary data). FOCUS ignores this directory when discovering samples. |
| `plots/` | User-controlled directory for storing plots and visualizations generated during downstream analysis. FOCUS ignores this directory when discovering samples. |
| `preprocessing/`, `alignment/`, `registration/`, `annotations/` | Per-stage output directory names. These are the same names used *inside* each sample folder, and they are excluded from sample discovery at the dataset root as well. |

All other directories at `<dataset_path>/` are treated as sample directories, with the directory name becoming the `sample_id` in all outputs. None of the seven reserved names above can be used as a `sample_id`.

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

1. **Parse imzML metadata** — the imzML XML is parsed to extract pixel grid coordinates, physical stage coordinates (µm), raster size, per-spectrum binary offsets, and data types (`float32` or `float64`). Physical coordinates are read from the `3DPositionX`/`3DPositionY` user parameters when present; spots without them fall back to their pixel indices scaled by the raster size, so the result is still in µm. Raster size comes from the `pixel size x`/`pixel size y` scan settings and is parsed as an **integer** number of µm — it falls back to `[1, 1]` when absent or when the stated pixel size is below 1 µm.

2. **Rotation correction** — a linear regression is fit to the stage coordinates of the most densely sampled pixel column, and the resulting slope angle is used to de-rotate all physical coordinates to align the scan with the Cartesian axes.

3. **Dual ion mode alignment** — when both ion modes are present, unpaired spots (acquisition artifacts) are removed by set intersection. An affine transformation is fitted from positive to negative mode physical coordinates; the transformed positive coordinates are averaged with the negative coordinates to yield the consensus spot centre positions.

4. **M/z calibration** — unless a `recalibration_reference` is supplied explicitly, reference peaks are computed from the dataset itself, from every spectrum of every sample. Peaks are grouped within `mass_tolerance` ppm, so the many slightly different measurements of one calibrant collapse into a single candidate reported at the weighted centroid of all of them. Each candidate is scored by *number of occurrences × fraction of samples containing it*, ties broken by ascending m/z. At least five are selected greedily per ion mode in descending score, and more are added if needed so that every sample is covered. When a lipid annotation database is available the candidate pool is restricted to annotation-matched m/z; otherwise all m/z are eligible.

    The selected references are shared by the whole dataset — one set per ion mode. Each sample then computes its own offsets against them: for each reference peak and each spectrum, the most intense peak within `mass_tolerance` that also clears `min_intensity_threshold` gives a local offset, these are averaged per pixel row, and every spectrum's m/z array is shifted by its row's mean offset to correct spatial mass drift. A reference a sample does not contain, and a row with no matches at all, simply drop out of that average.

5. **Consensus m/z grid construction** — all m/z values across all spectra and all samples are pooled, rounded to six decimal places, and clustered using a sliding-window weighted-centroid algorithm within `mass_tolerance` ppm. Clusters present in fewer than `frequency_threshold × max_cluster_count` spectra are discarded. Clustering is parallelised across CPU cores with chunked overlap merging.

6. **Intensity interpolation** — each spectrum is resampled onto the consensus m/z grid using inverse-distance weighting: for each original peak, its intensity is distributed among all reference bins falling within `mass_tolerance` ppm, weighted by 1/(ppm_distance + ε). The result is a dense `(N_spots, N_mz)` matrix.

7. **Tissue / background detection** — when `detect_background=True` **and** a `lipid_annotation_db` is configured, three spectral complexity features are computed per spot: Shannon entropy, peak count, and log(TIC). A 4th feature (annotation DB hit ratio) is added from the database. For `sample_type="tissue"`, a 1-component and a 2-component Gaussian Mixture Model are fit and BIC selects between them: unimodal keeps all spots, bimodal classifies spots with posterior ≥ 0.5 on the higher-mean component as tissue. Morphological hole-filling and binary opening clean up the spatial mask. For `sample_type="microgrid"`, Otsu thresholding with a 25th-percentile floor is used and spatial cleanup is disabled.

    !!! warning "Background detection requires a lipid annotation database"
        Detection is currently gated on `lipid_annotation_db` being set. With `detect_background=True` but no database, the step is silently skipped and every spot is marked foreground (`obs["foreground"]` all `True`). Set both, or expect no background flagging.

8. **Intensity normalisation** — supported methods are `tic` (divide each spectrum by its total ion current, so each spectrum sums to 1), `log` (log(1 + x) transform), `clr` (sparsity-preserving centered log-ratio — log-centers each spectrum over its nonzero entries only, leaving structural zeros at 0), `tic_mean_scaled` (rescale each spectrum to the **mean** total ion current over that sample's spots for that ion mode — like `tic` but preserves an interpretable absolute intensity scale instead of forcing a sum of 1; because the mean is per sample, values are *not* comparable across samples), or `none`. All methods are applied independently per sample and per ion mode. Applied after background detection. The unnormalised interpolated intensities are preserved in `.layers['raw']`.

9. **Per-sample cluster labels** — labels used only to colour spots in the alignment GUI, stored in `.obs['cluster']`. Samples with more than 100,000 spots are first *coarsened*: a uniform spatial grid of at most 100,000 bins is laid over the spots, all spots in a bin are **summed** into one pseudo-spot (so weak per-spot MSI signal accumulates into something clusterable), and the pseudo-spots are re-normalised so bin occupancy washes out. Then PCA (up to 50 components, bounded by the matrix dimensions) → neighbour graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`) runs on that matrix, and each bin's label propagates back to every spot it contains. Smaller samples skip binning and cluster every spot directly. The binned matrix, PCA embedding and neighbour graph are all throwaway — only `.obs['cluster']` is persisted. Samples too small to cluster, or resolving to a single cluster, get the single label `'0'`. PCA and Leiden run with `random_state=0`.

10. **Format as AnnData** — the interpolated matrix, spatial coordinates, and metadata are assembled into an AnnData object and saved as a gzip-compressed `.h5ad` file. Ion mode is encoded in `.var`, and all samples are concatenated into a single merged dataset (inner join on m/z features, gzip-compressed).

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `mass_tolerance` | `int` | `10` | Mass tolerance in ppm for m/z clustering and interpolation | Positive integer |
| `frequency_threshold` | `float` | `0.01` | Minimum relative cluster frequency to retain an m/z in the consensus grid | `0.0` – `1.0` |
| `intensity_normalization` | `str` | `"none"` | Intensity normalisation method (applied per ion mode) | `"none"`, `"tic"`, `"log"`, `"clr"`, `"tic_mean_scaled"` |
| `min_intensity_threshold` | `float` | `10000.0` | Minimum peak intensity considered valid during m/z recalibration | Non-negative float |
| `detect_background` | `bool` | `False` | Detect and flag background spots in the output (`obs["foreground"]`); all spots are still written. **Requires `lipid_annotation_db`** — without it the step is skipped and all spots are marked foreground | `True`, `False` |
| `sample_type` | `str` | `"tissue"` | Tissue architecture type; controls background detection strategy | `"tissue"`, `"microgrid"` |
| `recalibration_reference` | `dict` or `None` | `None` | Pre-computed per-ion-mode reference m/z arrays; computed from the dataset when `None` | `None` or `{MsiIonMode: np.ndarray}` |
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database (columns: `db_name`, `ionized_mass`, `ion_mode`) | File path or `None` |
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist | `True`, `False` |

!!! note "Config defaults and direct API calls agree"
    The defaults above are applied both by the pipeline's settings extractor (`_extract_msi_settings`) and by the `MsiDataset.process_dataset()` signature — there is no divergence between config runs and direct Python calls. Enable `detect_background` (together with a `lipid_annotation_db`) when you want a tissue/background flag; leave it off if all spots are known to be tissue or to skip the GMM/Otsu step for speed.

    Note that `mass_tolerance` must be a Python `int`. Passing a float (e.g. `10.0`) raises `ValueError: mass_tolerance must be a positive integer representing ppm.`

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
| `.var['lipid_annotation']` | Lipid annotation string — `"; "`-separated `db_name` hits, or `"Unannotated"` (categorical) |
| `.obsm['spatial']` | Physical spot coordinates in µm, shape `(N_spots, 2)`, `float32` |
| `.obsm['raster_coordinates']` | Raster cell bounding-box corners in µm, shape `(N_spots, 2, 2)` as `[[x1, y1], [x2, y2]]` |
| `.obs['sample_id']` | Sample identifier (categorical) |
| `.obs['foreground']` | Categorical boolean mask identifying tissue (`True`) vs. background (`False`) spots. **Always present**; all `True` when background detection did not run |
| `.obs['cluster']` | Per-sample cluster labels for alignment colouring (categorical strings) |
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

    The processing settings are identical — there is no ion mode switch in the configuration. Simply provide a complete `.imzML` + `.ibd` pair in both the `pos/` and `neg/` subdirectory of each sample; FOCUS detects the dual-mode layout from those files.
