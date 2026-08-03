# Spatial Transcriptomics

## Overview

FOCUS accepts any spatially resolved gene expression dataset as long as it is provided in AnnData `.h5ad` format with raw count data and spatial coordinates. The modality type is agnostic to the capture technology: Visium (10x Genomics), Xenium, MERFISH, Slide-seq, HDST, and any pipeline that outputs an AnnData object with the required fields are all supported.

The preprocessing pipeline validates the input, flags mitochondrial genes, computes QC metrics, optionally filters spots, optionally removes mitochondrial genes, derives per-sample cluster labels, and optionally normalises expression. It then concatenates the samples, optionally applies cross-sample gene filtering, recomputes QC metrics, optionally normalises again, and writes a unified multi-sample AnnData alongside the per-sample files. Every filtering and normalisation step is opt-in: with default settings `.X` is left as the raw counts from the input file.

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
    `spot_size` sets the spatial footprint used by `spot_interpolation` and `spot_aggregation` registration, so it should match your technology (see the table below).
    FOCUS reads it during preprocessing and normalises it: a scalar or 1-element array is broadcast to `[v, v]`, a 2-element array is kept as-is, and more than 2 values raises `ValueError`.
    If your input file is missing `.uns['spot_size']`, FOCUS applies the default `[1.0, 1.0]` µm.

---

## Typical Spot Sizes by Technology

Reference values for common spatial transcriptomics acquisition technologies. These are approximate typical sizes; always verify the exact specifications for your data:

| Technology | Vendor/Platform | Typical Spot Size (µm) | Notes |
|-----------|-----------------|---------------------|-------|
| Visium | 10x Genomics | 55 | Full resolution; ~65 µm sometimes quoted including edge padding |
| Visium HD | 10x Genomics | 2 | High-density capture; 16x higher resolution than standard Visium |
| Xenium | 10x Genomics | 0.2-1 | Single-cell or sub-cellular resolution; exact size depends on clustering |
| MERFISH | Various | 0.05-0.2 | Multiplexed error-robust FISH; subcellular spatial resolution |
| seqFISH | Multiple vendors | 0.05-0.5 | Sequential hybridization; subcellular to near-cellular resolution |
| Slide-seq | Various | 0.5-1 | High-resolution slide-based capture arrays |
| HDST | Harvard | 0.01-0.1 | Highly dense spatial transcriptomics; near-diffraction-limited resolution |

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

### Per-sample steps

Run by `SpatialTranscriptomic.preprocess_data()`. If the sample's output file already exists and `force_recomputing` is `False`, the cached path is returned and none of the steps below run.

1. **Load and validate**: the first `.h5ad` file found in the sample directory is loaded (`os.listdir` order; a directory holding more than one `.h5ad` gives no guarantee about which is used). A missing `.obsm['spatial']` raises `ValueError`; present coordinates are cast to `float32`. The spot size is read from `.uns['spot_size']` and normalised to a `(2,)` `float32` array: a scalar or 1-element array is broadcast to `[v, v]`, a 2-element array is kept, anything longer raises `ValueError`, and a missing value becomes `[1.0, 1.0]`. `.X` is converted to sparse CSR if it is not already, preserving dtype.

2. **Mitochondrial flag**: `.var['mt']` is set by a case-insensitive `MT-`/`MT.` name prefix. Detection is name-based, so datasets keyed by Ensembl IDs are not flagged.

3. **Spot filtering**: the four count/gene thresholds are applied in order (`min_count_per_spot`, `max_count_per_spot`, `min_genes_per_spot`, `max_genes_per_spot`) with `scanpy.pp.filter_cells`. Each is skipped when `None`; a value of `0` or less raises `ValueError`. The number and percentage of retained spots is reported.

4. **QC metrics**: `scanpy.pp.calculate_qc_metrics(qc_vars=['mt'], percent_top=None)` runs on the retained spots, with mitochondrial genes still present. This writes `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt` and their `log1p_` variants to `.obs`, and `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and `log1p_` variants to `.var`.

5. **Mitochondrial gene removal (optional)**: when `remove_mitochondrial_genes=True`, the genes flagged in `.var['mt']` are dropped from the feature set. Because this runs after step 4, the QC metrics in `.obs`, including `pct_counts_mt`, still describe the matrix as it was before removal.

6. **Observation names**: prefixed with `<sample_id>_`, unless every name already carries that prefix.

7. **Cluster labels**: written to `.obs['cluster']` and used to colour spots in the alignment GUI. Computed by `compute_cluster_labels` on `.X` as it stands at this point (post-filter, post-removal, still raw counts). Samples above 100,000 spots are first coarsened: a uniform spatial grid of at most 100,000 cells is laid over `.obsm['spatial']`, and all spots in a cell are summed into one pseudo-spot. The run matrix is then normalised to 10 000 counts and `log1p`-transformed on a throwaway copy, followed by PCA (`n_comps = min(50, n_rows-1, n_genes-1)`) → kNN graph (`n_neighbors = min(15, n_rows-1)`) → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`). Each cell's label propagates back to every spot it contains. Fewer than 2 run rows, fewer than 2 usable PCs, or a single resulting cluster all yield the single label `'0'`. Only `.obs['cluster']` is persisted; the coarsened matrix, PCA embedding and neighbour graph are discarded. PCA and Leiden run with `random_state=0`.

8. **Raw counts layer**: `.layers['raw']` is written with a copy of `.X` **only when** `total_counts_normalize` or `log1p_transform` is enabled. With both off, `.X` already holds the raw counts and no layer is created.

9. **Normalisation (both optional)**: when `total_counts_normalize=True`, `scanpy.pp.normalize_total` scales each spot to 10 000 total counts (`target_sum=1e4`); when `log1p_transform=True`, `scanpy.pp.log1p` is then applied. With both off, `.X` stays as raw counts.

10. **Write**: `.obs['sample_id']` and `.obs['cluster']` are stored as categoricals, `.obsm['spatial']` as `float32`. `.X` and every layer are guaranteed sparse CSR, and the file is written gzip-compressed.

### Dataset-level steps

Run by `SpatialTranscriptomicDataset.process_dataset()` after every sample has been through the steps above. If the merged file exists, `force_recomputing` is `False`, **and** its `.obs['sample_id']` set equals the active sample set, the cached merged file is returned instead.

1. **Collect spot sizes**: read from each per-sample file with a backed read, so `.X` is never materialised.

2. **Concatenate on disk**: `anndata.concat_on_disk` with `axis=0`, `join="outer"`, `fill_value=0`, so genes absent from a sample become zero counts. `.uns` is dropped during the concat and `spot_size` is rebuilt from step 1.

3. **Recover raw counts**: `.X` is replaced by `.layers['raw']` when that layer is present (it is carried over only from samples that were normalised); otherwise `.X` already holds raw counts. Cross-sample filtering and normalisation therefore always operate on raw counts.

4. **Cross-sample gene filtering (optional)**: see [Gene-level filters](#gene-level-filters-dataset-level-only). Skipped entirely when both thresholds are `None`. Gene counts before and after are reported.

5. **Recompute QC metrics**: `.var['mt']` is re-derived and `calculate_qc_metrics` re-runs on the merged raw matrix, so the merged QC reflects the retained spots and genes rather than the per-sample values that predate cross-sample filtering.

6. **Raw counts layer and normalisation**: same rule and same parameters as per-sample steps 8 and 9, applied to the merged matrix.

7. **Write**: `.obs['sample_id']` and `.obs['cluster']` as categoricals (the per-sample cluster labels carried through the concat are kept as-is), `.obsm['spatial']` as `float32`, `.uns['spot_size']` as a `{sample_id: [x, y]}` dictionary, sparse CSR, gzip-compressed.

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
| `min_spots_per_gene` | `float` or `None` | `None` | Minimum fraction of a sample's spots that must express a gene for that sample to count as passing. Must satisfy `0 < value < 1` |
| `min_count_spots_ratio_per_gene` | `float` or `None` | `None` | Minimum ratio of a gene's total counts to the number of spots expressing it, per sample. Must be `> 0` |
| `remove_mitochondrial_genes` | `bool` | `False` | Drop the genes flagged in `.var['mt']`. Applied per sample, before merging |

Both gene filters are evaluated per sample, and a gene is retained when it passes in **at least one** sample. How many samples a gene is detected in does not affect whether it is kept. A gene confined to a single sample survives on the strength of that sample alone. When both thresholds are set, a gene must satisfy each of them in at least one sample, not necessarily the same one.

For the ratio criterion, samples in which a gene is not expressed at all are neither counted as passing nor as failing. There is no highly-variable-gene selection: whatever survives these filters is carried through to registration.

### Normalisation and transform

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `total_counts_normalize` | `bool` | `False` | Normalise each spot to 10 000 total counts |
| `log1p_transform` | `bool` | `False` | Apply log(x + 1) transform after normalisation |

### Other

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist |

!!! note "Where the filters apply"
    The spot-level filters, `remove_mitochondrial_genes` and both normalisation flags are passed to each sample and applied per sample. `min_spots_per_gene` and `min_count_spots_ratio_per_gene` are dataset-level only. They are not parameters of `SpatialTranscriptomic.preprocess_data()` and act on the merged matrix.

    The defaults listed above are the same in the settings extractor (`_extract_st_settings`) and in the `preprocess_data()` / `process_dataset()` signatures, so config runs and direct Python calls agree.

---

## Using ST as Reference vs Non-Reference

### ST as the reference modality

When spatial transcriptomics defines the coordinate system, all other modalities are registered onto ST spots. Set `reference_modality` to the ST modality name in the top-level config. The ST spots become the output grid; every other modality produces a feature vector interpolated to each ST spot location.

```yaml
reference_modality: visium
```

### ST as a non-reference modality

When another spot-based modality (MSI or another ST instance) is the reference, ST is registered onto it using `spot_interpolation`. FOCUS maps reference anchor positions into ST coordinate space and computes Gaussian-weighted averages of ST expression profiles within each anchor footprint.

!!! note "Image-based reference"
    An image-based modality (`microscopy_image`, `raman`) may be set as the reference with ST as a target: preprocessing, alignment and registration all run. The final MuData compilation is skipped, because it requires the reference to be spot-based (`msi` or `st`). Make ST or MSI the reference if you need the compiled multimodal dataset.

---

## Choosing the Reference Modality

| Scenario | Recommended reference |
|----------|-----------------------|
| Spot-based modalities only (ST + MSI) | The spot modality with the lowest spatial resolution (coarser grid); the other registers via `spot_interpolation`. |
| Visium-centric analysis (no high-res image) | Spatial transcriptomics. Other spot modalities register via `spot_interpolation`; use `none` if ST stands alone. |
| Dataset also includes Raman or microscopy | A spot-based modality (ST or MSI). Image-based modalities then register as **targets** (`raman_pixel_interpolation` for Raman; `feature_extraction` for microscopy, but only when it is an H&E brightfield section, otherwise `none`). An image-based reference is accepted and will align and register, but MuData compilation is skipped. |

The reference modality defines the output coordinate space and the number of observation slots in the final multimodal dataset.

---

## Registration

| Registration type | Supported | Notes |
|-------------------|-----------|-------|
| `spot_interpolation` | Yes | ST registers onto a reference via Gaussian-weighted average of the ST spots in each anchor footprint |
| `spot_aggregation` | Yes | ST registers onto a reference by **summing** (not averaging) the ST spots in each anchor footprint, with no normalization. Used for subcellular-resolution data (e.g. Visium HD) |
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
| `.X` | Expression matrix, sparse CSR. Raw counts unless `total_counts_normalize` or `log1p_transform` was enabled |
| `.layers['raw']` | Raw counts before normalisation, sparse CSR. **Present only when `.X` was normalised** |
| `.obs_names` | Spot names, prefixed `<sample_id>_` |
| `.var_names` | Gene names, carried from the input file |
| `.var['mt']` | Boolean mitochondrial flag (all `False` when `remove_mitochondrial_genes` was enabled) |
| `.obsm['spatial']` | Spot coordinates in µm, `float32 (N_spots, 2)` |
| `.obs['sample_id']` | Sample identifier (categorical) |
| `.obs['cluster']` | Per-sample cluster labels (categorical strings) |
| `.obs` QC | `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt`, plus `log1p_total_counts`, `log1p_n_genes_by_counts`, `log1p_total_counts_mt` |
| `.var` QC | `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts`, plus `log1p_mean_counts`, `log1p_total_counts` |
| `.uns['spot_size']` | `float32` array `[x_µm, y_µm]` |

### Merged dataset

Path: `merged/preprocessing/<modality_name>_merged_processed.h5ad`

Same slots as above, with two differences:

- `.uns['spot_size']` is a dictionary `{sample_id: [x_µm, y_µm]}` rather than a single array.
- QC metrics and `.var['mt']` are recomputed on the merged matrix, so they reflect the retained spots and genes after cross-sample gene filtering.

Samples are concatenated with an outer join on genes, and a gene absent from a sample is filled with 0 counts.

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
