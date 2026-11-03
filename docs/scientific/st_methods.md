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
| Clustering aggregation | `spot`: native (`standard`); `bin`, `cell`: blocks of 1,000 UMIs (`sub_cellular`) (§3.6) |
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

Cluster labels (`.obs['cluster']`) colour spots in the alignment GUI only. ST runs the clustering
shared with MSI, described in [Alignment Clustering](spatial_clustering.md), through
`spot_cluster_labels`:

- **Mode.** `domains` for every unit.
- **Aggregation.** `unit: spot` clusters native spots (`resolution: standard`); `unit: bin` and
  `unit: cell` sum spots into lattice blocks or Voronoi supercells until the median unit holds
  1,000 UMIs (`resolution: sub_cellular`), on the raw counts of the retained spots and genes.
- **Eligibility.** A unit needs at least 50 UMIs and 20 detected genes.
- **Representation.** The configured normalization (§3.5) applied in memory to the summed counts of
  the eligible units; `none` uses the technology's `auto` method, and `size_factors` is followed by
  `log1p` ([Alignment Clustering §3](spatial_clustering.md#3-representation)). The normalization
  is part of the sample fingerprint.

`.obs['clustering_eligible']` records eligibility and `.uns['focus_clustering']` the provenance
([Alignment Clustering §8](spatial_clustering.md#8-provenance-and-calibration)). The per-sample
matrix keeps raw counts.

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
