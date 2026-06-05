# Spatial Transcriptomics

## Overview

FOCUS accepts any spatially resolved gene expression dataset as long as it is provided in AnnData `.h5ad` format with raw count data and spatial coordinates. The modality type is agnostic to the capture technology: Visium (10x Genomics), Xenium, MERFISH, Slide-seq, HDST, and any pipeline that outputs an AnnData object with the required fields are all supported.

The preprocessing pipeline validates the input, computes QC metrics, optionally filters spots and genes, normalises expression, and performs per-sample Leiden clustering. When multiple samples are processed together, the pipeline concatenates them, applies cross-sample gene filtering, re-normalises the merged dataset, and writes a unified multi-sample AnnData.

---

## Input Requirements

The input `.h5ad` file must satisfy the following:

| Field | Required | Description |
|-------|----------|-------------|
| `.X` | Yes | Gene expression matrix (sparse CSR recommended; raw integer counts preferred) |
| `.obsm['spatial']` | Yes | Spot coordinates in µm, shape `(N_spots, 2)` |
| `.var` | Yes | Gene metadata; gene names must be the index |
| `.uns['spot_size']` | No | Spot diameter `[x, y]` in µm. **FOCUS reads this field automatically.** If missing, defaults to `[1.0, 1.0]` µm. |

!!! tip "spot_size"
    `spot_size` controls the spatial footprint used during `spot_interpolation` registration.
    For Visium full-resolution spots the diameter is approximately 65 µm; set this correctly in your input AnnData to ensure accurate neighbourhood interpolation.
    FOCUS automatically extracts this value during preprocessing and normalises it: a scalar is broadcast to `[val, val]`, and a 1-element array is treated as isotropic.
    If your input file is missing `.uns['spot_size']`, FOCUS applies the default `[1.0, 1.0]` µm.

---

## Typical Spot Sizes by Technology

Reference values for common spatial transcriptomics acquisition technologies. These are approximate typical sizes; always verify the exact specifications for your data:

| Technology | Vendor/Platform | Typical Spot Size (µm) | Notes |
|-----------|-----------------|---------------------|-------|
| Visium | 10x Genomics | 55 | Full resolution; ~65 µm sometimes quoted including edge padding |
| Visium HD | 10x Genomics | 2 | High-density capture; 16x higher resolution than standard Visium |
| Xenium | 10x Genomics | 0.2–1 | Single-cell or sub-cellular resolution; exact size depends on clustering |
| MERFISH | Various | 0.05–0.2 | Multiplexed error-robust FISH; subcellular spatial resolution |
| seqFISH | Multiple vendors | 0.05–0.5 | Sequential hybridization; subcellular to near-cellular resolution |
| Slide-seq | Various | 0.5–1 | High-resolution slide-based capture arrays |
| HDST | Harvard | 0.01–0.1 | Highly dense spatial transcriptomics; near-diffraction-limited resolution |

Ensure `.uns['spot_size']` in your input AnnData matches your technology's specifications for accurate spatial integration with FOCUS.

---

## Directory Layout

Place one `.h5ad` file per sample inside `<sample_id>/<modality_name>/`:

```
dataset_root/
├── sample_A/
│   └── visium/
│       └── sample_A.h5ad
├── sample_B/
│   └── visium/
│       └── sample_B.h5ad
```

---

## Preprocessing Steps

1. **Load and validate** — the first `.h5ad` file in the sample directory is loaded. FOCUS asserts that `.obsm['spatial']` exists and casts it to `float32`. The spot size is read from `.uns['spot_size']` and normalised to a `(2,)` `float32` array; missing values default to `[1.0, 1.0]`. The expression matrix `.X` is converted to sparse CSR format if it is not already sparse.

2. **Mitochondrial flag** — mitochondrial genes are flagged in `.var['mt']` by a case-insensitive `MT-`/`MT.` name prefix (datasets keyed by Ensembl IDs are not flagged).

3. **Spot filtering** — spots failing any of the four optional count/gene thresholds are removed with `scanpy.pp.filter_cells`. All thresholds default to `None` (disabled).

4. **QC metric computation** — `scanpy.pp.calculate_qc_metrics(qc_vars=['mt'], percent_top=None)` runs on the retained spots, with mitochondrial genes still present so `pct_counts_mt` is meaningful. Per-spot (`n_genes_by_counts`, `total_counts`, `pct_counts_mt`, ...) and per-gene (`n_cells_by_counts`, ...) metrics are stored as inspectable metadata.

5. **Mitochondrial gene removal (optional)** — when `remove_mitochondrial_genes=True`, the genes flagged in `.var['mt']` are dropped. Off by default: in spatial data a high mitochondrial fraction is often biological rather than a low-quality artefact. Observation names are then prefixed with `<sample_id>_` to ensure uniqueness across samples.

6. **Raw counts preservation** — filtered, post-feature-selection raw counts are stored in `.layers['raw']` before any normalisation (FOCUS cross-modality convention).

7. **Total counts normalisation (optional)** — when `total_counts_normalize=True`, `scanpy.pp.normalize_total` scales each spot to a library size of 10 000 counts (`target_sum=1e4`). Off by default — `.X` stays raw unless requested.

8. **Log1p transform (optional)** — when `log1p_transform=True`, `scanpy.pp.log1p` is applied after normalisation.

9. **Leiden clustering** — labels are used only to colour spots during alignment. Clustering runs on a throwaway, internally normalised + log1p copy of the counts (so labels are meaningful regardless of the output `.X` normalisation): PCA (up to 50 components, limited by `min(n_obs-1, n_vars-1)`) → kNN graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`). Only `.obs['leiden']` is kept — PCA/neighbour intermediates are **not** persisted, keeping the file small. Samples with fewer than two spots (or too few PCs) receive a single label `'0'`. Per-sample Leiden is computed independently to avoid batch effects in visualisation.

---

## Processing Parameters

### Spot-level filters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `min_count_per_spot` | `int` or `None` | `None` | Minimum total UMI counts per spot |
| `max_count_per_spot` | `int` or `None` | `None` | Maximum total UMI counts per spot |
| `min_genes_per_spot` | `int` or `None` | `None` | Minimum number of genes detected per spot |
| `max_genes_per_spot` | `int` or `None` | `None` | Maximum number of genes detected per spot |

### Gene-level filters (dataset-level only)

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `min_spots_per_gene` | `float` or `None` | `None` | Minimum fraction of spots per sample expressing a gene for it to be retained; must be in `(0, 1)` |
| `min_count_spots_ratio_per_gene` | `float` or `None` | `None` | Minimum ratio of total counts to expressed spots per gene; unexpressed genes are skipped |
| `remove_mitochondrial_genes` | `bool` | `False` | Opt-in. Drop mitochondrial genes (`MT-`/`MT.` prefix) from the feature set. Applied per sample. |

> FOCUS deliberately preserves every gene with sufficient signal in at least one sample (to keep rare cell-type markers) and does **not** subset to highly variable genes — the full filtered panel is carried through to registration.

### Normalisation and transform

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `total_counts_normalize` | `bool` | `False` | Normalise each spot to 10 000 total counts |
| `log1p_transform` | `bool` | `False` | Apply log(x + 1) transform after normalisation |

### Other

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist |

!!! note "Defaults for normalisation"
    The defaults above (`False`/`False`) are the values applied when running through the configuration file (the `_extract_st_settings` settings extractor). When calling `SpatialTranscriptomic.preprocess_data()` / `SpatialTranscriptomicDataset.process_dataset()` directly in Python, both `total_counts_normalize` and `log1p_transform` default to `True`. Enable both for standard scanpy-style normalisation before dimensionality reduction.

---

## Using ST as Reference vs Non-Reference

### ST as the reference modality

When spatial transcriptomics defines the coordinate system, all other modalities are registered onto ST spots. Set `reference_modality` to the ST modality name in the top-level config. The ST spots become the output grid; every other modality produces a feature vector interpolated to each ST spot location.

```yaml
reference_modality: visium
```

### ST as a non-reference modality

When another spot-based modality (MSI or another ST instance) is the reference, ST is registered onto it using `spot_interpolation`. FOCUS maps reference anchor positions into ST coordinate space and computes Gaussian-weighted averages of ST expression profiles within each anchor footprint. **Note**: FOCUS does not support image-based modalities (microscopy, Raman) as reference when ST is a target. In such cases, ST must be the reference modality.

---

## Choosing the Reference Modality

| Scenario | Recommended reference |
|----------|-----------------------|
| Spot-based modalities only (ST + MSI) | The spot modality with the lowest spatial resolution (coarser grid); the other registers via `spot_interpolation`. |
| Visium-centric analysis (no high-res image) | Spatial transcriptomics. Other spot modalities register via `spot_interpolation`; use `none` if ST stands alone. |
| Dataset also includes Raman or microscopy | A spot-based modality (ST or MSI) must be the reference. Image-based modalities register as **targets** (`raman_pixel_interpolation` for Raman, `feature_extraction` for microscopy). An image-based modality cannot be the reference when a spot-based modality is present, because a mixed image-reference / spot-target pipeline is not supported. |

The reference modality defines the output coordinate space and the number of observation slots in the final multimodal dataset.

---

## Registration

| Registration type | Supported | Notes |
|-------------------|-----------|-------|
| `spot_interpolation` | Yes | ST registers onto a reference via Gaussian-weighted average of the ST spots in each anchor footprint |
| `spot_aggregation` | Yes | ST registers onto a reference by **summing** (not averaging) the ST spots in each anchor footprint, with no normalization — for subcellular-resolution data (e.g. Visium HD) |
| `none` | Yes | No registration; ST coordinates are used as-is (valid when ST is the reference) |
| `feature_extraction` | No | Not compatible with spot-based modalities |

```yaml
# ST as non-reference, registering onto another spot modality (MSI)
registration_type: spot_interpolation

# ST as reference (no registration needed)
registration_type: none
```

---

## Output

### Per-sample AnnData

Path: `<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.h5ad`

| Slot | Content |
|------|---------|
| `.X` | Normalised (and log1p if requested) expression matrix, sparse CSR `float32` |
| `.layers['raw']` | Filtered raw counts before normalisation, sparse CSR |
| `.var_names` | Gene names |
| `.obsm['spatial']` | Spot coordinates in µm, `float32 (N_spots, 2)` |
| `.obs['sample_id']` | Sample identifier (categorical) |
| `.obs['leiden']` | Per-sample Leiden cluster labels (categorical) |
| `.obs['n_counts']` | Total UMI counts per spot |
| `.obs['n_genes_by_counts']` | Number of genes detected per spot |
| `.obs['pct_counts_mt']` | Percentage of counts from mitochondrial genes |
| `.uns['spot_size']` | `float32 [x_µm, y_µm]` |

### Merged dataset

Path: `merged/preprocessing/<modality_name>_merged_processed.h5ad`

Contains all samples concatenated via an outer join on genes (missing genes filled with 0). `.uns['spot_size']` is a dictionary `{sample_id: [x_µm, y_µm]}`.

---

## Config Example

=== "ST as reference"

    ```yaml
    reference_modality: visium

    modalities:
      - name: visium
        type: st
        processing_settings:
          min_count_per_spot: 500
          min_genes_per_spot: 200
          min_spots_per_gene: 0.05
          total_counts_normalize: true
          log1p_transform: true
        registration_type: none
    ```

=== "ST as non-reference"

    ```yaml
    reference_modality: lipidomics

    modalities:
      - name: lipidomics
        type: msi
        processing_settings:
          mass_tolerance: 10
          intensity_normalization: tic
        registration_type: none
        
      - name: visium
        type: st
        processing_settings:
          min_count_per_spot: 500
          min_genes_per_spot: 200
          min_spots_per_gene: 0.05
          total_counts_normalize: true
          log1p_transform: true
        registration_type: spot_interpolation
    ```
