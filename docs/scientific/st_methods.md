# Spatial Transcriptomics Preprocessing Methods

## 1. Objective

The ST preprocessing module transforms per-sample spatial transcriptomics AnnData files into quality-controlled, merge-ready datasets while preserving spatial coordinates and raw-count provenance.

For a sample with \(N\) observations (spots/cells) and \(G\) genes, the core object is:

\[
X \in \mathbb{R}_{\ge 0}^{N\times G}
\]

with required spatial coordinates:

\[
\texttt{obsm['spatial']} \in \mathbb{R}^{N\times 2}
\]

---

## 2. Input contract and normalization of geometry metadata

Implementation (`SpatialTranscriptomic.load_data`) expects one `.h5ad` per sample directory and enforces:

- presence of `.obsm['spatial']`
- conversion of spatial coordinates to `float32`
- sparse CSR storage for `.X`

Spot footprint metadata is normalized by `_normalize_spot_size`:

- missing -> `[1.0, 1.0]`
- scalar \(a\) -> `[a, a]`
- length-1 array -> isotropic `[a, a]`
- length-2 array -> `[a_x, a_y]`

Stored as `float32` in `.uns['spot_size']`.

---

## 3. Per-sample preprocessing pipeline

`SpatialTranscriptomic.preprocess_data` executes the following sequence.

### 3.1 QC metric computation

Mitochondrial genes are flagged by uppercase prefix `MT-`.

`scanpy.pp.calculate_qc_metrics(..., qc_vars=['mt'])` is applied, adding standard QC summaries in `.obs` (for example `n_genes_by_counts`, `total_counts`, `pct_counts_mt`, with Scanpy-version-dependent extras).

### 3.2 Spot-level filtering (optional)

Filters are applied via `scanpy.pp.filter_cells` when configured:

- `min_count_per_spot`
- `max_count_per_spot`
- `min_genes_per_spot`
- `max_genes_per_spot`

Invalid non-positive thresholds are rejected.

### 3.3 Observation index standardization

Observation names are prefixed with `<sample_id>_` when needed to guarantee uniqueness after merge.

### 3.4 Raw-count preservation

Filtered, pre-normalization counts are copied to:

```python
layers['raw']
```

### 3.5 Optional normalization and transform

- total-count normalization (`scanpy.pp.normalize_total`, target sum \(10^4\)) when `total_counts_normalize=true`
- `scanpy.pp.log1p` when `log1p_transform=true`

### 3.6 Per-sample clustering

Leiden clustering is run only when the sample has sufficient dimensionality:

\[
n_{pcs} = \min(50, N-1, G-1)
\]

- if \(N \ge 2\) and \(n_{pcs} \ge 2\): PCA -> neighbors -> Leiden (`resolution=0.5`, `flavor='igraph'`)
- otherwise: all observations get cluster label `'0'`

### 3.7 Output metadata and persistence

Per-sample output sets:

- `.obs['sample_id']` (categorical)
- `.obs['leiden']` (categorical)
- `.obsm['spatial']` as `float32`

Saved as gzip-compressed `.h5ad`.

---

## 4. Cross-sample merged processing

`SpatialTranscriptomicDataset.process_dataset` performs sample-level preprocessing first, then merged processing.

### 4.1 Merge construction

For each sample file:

1. reload `.h5ad`
2. restore `.X <- .layers['raw']`
3. remove temporary raw layer before concatenation

All samples are concatenated with outer gene union:

```python
ad.concat(..., join='outer', fill_value=0)
```

Then `.X` is forced to sparse CSR.

### 4.2 Cross-sample gene filtering

Let \(S\) be number of samples and \(\tau = 0.05\) (`_NUM_SAMPLES_FILTER`).

A gene is retained if it passes criterion in at least

\[
\left\lceil \tau S \right\rceil
\]

samples.

#### A) Expression-frequency filter (`min_spots_per_gene = \theta`)

Within each sample \(s\) with \(N_s\) observations, gene \(g\) passes if:

\[
\#\{i: X_{ig}>0\} \ge \max\left(1,\left\lceil \theta N_s \right\rceil\right)
\]

Constraint enforced in code: \(0 < \theta < 1\).

#### B) Count-per-expressed-spot ratio (`min_count_spots_ratio_per_gene = \rho`)

For each sample, define:

- expressed count: \(E_{s,g}=\#\{i: X_{ig}>0\}\)
- total count: \(C_{s,g}=\sum_i X_{ig}\)

If \(E_{s,g}>0\), gene passes sample \(s\) if:

\[
\frac{C_{s,g}}{E_{s,g}} \ge \rho
\]

Unexpressed genes in a sample are neutral (neither pass nor fail that sample).

### 4.3 Merged normalization and metadata

After gene filtering:

- post-filter raw counts are stored in `combined.layers['raw']`
- optional `normalize_total` and `log1p` are applied to merged `.X`
- `.obs['sample_id']` and `.obs['leiden']` are categorical
- `.obsm['spatial']` is `float32`
- `.uns['spot_size']` becomes a per-sample dictionary `{sample_id: [sx, sy]}`

Saved as gzip-compressed merged `.h5ad`.

---

## 5. Output files

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality_name}/{modality_name}_{sample_id}_processed.h5ad
```

Merged:

```text
{dataset_path}/merged/preprocessing/{modality_name}_merged_processed.h5ad
```

---

## 6. Parameters and effective defaults

Config-extracted defaults for ST preprocessing (`_extract_st_settings`):

- `min_count_per_spot`: `null`
- `max_count_per_spot`: `null`
- `min_genes_per_spot`: `null`
- `max_genes_per_spot`: `null`
- `min_spots_per_gene`: `null`
- `min_count_spots_ratio_per_gene`: `null`
- `total_counts_normalize`: `false`
- `log1p_transform`: `false`
- `force_recomputing`: `false`

Note: method signatures in lower-level preprocessing functions define `total_counts_normalize=True` and `log1p_transform=True`, but in normal pipeline execution these are overridden by the extracted config defaults above unless explicitly set.
