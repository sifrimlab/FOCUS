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
the retained spots, **with mitochondrial genes still present**. It adds per-spot QC
summaries to `.obs` (`total_counts`, `n_genes_by_counts`, `total_counts_mt`,
`pct_counts_mt`, and their `log1p_` variants) and per-gene summaries to `.var`
(`n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts`, and their
`log1p_` variants). `percent_top=None` omits the `pct_counts_in_top_*` columns.

### 3.4 Mitochondrial gene removal (optional)

When `remove_mitochondrial_genes=true`, the genes flagged in `.var['mt']` (§3.1) are
dropped from the feature set. Because this step follows §3.3, the QC metrics already
written to `.obs` — `pct_counts_mt` included — describe the matrix as it stood before
removal, and they are not recomputed here.

### 3.5 Observation index standardization

Observation names are prefixed with `<sample_id>_`, unless every name already carries
that prefix.

### 3.6 Per-sample clustering

Cluster labels (`.obs['cluster']`) are consumed only by the alignment GUI for spot
colouring. They are computed on `.X` at this point in the sequence — post-filter,
post-removal, and still raw counts, since §3.7 has not yet run.

Let \(N\) be the spot count, \(G\) the gene count and \(C = 100{,}000\) the row cap.

1. **Coarsening.** If \(N > C\), a uniform spatial grid of at most \(C\) cells is laid over
   `.obsm['spatial']` and all spots in a cell are summed into one pseudo-spot, giving a run
   matrix of \(N_r \le C\) rows. Otherwise \(N_r = N\) and every spot is used directly.
2. **Internal normalization.** The run matrix is total-count normalized to \(10^4\) and
   `log1p`-transformed on a throwaway copy.
3. **Partition.** With

   \[
   n_{pcs} = \min(50,\, N_r-1,\, G-1),
   \qquad
   n_{neighbors} = \min(15,\, N_r-1),
   \]

   PCA \(\to\) kNN \(\to\) Leiden (`resolution=0.5`, `flavor='igraph'`, `n_iterations=2`,
   `directed=False`) runs on that matrix. Each cell's label propagates back to every spot
   that contributed to it.
4. **Fallback.** If \(N_r < 2\), \(n_{pcs} < 2\), or the partition resolves to a single
   cluster, every spot receives the label `'0'`.

Only the label array is persisted; the coarsened matrix, PCA embedding and neighbour graph
are discarded. PCA and Leiden run with `random_state=0`.

### 3.7 Optional normalization and transform

Both steps are opt-in and leave `.X` as raw counts by default:

- total-count normalization (`scanpy.pp.normalize_total`, target sum \(10^4\)) when `total_counts_normalize=true`
- `scanpy.pp.log1p` when `log1p_transform=true`

`layers['raw']` receives a copy of `.X` immediately before these steps, and **only** when
at least one of them is enabled. With both off, `.X` holds the raw counts and no layer is
written. The downstream alignment and registration stages consume `.X` as-is, without
further normalization.

### 3.8 Output metadata and persistence

Per-sample output sets:

- `.obs_names` prefixed `<sample_id>_`
- `.obs['sample_id']` (categorical)
- `.obs['cluster']` (categorical)
- per-spot / per-gene QC metrics from §3.3
- `.var['mt']` (boolean)
- `.obsm['spatial']` as `float32`
- `.uns['spot_size']` as a `float32` array of shape (2,)
- `.layers['raw']` only under the condition in §3.7

`.X` and every layer are coerced to sparse CSR and the file is written gzip-compressed.
If the output file already exists and `force_recomputing=false`, the whole of §3 is
skipped and the cached path is returned.

---

## 4. Cross-sample merged processing

`SpatialTranscriptomicDataset.process_dataset` performs sample-level preprocessing first, then merged processing.

### 4.1 Merge construction

If the merged file already exists, `force_recomputing=false`, and the set of
`.obs['sample_id']` values it contains equals the active sample set, that file is returned
and §4 is skipped.

Otherwise each per-sample `.uns['spot_size']` is read with a backed read (so no `.X` is
materialized), and the per-sample files are concatenated **on disk**, streaming one file at
a time rather than holding them all plus the result in memory:

```python
concat_on_disk_compat(sample_files, merged_file,
                      axis=0, join='outer', fill_value=0, merge='same')
```

The outer join takes the union of genes across samples, and a gene absent from a sample is
filled with 0 counts. `.uns` is dropped by the concat; `spot_size` is rebuilt from the
backed reads as a per-sample dictionary (§4.3).

The merged file is then read back and raw counts are restored on the combined object:

```python
combined.X = combined.layers.pop('raw', combined.X)
```

The `'raw'` layer is present only if the per-sample files were normalized (§3.7); when
they were not, `.X` already holds raw counts. Either way, §4.2 and the recomputed QC of
§4.3 operate on raw counts. `.X` and any layer are then coerced to sparse CSR.

### 4.2 Cross-sample gene filtering

Both criteria are opt-in; when both are `null` the whole of §4.2 is skipped and every gene
is kept.

Each criterion is evaluated independently within every sample, and a gene is retained if it
passes in **at least one** sample:

\[
\text{keep}(g) \iff \#\{s : g \text{ passes in } s\} \ge 1
\]

The number of samples in which a gene is detected therefore has no bearing on whether it is
kept: a gene confined to a single sample survives on the strength of that sample alone. When
both criteria are set, a gene must satisfy each of them in at least one sample, not
necessarily the same one.

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

- `.var['mt']` is re-derived and QC metrics are **recomputed** on the merged raw matrix (`calculate_qc_metrics`, `qc_vars=['mt']`, `percent_top=None`), so `.obs`/`.var` QC reflect the retained spots and genes; the per-sample QC of §3.3 predates cross-sample filtering
- `combined.layers['raw']` receives a copy of `.X`, under the same condition as §3.7 — only when at least one normalization step is enabled
- optional `normalize_total` (target sum \(10^4\)) and `log1p` are applied to merged `.X`
- `.obs['sample_id']` and `.obs['cluster']` are stored as categoricals; the per-sample cluster labels carried through the concat are kept unchanged, so no clustering runs on the merged matrix
- `.obsm['spatial']` is `float32`
- `.uns['spot_size']` is a per-sample dictionary `{sample_id: [sx, sy]}`

`.X` and any layer are coerced to sparse CSR and saved gzip-compressed.

There is no highly-variable-gene selection: the panel surviving §4.2 is carried through to
registration in full.

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
