# Spatial Transcriptomics Preprocessing Methods

Implementation reference for `focus/preprocessing/transcriptomics/`.
`SpatialTranscriptomicDataset.process_dataset()` preprocesses every sample through
`SpatialTranscriptomic` (§3) and then builds the merged dataset (§4).

---

## 1. Objective

The module turns per-sample spatial transcriptomics AnnData files of any technology into one merged
dataset with a unified feature set and one global normalization, keeping raw counts and spatial
coordinates.

For a sample with \(N\) observations (spots, bins or cells) and \(G\) genes, the core object is
the raw count matrix

\[
X \in \mathbb{N}^{N\times G}
\]

with spatial coordinates `obsm['spatial']` \(\in \mathbb{R}^{N\times 2}\) in µm.

Two settings describe the technology and select every calibrated value (§3.3, §4.1, §4.3):

- `capture`: `sequencing` (whole-transcriptome capture: Visium, Visium HD, Stereo-seq, Slide-seq) or
  `imaging` (targeted in situ panels: Xenium, MERFISH, CosMx);
- `unit`: `spot` (multicellular capture spots), `bin` (sub-cellular square bins or beads) or `cell`
  (segmented cells).

| Calibrated value | Rule |
|---|---|
| Outlier rule | `spot`: local (§3.3.2); `cell`: global (§3.3.3); `bin`: none |
| Clustering preset | `spot`: `standard`; `bin`, `cell`: `sub_cellular` (§3.6) |
| Gene rule | `sequencing`: 20 observations in one sample; `imaging`: 1 (§4.1) |
| `auto` normalization | `sequencing`: `log_normalize`; `imaging`: `area_log_normalize` (§4.3) |

The constants live in `technology.py` and enter the preprocessing fingerprint.

---

## 2. Input contract

`SpatialTranscriptomic.load_data` reads the single `.h5ad` file of the sample directory (several
files raise `ValueError`) and enforces:

- `.obsm['spatial']` present, finite, shape \((N, 2)\), cast to `float32`;
- `.uns['spot_size']` present and positive: a scalar or length-1 array becomes `[a, a]`, a length-2
  array `[a_x, a_y]`, stored as `float32`. A missing value raises `ValueError`;
- **raw counts** (`resolve_counts`): the first matrix among `.X`, `layers['counts']`,
  `layers['raw']` and `.raw.X` (when `.raw` has the same genes) whose stored values are finite,
  non-negative and integer-valued. It becomes a CSR `.X`; every other input matrix is dropped, so no
  normalized input matrix reaches a later step. No candidate raises `ValueError`;
- unique gene names (`var_names_make_unique`, reported).

---

## 3. Per-sample preprocessing pipeline

`SpatialTranscriptomic.preprocess_data` runs the following sequence on raw counts. The per-sample
output keeps raw counts; normalization runs once on the merged dataset (§4.3).

### 3.1 Control features

Imaging panels carry negative-control probes, negative-control and unassigned codewords, and blank
barcodes; they measure background, not expression. A feature is a control when
`var['feature_types']` is present and differs from `Gene Expression`, or when its name starts with
`NegControlProbe_`, `NegControlCodeword_`, `NegPrb`, `BLANK_`, `Blank-`, `UnassignedCodeword_`,
`DeprecatedCodeword_`, `Intergenic_` or `SystemControl`. Their counts per spot are stored as
`obs['total_counts_control']` and `obs['pct_counts_control']` (percent of all counts, controls
included), then the features are removed[^salas2025].

### 3.2 Mitochondrial gene flag

Mitochondrial genes are flagged in `.var['mt']` by a case-insensitive `MT-`/`MT.` name prefix.
Detection is name-based, so datasets keyed by Ensembl identifiers are not flagged.

### 3.3 Quality control

#### 3.3.1 Structural cleanup

Always applied, with no threshold:

- spots with `obs['in_tissue'] == 0`, when the column exists (spots under the capture area but
  outside the tissue);
- spots with zero counts after control removal, which carry no expression and no defined library
  size.

#### 3.3.2 Local outliers (`unit: spot`)

Library size in spatial data follows tissue structure: library-size differences between regions
are partly biological[^bhuva2024], so a global threshold removes whole low-depth domains. Spots are
compared with their spatial neighbourhood instead, as in SpotSweeper[^totty2025]. For a metric
\(m\), let \(y_i = \log(1 + m_i)\) and \(\mathcal{N}_i\) the spot and its 36 nearest spots
(`scipy.spatial.cKDTree`). With the neighbourhood median \(\tilde y_i\) and
\(\text{MAD}_i = 1.4826 \cdot \operatorname{median}_{j \in \mathcal{N}_i} |y_j - \tilde y_i|\),

\[
z_i = \frac{y_i - \tilde y_i}{\text{MAD}_i}
\]

(\(z_i = 0\) when \(\text{MAD}_i = 0\)). A spot is removed when \(z_i < -3\) for the library size
or the number of detected genes, or \(z_i > 3\) for the mitochondrial percent (when mitochondrial
genes are present). 36 neighbours and the cutoff 3 are SpotSweeper's defaults.

#### 3.3.3 Global outliers (`unit: cell`)

Segmented cells are independent observations, as in single-cell data. A cell is removed when its
\(\log(1 + m)\) lies more than 5 scaled MADs below the median, for the library size or the number
of detected genes[^germain2020] [^heumos2023]. 5 MADs is the lenient end of the 3 to 5 MAD range,
so small cell types with few transcripts are kept.

#### 3.3.4 Bins (`unit: bin`)

Sub-cellular bins hold tens of counts, so local medians and MADs are dominated by sampling noise and
a low bin is expected rather than defective. Bins receive the structural cleanup only. This is a
FOCUS design choice.

`quality_control: false` disables §3.3.2 and §3.3.3; the structural cleanup always runs. The
number of spots removed per reason is reported and stored in `.uns['focus_qc']`.

### 3.4 QC metric computation

`scanpy.pp.calculate_qc_metrics(..., qc_vars=['mt'], percent_top=None)`[^wolf2018] runs on the
retained spots, **with mitochondrial genes still present**: `total_counts`, `n_genes_by_counts`,
`total_counts_mt`, `pct_counts_mt` (and `log1p_` variants) in `.obs`, and `n_cells_by_counts`,
`mean_counts`, `total_counts`, `pct_dropout_by_counts` (and `log1p_` variants) in `.var`.

### 3.5 Area, mitochondrial removal and observation names

- `obs['focus_area']` is the denominator of area normalization (§4.3): \(s_x s_y\) from
  `spot_size` for spots and bins, and for cells the first of the `obs` columns `cell_area`,
  `area_um2`, `area`, `Area`, `volume`. Cells without such a column get no area.
- With `remove_mitochondrial_genes=true`, the genes flagged in `.var['mt']` are removed. The QC
  metrics of §3.4 describe the matrix before removal.
- Observation names are prefixed with `<sample_id>_`, unless every name already carries that prefix.

### 3.6 Per-sample clustering

Cluster labels (`.obs['cluster']`) are consumed only by the alignment GUI for spot
colouring. They are computed on the raw counts of the retained spots and genes.

ST uses `spot_cluster_labels`. It chooses the analytical units (§3.6.1), sums the raw counts
of each unit, clusters the units with steps 1 to 6, and gives every spot the label and
eligibility of its unit. Only `.obsm['spatial']` defines the units and their neighbourhoods;
no other metadata (such as `array_row` / `array_col`) is read.

The `unit` setting selects one of two presets (`spot`: `standard`; `bin` and `cell`:
`sub_cellular`). Everything else is shared.

| Preset | Intended data | Neighbourhood | Unit growth for signal |
|---|---|---|---|
| `standard` | Multicellular spots (Visium) | 1 ring | None; native spots below the cap |
| `sub_cellular` | Weak sub-cellular units (Visium HD bins, segmented cells, imaging bins) | 2 rings | Until the median unit holds 1,000 UMIs |

Shared values: at most 250,000 units, eligibility of 50 UMIs and 20 detected genes per unit,
2,000 HVGs, 50 PCs, 15 neighbours, Leiden resolution 0.5, merge cutoff 0.8, seed 0. MSI uses the
same feature budget and merging ([MSI §6b](msi_methods.md#6b-per-sample-clustering)).

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
4. **Partition.** A neighbor graph (up to 15 neighbors) on the spatial embedding and Leiden[^traag2019]
   (`resolution=0.5`, `flavor='igraph'`, `n_iterations=2`, `directed=False`) run on the
   eligible units. PCA, neighbor construction and Leiden receive seed 0. Scanpy uses
   approximate neighbor construction for large inputs.
5. **Merging.** Adjacent Leiden clusters that no density valley separates are merged (§3.6.3).
6. **Insufficient information.** Fewer than three eligible units or fewer than two usable PCs
   yields unassigned labels, with the reason recorded in provenance.

`.obs['clustering_eligible']` records eligibility; `.uns['focus_clustering']` records the
preset, method version, parameters, software versions, units, selected genes, cluster sizes,
the number of Leiden clusters (`n_leiden_clusters`), the number and ratios of merges
(`n_merges`, `merge_ratios`), PCA variance, PC/count-depth correlations and `label_coherence`:
the agreement of each unit's label with its 1-ring neighbours, corrected for chance (0 for
labels placed at random, 1 when all neighbours agree). Intermediate expression, embeddings and graphs are discarded.
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

#### 3.6.3 Merging splits without a density valley

Leiden at a fixed resolution also divides continuous variation, such as a gradient or a smooth
expression program, into bands, and the number of bands grows with the number of units.
`merge_unsupported_clusters` merges these splits after the partition. It follows the
hierarchical merging of Hennig[^hennig2010]: the pair of clusters whose union is closest to
unimodal is merged, the criterion is recomputed for the merged cluster, and merging stops at a
cutoff. The criterion is Hennig's ridgeline ratio: the density minimum between the two clusters
divided by the smaller of the two density maxima on either side of it.

FOCUS adaptation: Hennig computes the ratio along the ridgeline of two fitted Gaussian components.
The clusters here are a hard Leiden partition, so the ratio is computed on the empirical density
of the units:

- **Adjacency.** Two groups are candidates only when the neighbor graph of step 4 joins them.
- **Projection.** The units of both groups are projected on the axis between the two group
  centroids in the clustering embedding.
- **Density.** A 512-bin histogram of the projection is smoothed with a Gaussian kernel at the
  normal-reference bandwidth 0.9 · min(sd, IQR / 1.34) · n^(-1/5)[^silverman1986]. The valley is
  the density minimum between the two group medians; the ratio divides it by the smaller of the
  density maxima on either side. The ratio is 1 for any unimodal density and tends to 0 for a
  clean gap.
- **Merge rule.** The candidate pair with the highest ratio is merged while that ratio is at
  least 0.8. FOCUS design choice: a merge is refused when an adjacent pair of the Leiden
  clusters across the two groups has a ratio below 0.8. Without this rule, a merged group that a
  large mixed cluster dominates can absorb a cluster it was separated from, and the merges
  cascade; on the weak-domain sample below, down to a single cluster.

Groups are relabelled `0`, `1`, ... by decreasing size. Merging only removes clusters, so a sample
never has more clusters than Leiden found. The cutoff 0.8 is a FOCUS design choice, calibrated on
synthetic square lattices with Poisson counts and 1-ring smoothing (ARI: adjusted Rand index
against the simulated domains):

| Data | Units | Leiden clusters | After merging |
|---|---|---|---|
| 3 domains under a smooth expression field and a depth field | 102,400 | 6 (ARI 0.79) | 3 (ARI 1.00) |
| One domain with a gradient | 102,400 | 3 | 1 |
| 2 domains joined by a 12-unit mixed boundary | 102,400 | 3 (ARI 0.98) | 3 (ARI 0.98) |
| 3 weak domains under a stronger continuous field | 102,400 | 5 (ARI 0.46) | 3 (ARI 0.18) |
| A domain covering 1.9% of the sample | 22,500 | 2 | 2 (ratio 0.01) |

Cutoffs of 0.7 and 0.8 gave the same result on the first sample; 0.9 left 4 clusters. In the
weak-domain sample the domains are not separable in the embedding, and merging cannot recover them. Merging
takes a fraction of a second on these samples, while the neighbor graph takes about 20 s.

### 3.7 Output metadata and persistence

The per-sample output holds raw counts in `.X` (no layer), the QC of §3.1 to §3.4, `.var['mt']`,
`.obs['sample_id']` and `.obs['cluster']` (categorical), `.obs['focus_area']`, `.obsm['spatial']`
(`float32`), `.uns['spot_size']`, `.uns['focus_qc']` and the cache stamps. `.X` is sparse CSR; the
file is written gzip-compressed to a temporary sibling and renamed (`write_h5ad_atomic`).

If `force_recomputing=false` and the stored `.uns['focus_preprocessing_fingerprint']` matches
(§6), §3 is skipped. The input SHA-256 is stored in `.uns['focus_input_signature']` with the
input's path, size and modification time, and reused while those are unchanged.

---

## 4. Cross-sample merged processing

`SpatialTranscriptomicDataset.process_dataset` preprocesses every sample first, then builds the
merged dataset. If `force_recomputing=false`, no sample was rebuilt, and the merged fingerprint
matches, the cached merged file is returned.

### 4.1 Gene rule and union panel

For sample \(s\), \(E_{s,g}\) is the number of retained observations in which gene \(g\) is
detected (`var['n_cells_by_counts']`, read without loading the counts). A gene is kept when

\[
\max_s E_{s,g} \ge k, \qquad k = \begin{cases} 20 & \text{sequencing} \\ 1 & \text{imaging} \end{cases}
\]

so a gene confined to one sample is kept on the strength of that sample alone. The minimum of 20
observations follows the single-cell best practice of Heumos et al.[^heumos2023]. Imaging panels
are designed, so every detected panel gene is kept.

The per-sample files are concatenated **on disk** (one file streamed at a time):

```python
concat_on_disk_compat(sample_files, merged_file,
                      axis=0, join='outer', fill_value=0, merge='same')
```

The outer join builds the union panel, a gene absent from a sample being 0 there; the genes that
fail the rule are then removed in place. Spots are never removed at this level, so the merged rows
match the per-sample files, which registration (`read_target`) and compilation rely on.

### 4.2 Merged QC

`total_counts`, `n_genes_by_counts` (and `log1p_` variants) and the gene metrics are recomputed on
the final panel. `total_counts_mt`, `pct_counts_mt` and the control metrics keep the per-sample
values measured before mitochondrial and control features were removed.

### 4.3 Global normalization

The method runs once on the merged counts, so every spot of every sample shares one scale. Every
method but `none` moves the counts to `layers['raw']` and writes a `float32` `.X`;
`.uns['focus_normalization']` records the method and its scale. With library size
\(L_i = \sum_g X_{ig}\) and the median \(\tilde L\) of the positive \(L_i\) over all samples:

| `normalization` | \(\hat X_{ig}\) | Source |
|---|---|---|
| `none` | \(X_{ig}\) | |
| `log_normalize` | \(\log\left(1 + X_{ig} \tilde L / L_i\right)\) | shifted logarithm with size factors[^ahlmann2023] |
| `cp10k_log` | \(\log\left(1 + 10^4 X_{ig} / L_i\right)\) | Seurat LogNormalize[^satija2015], scanpy default[^wolf2018] |
| `size_factors` | \(X_{ig} \tilde L / L_i\) | library-size factors on the linear scale[^ahlmann2023] |
| `area_log_normalize` | \(\log\left(1 + X_{ig} \tilde a / a_i\right)\) | cell area or volume normalization[^atta2024] |

\(a_i\) is `obs['focus_area']` and \(\tilde a\) its median over all spots. A spot with \(L_i = 0\)
(possible after the gene rule) stays all zero.

`auto` gives `log_normalize` for `sequencing`. For `imaging` it gives `area_log_normalize`: with
targeted panels the library size depends on which genes the panel holds, and count-based scaling
shifts expression by panel composition, which area or volume normalization avoids[^atta2024]. When
some cell has no area, `auto` uses `log_normalize` and reports a warning; an explicit
`area_log_normalize` raises `ValueError`.

`log_normalize` scales to the median depth of the dataset rather than to \(10^4\). For sub-cellular
bins with a few hundred counts, a \(10^4\) target is equivalent to a pseudo-count far below the
data's overdispersion and stretches the zeros away from the single counts[^ahlmann2023].

Not offered: analytic Pearson residuals[^lause2021] and sctransform produce a dense matrix over the
full panel (FOCUS selects no highly variable genes), for example 36 GB at 500,000 bins and 18,000
genes. Library-size normalization in spatial data can remove part of the biological signal
carried by depth[^bhuva2024] [^osta]; `normalization: none` keeps the counts for methods that model
depth themselves.

A `spot_aggregation` registration of a normalized ST target sums the raw counts of each anchor
footprint and then applies the same method once to the merged registered file, with
`obs['focus_area']` set to the anchor footprint area.

### 4.4 Metadata and persistence

`.obs['sample_id']` and `.obs['cluster']` are categoricals (the per-sample labels are kept),
`.obsm['spatial']` is `float32`, `.uns['spot_size']` is `{sample_id: [sx, sy]}`, and the
per-sample clustering provenance is stored in `.uns['focus_clustering']`. The file is sparse CSR,
gzip-compressed, written atomically. There is no highly-variable-gene selection: the panel of §4.1
is carried through to registration in full.

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

| Setting | Default |
|---|---|
| `capture` | `sequencing` |
| `unit` | `spot` |
| `normalization` | `auto` |
| `quality_control` | `true` |
| `remove_mitochondrial_genes` | `false` |
| `force_recomputing` | `false` |

The removed settings `resolution`, `min_count_per_spot`, `max_count_per_spot`,
`min_genes_per_spot`, `max_genes_per_spot`, `min_spots_per_gene`,
`min_count_spots_ratio_per_gene`, `total_counts_normalize` and `log1p_transform` raise `ValueError`
during configuration validation, naming the replacing setting.

Per-sample cache keys hold the input SHA-256, the settings, the technology constants (§1), the
method version (`st-5`) and the numerical software versions. Merged keys hold every sample key,
the normalization and the gene rule. A changed ST fingerprint invalidates the dependent alignment
and registration caches.

## References

[^totty2025]: Totty M, Hicks SC, Guo B. SpotSweeper: spatially aware quality control for spatial transcriptomics. *Nat Methods* (2025). [doi:10.1038/s41592-025-02713-3](https://doi.org/10.1038/s41592-025-02713-3). Local outliers against the nearest spots (`localOutliers`, 36 neighbours, cutoff 3).
[^germain2020]: Germain PL, Sonrel A, Robinson MD. pipeComp, a general framework for the evaluation of computational pipelines, reveals performant single cell RNA-seq preprocessing tools. *Genome Biol* 21:227 (2020). [doi:10.1186/s13059-020-02136-7](https://doi.org/10.1186/s13059-020-02136-7)
[^heumos2023]: Heumos L, Schaar AC, Lance C, et al. Best practices for single-cell analysis across modalities. *Nat Rev Genet* 24:550-572 (2023). [doi:10.1038/s41576-023-00586-w](https://doi.org/10.1038/s41576-023-00586-w)
[^salas2025]: Marco Salas S, Kuemmerle LB, Mattsson-Langseth C, et al. Optimizing Xenium In Situ data utility by quality assessment and best-practice analysis workflows. *Nat Methods* 22:813-823 (2025). [doi:10.1038/s41592-025-02617-2](https://doi.org/10.1038/s41592-025-02617-2)
[^bhuva2024]: Bhuva DD, Tan CW, Salim A, et al. Library size confounds biology in spatial transcriptomics data. *Genome Biol* 25:99 (2024). [doi:10.1186/s13059-024-03241-7](https://doi.org/10.1186/s13059-024-03241-7)
[^ahlmann2023]: Ahlmann-Eltze C, Huber W. Comparison of transformations for single-cell RNA-seq data. *Nat Methods* 20:665-672 (2023). [doi:10.1038/s41592-023-01814-1](https://doi.org/10.1038/s41592-023-01814-1)
[^atta2024]: Atta L, Clifton K, Anant M, Aihara G, Fan J. Gene count normalization in single-cell imaging-based spatially resolved transcriptomics. *Genome Biol* 25:153 (2024). [doi:10.1186/s13059-024-03303-w](https://doi.org/10.1186/s13059-024-03303-w)
[^satija2015]: Satija R, Farrell JA, Gennert D, Schier AF, Regev A. Spatial reconstruction of single-cell gene expression data. *Nat Biotechnol* 33:495-502 (2015). [doi:10.1038/nbt.3192](https://doi.org/10.1038/nbt.3192)
[^wolf2018]: Wolf FA, Angerer P, Theis FJ. SCANPY: large-scale single-cell gene expression data analysis. *Genome Biol* 19:15 (2018). [doi:10.1186/s13059-017-1382-0](https://doi.org/10.1186/s13059-017-1382-0)
[^lause2021]: Lause J, Berens P, Kobak D. Analytic Pearson residuals for normalization of single-cell RNA-seq UMI data. *Genome Biol* 22:258 (2021). [doi:10.1186/s13059-021-02451-7](https://doi.org/10.1186/s13059-021-02451-7)
[^osta]: Bioconductor. *Orchestrating Spatial Transcriptomics Analysis with Bioconductor* (OSTA), chapter "Normalization". [bioconductor.org/books/release/OSTA](https://bioconductor.org/books/release/OSTA/)
[^traag2019]: Traag VA, Waltman L, van Eck NJ. From Louvain to Leiden: guaranteeing well-connected communities. *Sci Rep* 9:5233 (2019). [doi:10.1038/s41598-019-41695-z](https://doi.org/10.1038/s41598-019-41695-z)
[^hennig2010]: Hennig C. Methods for merging Gaussian mixture components. *Adv Data Anal Classif* 4:3-34 (2010). [doi:10.1007/s11634-010-0058-3](https://doi.org/10.1007/s11634-010-0058-3). Hierarchical merging and the ridgeline ratio, as implemented in the fpc package (`mergenormals`, method `ridge.ratio`).
[^silverman1986]: Silverman BW. *Density Estimation for Statistics and Data Analysis*. Chapman and Hall, London (1986). [doi:10.1201/9781315140919](https://doi.org/10.1201/9781315140919). Normal-reference rule-of-thumb bandwidth.
