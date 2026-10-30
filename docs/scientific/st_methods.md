# Spatial Transcriptomics Preprocessing Methods

Implementation reference for `focus/preprocessing/transcriptomic.py`.
`SpatialTranscriptomicDataset.process_dataset()` preprocesses every sample through
`SpatialTranscriptomic` (§3) and then builds the merged dataset (§4).

---

## 1. Objective

The ST preprocessing module transforms per-sample spatial transcriptomics AnnData files into quality-controlled, merge-ready datasets while preserving spatial coordinates and raw-count provenance.

For a sample with \(N\) observations (spots/cells) and \(G\) genes, the core object is:

\[
X \in \mathbb{R}_{\ge 0}^{N\times G}
\]

with required spatial coordinates `obsm['spatial']` \(\in \mathbb{R}^{N\times 2}\).

---

## 2. Input contract and normalization of geometry metadata

Implementation (`SpatialTranscriptomic.load_data`) expects one `.h5ad` per sample directory and enforces:

- presence of `.obsm['spatial']`
- conversion of spatial coordinates to `float32`
- sparse CSR storage for `.X`

Spot footprint metadata is normalized by `_normalize_spot_size`:

- missing → `[1.0, 1.0]`
- scalar \(a\) → `[a, a]`
- length-1 array → isotropic `[a, a]`
- length-2 array → `[a_x, a_y]`

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
written to `.obs`, including `pct_counts_mt`, describe the matrix as it stood before
removal, and they are not recomputed here.

### 3.5 Observation index standardization

Observation names are prefixed with `<sample_id>_`, unless every name already carries
that prefix.

### 3.6 Per-sample clustering

Cluster labels (`.obs['cluster']`) are consumed only by the alignment GUI for spot
colouring. They are computed on `.X` at this point in the sequence: post-filter,
post-removal, and still raw counts, since §3.7 has not yet run.

ST uses `spot_cluster_labels`. It chooses the analytical units (§3.6.1), sums the raw counts
of each unit, clusters the units with steps 1 to 5, and gives every spot the label and
eligibility of its unit. Only `.obsm['spatial']` defines the units and their neighbourhoods;
no other metadata (such as `array_row` / `array_col`) is read.

The modality setting `resolution` selects one of two presets. Everything else is shared.

| Preset | Intended data | Neighbourhood | Unit growth for signal |
|---|---|---|---|
| `standard` | Multicellular spots (Visium) | 1 ring | None; native spots below the cap |
| `sub_cellular` | Weak sub-cellular spots (Visium HD, Xenium-derived bins) | 2 rings | Until the median unit holds 1,000 UMIs |

Shared values: at most 250,000 units, eligibility of 50 UMIs and 20 detected genes per unit,
2,000 HVGs, 50 PCs, 15 neighbours, Leiden resolution 0.5, seed 0. MSI uses the same feature
budget ([MSI §6b](msi_methods.md#6b-per-sample-clustering)).

1. **Clustering eligibility.** A unit needs at least 50 UMIs and 20 detected genes. These
   alignment-only thresholds are not universal biological QC cutoffs. Spots of ineligible
   units remain in the exported matrix with the label `unassigned` and a neutral display
   color. Zero-count units are always unassigned.
2. **Analytical representation.** On an independent sparse copy of eligible raw counts,
   remove unexpressed genes, normalize to 10,000 counts, apply `log1p`, select up to 2,000
   highly variable genes with Scanpy's Seurat dispersion method, and compute sparse centered
   PCA (up to 50 PCs). This feature selection never subsets the output expression matrix.
   Dataset-level gene filters run later and do not redefine these per-sample alignment labels.
3. **Spatial embedding.** Each eligible unit is represented by its own PCs joined to the mean
   PCs of its neighbourhood (§3.6.2).
4. **Partition.** A neighbor graph (up to 15 neighbors) on the spatial embedding and Leiden
   (`resolution=0.5`, `flavor='igraph'`, `n_iterations=2`, `directed=False`) run on the
   eligible units. PCA, neighbor construction and Leiden receive seed 0. Scanpy uses
   approximate neighbor construction for large inputs.
5. **Insufficient information.** Fewer than three eligible units or fewer than two usable PCs
   yields unassigned labels, with the reason recorded in provenance.

`.obs['clustering_eligible']` records eligibility; `.uns['focus_clustering']` records the
preset, method version, parameters, software versions, units, selected genes, cluster sizes,
PCA variance, PC/count-depth correlations and `label_coherence`: the agreement of each unit's
label with its 1-ring neighbours, corrected for chance (0 for labels placed at random, 1 when
all neighbours agree). Intermediate expression, embeddings and graphs are discarded.
Count-depth correlation is descriptive and is not automatically regressed out.

#### 3.6.1 Clustering units

`plan_spatial_units` keeps the spots as units unless one of two criteria applies:

- **Cap.** The sample has more than 250,000 spots.
- **Signal** (`sub_cellular` only). The sample has more than 1,000 spots and the median spot
  holds fewer than 1,000 UMIs.

Otherwise spots are grouped into coarser units. A grid whose cell size is not an integer
multiple of the acquisition step, or whose cells are not aligned to the acquisition lattice,
gives cells with periodically varying spot counts, which appear as a grid pattern in the
labels. Units are therefore built from the acquisition lattice, inferred from the coordinates:

- **Lattice inference** (`infer_lattice`). On a seeded sample of at most 20,000 spots, the 8
  nearest displacements of each spot give two basis vectors: the dominant direction among
  nearest-shell displacements, and the shortest displacement cluster more than 25 degrees
  away from it. Spots within 20 steps of the most central spot are rounded to lattice indices
  and the basis and origin are refitted by least squares; the radius triples until it covers
  every spot, so a small basis error cannot accumulate over thousands of steps. The lattice is
  accepted when the 99th percentile residual is at most 0.1 step, the maximum residual is at
  most 0.45 step, and every spot has its own lattice site. This covers square lattices at any
  rotation (Visium HD), hexagonal lattices (standard Visium, in a 60 degree basis) and
  rectangular rasters. The lattice step comes from the coordinates; `spot_size` is the spot
  footprint and can differ from it (55 µm spots at a 100 µm pitch on standard Visium).
- **Blocks.** The unit of a spot is the block (i // k, j // k) of its lattice indices (i, j),
  counted from the smallest index. Every interior block holds exactly k² sites (a
  parallelogram on hexagonal lattices), so block occupancy has no periodic component, and
  the blocks follow the lattice, not the image axes. k is the smallest integer for which the
  number of occupied blocks is at most the cap and, under the signal criterion, the median
  block total reaches 1,000 UMIs. The signal criterion stops growing k before fewer than
  1,000 blocks would remain; the provenance then records `signal_limited` and a warning is
  reported.
- **Voronoi supercells.** Layouts without a lattice (segmented cells, beads) are grouped into
  m tiles. m spots drawn with the seeded random generator are the initial seeds, every spot
  is assigned to its nearest seed, and five Lloyd iterations move each seed to the centroid
  of its tile. Seeds follow spot density, and tiles hold similar spot counts with no periodic
  structure. m is the cap, reduced to n / s under the signal criterion, where
  s = ⌈1,000 / median spot count⌉, and never below 1,000.

Units exist only in memory. `.uns['focus_clustering']['units']` records the method
(`native`, `lattice` or `voronoi`), the layout (`lattice` or `irregular`), the lattice step,
the number of units, the criterion that set their size (`cap`, `signal` or both) and, for
blocks, `k` and the block size in coordinate units. Per-spot cluster sizes and eligible-spot
counts are recorded next to the per-unit values.

#### 3.6.2 Neighbourhood embedding

Expression of a single sub-cellular unit is dominated by sampling noise, so labels computed
from each unit alone scatter at the scale of single units. Each unit's clustering embedding
is therefore `[sqrt(1 - w) * own PCs, sqrt(w) * neighbourhood mean PCs]` with w = 0.8, the
weighting BANKSY uses for domain segmentation. The neighbourhood mean averages the unit with
its eligible neighbours:

- **Lattice layouts:** the units whose grid offset has a physical length of at most
  (r + 0.5) times the longest grid step, for r rings: 8 units on square grids and 6 on
  hexagonal grids for r = 1. The neighbourhood is isotropic in physical space.
- **Irregular layouts:** the 3r(r + 1) nearest unit centroids (6 for r = 1).

The unit's own PCs are kept because a neighbourhood mean alone makes adjacent units nearly
identical; the neighbor graph then reproduces the spatial grid, and Leiden divides
homogeneous tissue into compact patches. On a synthetic two-domain lattice with weak
per-spot signal, the 2-ring mean alone gave 15 to 17 patches, while the weighted embedding
recovered the two domains (adjusted Rand index 0.99). Expression itself is never smoothed.

The presets were calibrated on a Visium HD sample with 16 µm bins (median 539 UMIs per bin)
and on data derived from it. Neighbour coherence was:

| Data | Setting | Coherence |
|---|---|---|
| 16 µm bins, unchanged | Labels from each bin alone | 0.47 |
| 16 µm bins, unchanged | `sub_cellular` (2 x 2 blocks, 2 rings) | 0.81 |
| Counts thinned 64 times, emulating 2 µm bins | Signal target of 50 UMIs | 0.06 |
| Counts thinned 64 times, emulating 2 µm bins | Signal target of 1,000 UMIs | 0.71 |
| 3 x 3 sums at 96 µm pitch, emulating standard Visium | No neighbourhood | 0.37 |
| 3 x 3 sums at 96 µm pitch, emulating standard Visium | `standard` (1 ring) | 0.67 |

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
If `force_recomputing=false` and the output file's `.uns['focus_preprocessing_fingerprint']`
matches the current fingerprint (§6), the whole of §3 is skipped and the cached path is returned.

---

## 4. Cross-sample merged processing

`SpatialTranscriptomicDataset.process_dataset` performs sample-level preprocessing first, then merged processing.

### 4.1 Merge construction

If `force_recomputing=false`, no sample was rebuilt in this run, and the merged file's
fingerprint matches the per-sample fingerprints (which fix the active sample set) and the
merged gene-filter and normalization settings, that file is returned and §4 is skipped.

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
kept: a gene confined to a single sample is kept on the basis of that sample alone. When
both criteria are set, a gene must satisfy each of them in at least one sample, not
necessarily the same one.

#### A) Expression-frequency filter (`min_spots_per_gene` = θ)

Within each sample \(s\) with \(N_s\) observations, gene \(g\) passes if:

\[
\#\{i: X_{ig}>0\} \ge \max\left(1,\left\lceil \theta N_s \right\rceil\right)
\]

Constraint enforced in code: \(0 < \theta < 1\).

#### B) Count-per-expressed-spot ratio (`min_count_spots_ratio_per_gene` = ρ)

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
- `combined.layers['raw']` receives a copy of `.X`, under the same condition as §3.7: only when at least one normalization step is enabled
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
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.h5ad
```

Merged:

```text
{dataset_path}/merged/preprocessing/{modality}_merged_processed.h5ad
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

Output spot/gene filtering and output normalization remain opt-in. Clustering has its own eligibility and normalization, which do not remove data from the output.

`resolution` defaults to `standard`. The clustering values of each preset are fixed (§3.6);
they are not configuration settings.

Per-sample cache keys include the input SHA-256, effective preprocessing settings (including `resolution`),
method version and numerical software versions. Merged keys include all sample keys and the
merged gene-filter settings. Files without a key are recomputed. Changed ST inputs
invalidate dependent alignment caches through the orchestrator, and registration caches through
both the orchestrator and the registration fingerprint, which includes the target's preprocessing
fingerprint.
