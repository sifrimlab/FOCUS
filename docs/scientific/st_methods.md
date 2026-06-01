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

### 3.1 Mitochondrial gene flag

Mitochondrial genes are flagged in `.var['mt']` by a case-insensitive `MT-`/`MT.`
name prefix. Detection is name-based, so datasets keyed by Ensembl identifiers will
not be flagged.

### 3.2 Spot-level filtering (optional)

Filters are applied via `scanpy.pp.filter_cells` when configured:

- `min_count_per_spot`
- `max_count_per_spot`
- `min_genes_per_spot`
- `max_genes_per_spot`

Invalid non-positive thresholds are rejected. All thresholds default to `null` (off).

### 3.3 QC metric computation

`scanpy.pp.calculate_qc_metrics(..., qc_vars=['mt'], percent_top=None)` is applied on
the retained spots, **with mitochondrial genes still present** so that `pct_counts_mt`
is meaningful. It adds per-spot QC summaries to `.obs` (`n_genes_by_counts`,
`total_counts`, `pct_counts_mt`, ...) and per-gene summaries to `.var`
(`n_cells_by_counts`, ...). These metrics are persisted as inspectable metadata and
drive the optional mitochondrial gene filter below. `percent_top=None` skips the
`pct_counts_in_top_*` columns to keep the output lean.

### 3.4 Mitochondrial gene removal (optional, QC-driven)

When `remove_mitochondrial_genes=true`, genes flagged in `.var['mt']` are dropped from
the feature set. This is **off by default**: in spatial data a high mitochondrial
fraction is frequently a genuine biological signal (e.g. metabolically active regions)
rather than a low-quality artefact, so mitochondrial content is never discarded unless
explicitly requested.

### 3.5 Observation index standardization

Observation names are prefixed with `<sample_id>_` when needed to guarantee uniqueness
after merge.

### 3.6 Raw-count preservation

Filtered, post-feature-selection counts are copied to `layers['raw']` (the FOCUS
cross-modality convention for unnormalized counts) before any normalization.

### 3.7 Optional normalization and transform

Both steps are opt-in and leave `.X` as raw counts by default:

- total-count normalization (`scanpy.pp.normalize_total`, target sum \(10^4\)) when `total_counts_normalize=true`
- `scanpy.pp.log1p` when `log1p_transform=true`

Because the downstream alignment and registration stages consume the preprocessing
output **as-is** (no further normalization), whatever ends up in `.X` is the final
analysis matrix.

### 3.8 Per-sample clustering

Leiden labels (`.obs['leiden']`) are used only to colour spots during the interactive
alignment stage. Clustering is computed on a throwaway, internally normalized +
`log1p` representation of the raw counts, so the labels are meaningful even when the
output `.X` is left as raw counts. Only the labels are kept — the PCA embedding and
neighbour graph are **not** persisted, keeping the saved `.h5ad` small.

\[
n_{pcs} = \min(50, N-1, G-1)
\]

- if \(N \ge 2\) and \(n_{pcs} \ge 2\): normalize+log1p (internal copy) -> PCA -> neighbors -> Leiden (`resolution=0.5`, `flavor='igraph'`, `n_iterations=2`, `directed=False`)
- otherwise: all observations get cluster label `'0'`

### 3.9 Output metadata and persistence

Per-sample output sets:

- `.obs['sample_id']` (categorical)
- `.obs['leiden']` (categorical)
- per-spot/per-gene QC metrics from §3.3
- `.obsm['spatial']` as `float32`
- `.layers['raw']` (unnormalized counts)

Saved as gzip-compressed `.h5ad`. PCA/neighbour-graph intermediates are not written.

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

- QC metrics are **recomputed** on the merged matrix (`calculate_qc_metrics`, `percent_top=None`) so `.obs`/`.var` QC reflect the retained spots and genes (per-sample QC predates cross-sample filtering)
- post-filter raw counts are stored in `combined.layers['raw']`
- optional `normalize_total` and `log1p` are applied to merged `.X`
- `.obs['sample_id']` and `.obs['leiden']` are categorical
- `.obsm['spatial']` is `float32`
- `.uns['spot_size']` becomes a per-sample dictionary `{sample_id: [sx, sy]}`

Saved as gzip-compressed merged `.h5ad`.

Note: the gene-selection design deliberately retains every gene with sufficient signal
in at least one sample (to preserve rare cell-type markers). FOCUS does **not** subset
to highly variable genes — the full filtered panel is carried through to registration.

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
- `remove_mitochondrial_genes`: `false`
- `total_counts_normalize`: `false`
- `log1p_transform`: `false`
- `force_recomputing`: `false`

All filtering and normalization steps are opt-in; no step is forced on by default.
