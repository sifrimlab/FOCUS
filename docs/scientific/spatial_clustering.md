# Alignment Clustering

Spatial transcriptomics (ST) and MSI samples receive per-sample cluster labels in
`.obs['cluster']`. The labels colour spots in the alignment GUI only: no registration,
annotation or export step reads them. Both modalities run one shared routine,
`focus.spatial.clustering.cluster_spots`, configured by the modality preset. The modality supplies
three parts: the spots that take part, the eligibility of a unit, and the normalized representation
of the summed counts or intensities.

Method version `clusters-2`, introduced with ST `st-6` and MSI `msi-14`. Every ST and MSI output is
rebuilt once, and every ST and MSI alignment pair is aligned again once, because the alignment key
is the preprocessing fingerprint.

## 1. Presets

The clustering mode comes from the modality preset; the `resolution` setting decides whether units
are aggregated for signal and sets the neighbourhood of the embedding (§4).

| Modality preset | Mode | Aggregation signal under `sub_cellular` | Neighbourhood |
|---|---|---|---|
| ST `unit: spot` | domains | none (`resolution` is `standard`) | 1 ring |
| ST `unit: bin`, `unit: cell` | domains | summed counts, median unit at least 1,000 UMIs | 2 rings |
| MSI `sample_type: tissue`, `tissue_noisy` | domains | detected features, median unit at least 200 | 1 ring (`standard`), 2 rings (`sub_cellular`) |
| MSI `sample_type: cell_culture` | gradient | detected features, median unit at least 200 | 1 ring (`standard`), 2 rings (`sub_cellular`) |
| MSI `sample_type: microgrid` | foreground | none (`resolution` is not used) | none |

ST derives `resolution` from `unit` (`spot`: `standard`; `bin`, `cell`: `sub_cellular`). MSI reads
the modality setting `resolution` (default `standard`).

- **domains**: tissue domains. Leiden overclusters the units on an edge-preserving neighbourhood
  embedding, and adjacent clusters that no density valley separates are merged (§4, §5).
- **gradient**: signal that changes along smooth gradients rather than between discrete domains.
  The labels are ordered levels of the first principal component (§6).
- **foreground**: isolated cells of about one pixel on a background matrix. The labels separate the
  foreground from the background, without clustering (§7).

The representation (§3) and the labels are never smoothed; space enters the clustering embedding
only (§4).

## 2. Clustering units

`plan_spatial_units` keeps the spots as units unless one of two criteria applies:

- **Cap.** The sample has more than 250,000 spots.
- **Signal** (`sub_cellular` only). The sample has more than 1,000 spots and the median spot signal
  is below the preset's target. The signal of a unit is its summed counts (ST) or the number of
  features with a nonzero value in its summed spectrum (MSI). FOCUS design choice: the number of
  detected features does not depend on the instrument's intensity scale, and it rises as weak
  spectra are summed. It is not additive over the spots of a unit, so it is recomputed on every
  candidate unit plan.

Otherwise spots are grouped into coarser units. A grid whose cell size is not an integer multiple of
the acquisition step, or whose cells are not aligned to the acquisition lattice, gives cells with
periodically varying spot counts, which appear as a grid pattern in the labels. Units are therefore
built from the acquisition lattice, inferred from the coordinates:

- **Lattice inference** (`infer_lattice`). On a seeded sample of at most 20,000 spots, the 8 nearest
  displacements of each spot give two basis vectors: the dominant direction among nearest-shell
  displacements, and the shortest displacement cluster more than 25 degrees away from it. Spots
  within 20 steps of the most central spot are rounded to lattice indices and the basis and origin
  are refitted by least squares; the radius triples until it covers every spot, so a small basis
  error cannot accumulate over thousands of steps. The lattice is accepted when the 99th percentile
  residual is at most 0.1 step, the maximum residual is at most 0.45 step, and every spot has its
  own lattice site. This covers square lattices at any rotation (Visium HD), hexagonal lattices
  (standard Visium, in a 60 degree basis) and rectangular rasters (MSI). The lattice step comes from
  the coordinates; `spot_size` is the spot footprint and can differ from it (55 µm spots at a 100 µm
  pitch on standard Visium). FOCUS design choice.
- **Blocks.** The unit of a spot is the block (i // k, j // k) of its lattice indices (i, j),
  counted from the smallest index. Every interior block holds exactly k² sites (a parallelogram on
  hexagonal lattices), so block occupancy has no periodic component, and the blocks follow the
  lattice, not the image axes. k is the smallest integer for which the number of occupied blocks is
  at most the cap and, under the signal criterion, the median block reaches the target. The signal
  criterion stops growing k before fewer than 1,000 blocks would remain; the provenance then
  records `signal_limited` and a warning is reported.
- **Voronoi supercells.** Layouts without a lattice (segmented cells, beads) are grouped into m
  tiles. m spots drawn with the seeded random generator are the initial seeds, every spot is
  assigned to its nearest seed, and five Lloyd iterations[^lloyd1982] move each seed to the centroid
  of its tile. Seeds follow spot density, and tiles hold similar spot counts with no periodic
  structure. m is the cap, reduced under the signal criterion to n / s tiles, where s starts at
  ⌈target / median spot signal⌉ and grows by half until the median tile reaches the target; m never
  falls below 1,000.

Only the spots that take part (MSI: foreground spectra with at least one peak; ST: every spot) are
summed into a unit, and only they count toward its signal. A unit is eligible when it holds such a
spot and passes the modality rule: ST needs at least 50 UMIs and 20 detected genes, MSI one nonzero
feature. These thresholds are alignment display rules, not tissue QC. Spots of ineligible units and
spots that take no part keep their place in the exported matrix with the label `unassigned` and a
neutral display colour. Every spot of an eligible unit takes the unit's label.

Units exist only in memory. `.uns['focus_clustering']['units']` records the method (`native`,
`lattice` or `voronoi`), the layout (`lattice` or `irregular`), the lattice step, the number of
units, the criterion that set their size (`cap`, `signal` or both) and, for blocks, `k` and the
block size in coordinate units.

## 3. Representation

The units are clustered on normalized, log-scale values, never on raw counts or intensities. The
representation follows the modality's configured normalization, so the labels show what the
processed data shows.

- **ST.** The summed counts of the eligible units are normalized with the configured method
  ([ST §3.5](st_methods.md)), applied in memory to the units of the sample. The library-size or area
  scale is the median of the sample's units instead of the merged dataset; the method is otherwise
  identical. `normalization: none` uses the method `auto` resolves to for the technology
  (`log_normalize` for sequencing, `area_log_normalize` for imaging); area normalization falls back
  to `log_normalize` when a spot has no finite positive area. `size_factors` is linear, so `log1p`
  follows it. The unit area is the summed area of its spots. FOCUS design choice: per-sample files
  keep raw counts, and the normalization enters the sample fingerprint, so changing it relabels the
  samples.
- **MSI.** Each unit is the mean of the normalized spectra (`.X`) of its spots that take part, then
  `log1p`. `intensity_normalization: none` divides each summed spectrum by the median of its nonzero
  features[^deininger2011] instead. FOCUS design choice: every MSI normalization divides a spectrum by
  one divisor at a fixed scale, so a typical feature is near 1 and `log1p` acts at the same point of
  the intensity range whatever the instrument's scale. TIC and RMS divisors follow the most intense
  ions, and so do the labels computed on them; the median divisor does not.

Features without a nonzero value in any eligible unit are removed. Samples with more than 2,000
features keep the 2,000 most variable ones by normalized dispersion[^satija2015] (Scanpy
`highly_variable_genes`, `flavor="seurat"`[^wolf2018]). The feature selection never subsets the
exported matrix. Sparse centered PCA then gives min(50, n_units - 1, n_features - 1) components,
with seed 0.

## 4. Neighbourhood embedding and partition

A single unit carries sampling noise (ST) and detection dropout (MSI) that a clustering of the units
alone reads as structure: on a Visium HD sample the domains of the tissue did not separate even on
80 µm blocks, and on a MALDI-TOF sample ions detected at random in 81 % of the spectra split one
region into interleaved clusters (§8). Each unit's clustering embedding is therefore
`[sqrt(1 - w) * own PCs, sqrt(w) * neighbourhood mean PCs]` with w = 0.8, the weighting BANKSY uses
for domain segmentation[^singhal2024]. The unit's own PCs are kept because a neighbourhood mean
alone makes adjacent units nearly identical; the neighbour graph then reproduces the spatial grid,
and Leiden divides homogeneous tissue into compact patches.

The neighbourhood mean is edge-preserving (`similarity_operator`): over the units of the
neighbourhood (lattice layouts: grid offsets of physical length at most (r + 0.5) times the longest
grid step, 8 units on square grids and 6 on hexagonal grids for r = 1; irregular layouts: the
3r(r + 1) nearest unit centroids), a neighbour weighs exp(-(d / h)²), where d is the Euclidean
distance of the two units' principal components and h the median of d over all neighbour pairs of
the sample; the unit itself weighs 1. A typical neighbour weighs about e⁻¹ of the unit, and a
neighbour across a sharp expression boundary weighs almost nothing, so the mean does not average two
domains and produces no clusters one unit wide along their boundary. This is a bilateral filter on
the unit lattice[^tomasi1998]. FOCUS adaptation: BANKSY averages the neighbours with Gaussian
weights in physical distance[^singhal2024]; here the weights are set by expression similarity, and
the azimuthal Gabor term of BANKSY is not used. Expression itself is never smoothed.

In `domains` mode, a neighbour graph (15 neighbours, seed 0, approximate for large inputs) on the
embedding and Leiden[^traag2019] (`flavor="igraph"`, `n_iterations=2`, `directed=False`, seed 0)
partition the eligible units into about 25 clusters: the resolution is searched by geometric
bisection between 0.05 and 8 (at most eight runs) for the partition closest to that count. The
target is capped so the mean cluster holds at least 300 units. FOCUS design choice: Leiden splits
every domain into several clusters and the merge of §5 joins the splits; the number of clusters at
a fixed resolution grows with the number of units (the samples of §8 needed resolutions 0.5 to 2.2),
so the target is a cluster count. The size bound keeps the merge test reliable: on two halves of a
cut 20-dimensional Gaussian, the valley ratio of §5 falls below 0.8 in 25 % of cases at 50 units
per cluster, 8 % at 200, 4 % at 300 and 1 % at 500 (200 repetitions each).

## 5. Merging splits without a density valley

Leiden at a fixed resolution also divides continuous variation, such as a gradient or a smooth
expression program, into bands, and the number of bands grows with the number of units.
`merge_unsupported_clusters` merges these splits. It follows the hierarchical merging of
Hennig[^hennig2010]: the pair of clusters closest to unimodal is merged, the criterion is
recomputed for the merged cluster, and merging stops at a cutoff. The criterion is Hennig's
ridgeline ratio: the density minimum between two clusters divided by the smaller of the two density
maxima on either side of it.

FOCUS adaptation: Hennig computes the ratio along the ridgeline of two fitted Gaussian components.
The clusters here are a hard Leiden partition, so the ratio is computed on the empirical density of
the units, and merged groups are scored from their input clusters:

- **Pair ratio.** Every pair of Leiden clusters that the neighbour graph of §4 joins is projected,
  in the embedding of §4, on its discriminant axis: the difference of the two means weighted by the inverse pooled
  within-cluster covariance (Fisher's linear discriminant[^fisher1936]). The covariance pools every
  Leiden cluster around its own mean, so it rests on all units, and it is shrunk toward the scaled
  identity with the Ledoit-Wolf estimator[^ledoit2004]. FOCUS design choice: a covariance estimated
  from the two clusters alone overfits small clusters in 100 dimensions and shows valleys that the
  data does not have.
- **Density.** A 512-bin histogram of the projection over its 0.5 % to 99.5 % quantile range is
  smoothed with a Gaussian kernel at the normal-reference bandwidth
  0.9 · min(sd, IQR / 1.34) · n^(-1/5)[^silverman1986]. FOCUS design choice: on the full range, a
  few outlying units compress both clusters into a few bins and hide a valley. The valley is the
  density minimum between the two cluster medians; the ratio divides it by the smaller of the
  density maxima on either side. The ratio is 1 for any unimodal density and tends to 0 for a
  clean gap.
- **Group score.** Two groups of Leiden clusters are scored by the mean of the pair ratios across
  them, each weighted by its contact: the summed neighbour-graph connectivity between the two Leiden
  clusters. FOCUS design choice: the ratio of the union of two groups is not used, because a
  smaller group beside a larger one shows as a shoulder of the union's density rather than a
  separate peak, so a growing group absorbs its neighbours one by one (a Visium HD sample merged to
  a single cluster this way, §8). The contact weights keep two Leiden clusters that touch through
  a few edges, such as the two ends of a merged chain of bands, from blocking a merge that the
  broad contacts support.
- **Merge rule.** The pair of groups with the highest score is merged while the score is at least
  0.8 (FOCUS design choice, §8).

Groups are relabelled `0`, `1`, ... by decreasing size. Merging only removes clusters, so a sample
never has more clusters than Leiden found. Known limits: two populations whose pair ratios have no
valley are merged even when their means differ, so a small domain with weak markers next to a large
one is merged into it. The neighbourhood embedding correlates adjacent units, so a smooth expression
field or gradient inside a domain is kept as a few spatially coherent bands instead of one cluster
(§8); no band mixes two domains.

## 6. Gradient levels

In `gradient` mode the neighbourhood mean (§4) of the first principal component of the eligible
units is cut at its 1/8, ..., 7/8 quantiles into eight levels, labelled `0` (lowest) to `7`. The sign of the component is set so its
largest absolute loading is positive, which makes the order deterministic. Tied quantiles collapse,
so a component with few distinct values gives fewer levels. FOCUS design choice: a culture whose
signal changes along a gradient has no density valley, so the merge of §5 would return a single
cluster and no alignment cue. Ordered levels show the direction and position of the gradient. The
levels use the categorical display colours of the alignment GUI; their names give the order.

## 7. Foreground mode

In `foreground` mode (MSI `microgrid`) the foreground spectra with a peak are labelled `0` and the
rest `unassigned`, without PCA or clustering. FOCUS design choice: a microgrid cell covers about one
pixel, and its spectrum carries too little signal to cluster; the alignment GUI shows the foreground
spots of a microgrid in one class.

## 8. Provenance and calibration

`.obs['clustering_eligible']` records eligibility. `.uns['focus_clustering']` records the method
version, mode, representation, resolution, parameters, software versions, units, selected
features, cluster sizes, the number of Leiden clusters (`n_leiden_clusters`), the number and ratios
of merges (`n_merges`, `merge_ratios`), PCA variance, the correlation of each component with the log
summed counts or intensities, and `label_coherence`: the agreement of each unit's label with its
1-ring neighbours, corrected for chance (0 for labels placed at random, 1 when all neighbours
agree). MSI also records the sample type. The merged file keeps the per-sample provenance in
`.uns['focus_clustering'][sample_id]`, and `.uns['cluster_colors']` follows the merged categories.
Intermediate matrices, embeddings and graphs are discarded.

The method was calibrated on synthetic square lattices of 120 x 120 spots with Poisson counts over
300 genes and known domains, at two depths (about 2,000 UMIs per spot, clustered as native `spot`
units; about 130 UMIs per spot, clustered as `bin` units of 3 x 3 spots), and on four real samples:
two Visium HD 16 µm melanoma sections (MEL and MEL2, `sequencing` + `bin`) and two MALDI-TOF
acquisitions of the same tissue (10 x 20 µm pixels; MEL in positive mode with `tic`, MEL2 in both
polarities with `none`). The former routine (`clusters-1`: plain neighbourhood mean of 1 ring for
`spot` and 2 rings for `bin`, Leiden 0.5, union scores with a veto on input pairs separated by a
valley) is listed for comparison. ARI: adjusted Rand index against the simulated domains, on
eligible spots. Boundary clusters: clusters with at least 80 % of their spots within one step of a
domain boundary.

| Data | Former, 2,000 UMIs | Current, 2,000 UMIs | Former, 130 UMIs | Current, 130 UMIs |
|---|---|---|---|---|
| 3 domains under a smooth field | 7 clusters, ARI 0.94, 3 boundary clusters | 9, ARI 0.66 | 3, ARI 0.95 | 5, ARI 0.87 |
| One domain with a gradient | 1 | 4 bands | 1 | 4 bands |
| 2 domains, sharp boundary | 4, ARI 0.97, 2 boundary clusters | 2, ARI 1.00 | 4, ARI 0.90 | 2, ARI 1.00 |
| 6 domains, markers of decreasing strength | 11, ARI 0.97, 1 boundary cluster | 6, ARI 1.00 | 6, ARI 0.96 | 6, ARI 0.96 |
| A domain covering 2 % of the sample, under a field | 2, ARI 1.00 | 2, ARI 1.00 | 2, ARI 0.89 | 3, ARI 0.27 |
| 2 weak domains | 4, ARI 0.97, 2 boundary clusters | 2, ARI 1.00 | 2, ARI 1.00 | 2, ARI 1.00 |

No current result has a boundary cluster. The losses are the smooth field and the gradient, kept
as bands (§5), and the 2 % domain at 130 UMIs, which the field bands absorb. Scores from the union
of two groups instead of their pair ratios merged the MEL Visium HD sample (without neighbourhood)
to one cluster; scores from the minimum or the unweighted mean of the pair ratios kept a gradient in
2 to 3 bands and lost the 2 % domain.

| Data | Former | Without neighbourhood | Current |
|---|---|---|---|
| MEL2 Visium HD, 98,728 bins (2 x 2 blocks, 24,188 units) | 5 clusters, coherence 0.87 | 36 Leiden clusters, 2 after merging, coherence 0.81 | 27 Leiden clusters (resolution 2.18), 8 after merging, coherence 0.83, 19 s |
| MEL Visium HD, 125,467 bins (2 x 2 blocks, 25,834 units) | 7, coherence 0.81 | 18, 7 after merging, coherence 0.51 | 23 (resolution 1.30), 14 after merging, coherence 0.76, 6 s |
| MEL2 MALDI-TOF, both polarities, 71,325 spectra, `none` | 15 | 31, 18 after merging, coherence 0.57 | 26 (resolution 0.63), 21 after merging, coherence 0.89, 17 s |
| MEL MALDI-TOF, positive mode, 182,865 spectra, `tic` | 13 | 29, 11 after merging, coherence 0.77 | 27 (resolution 0.47), 17 after merging, coherence 0.91, 36 s |

Without the neighbourhood, the MEL2 Visium HD labels kept a single mass and its margin: on 2 x 2,
3 x 3 and 5 x 5 blocks (1,000, 3,000 and 10,000 UMIs per block) the merge returned 2, 4 and 2
clusters, while the former labels showed 5 domains. The MEL2 MALDI-TOF labels were interleaved at
the scale of single pixels; the clusters differed mainly by the detection of m/z 747.50 and its
isotopes (detected in 91 % and 44 % of the spectra of two interleaved clusters, Moran's I of the
detection 0.18), and 1,397 of the 1,430 features detected in 20 % to 95 % of the spectra had a
detection Moran's I below 0.3. Per-polarity TIC or median normalization, fewer components, more
neighbours and the removal of those features left the coherence between 0.51 and 0.69. A plain
neighbourhood mean (1 ring) at the Leiden resolution 2.0 split the MEL2 MALDI-TOF sample into 48
compact patches. Leiden targets of 10 and 15 clusters gave 4 and 6 clusters on MEL2 Visium HD and 10
on MEL2 MALDI-TOF, fewer domains than the former labels; 25 gave 8 and 21. One or two rings changed
the MALDI-TOF results little (16 and 19 clusters at resolution 0.5); two rings recovered more of the
Visium HD domains (9 against 5 clusters at resolution 2.0).

The MSI features target was calibrated before the neighbourhood embedding was added, on the MEL
MALDI-TOF acquisition (median 291 detected
features per spectrum), with intensities thinned to emulate smaller pixels: each intensity was read
as a count of detection quanta (the 5th percentile of the nonzero intensities) and thinned
binomially, so weak peaks fall below detection first.

| Kept share (median detected features) | Native pixels | Target 100 | Target 200 | Target 400 |
|---|---|---|---|---|
| 1/8 (95) | 4 clusters, coherence 0.64, ARI 0.28 | k = 2, 7, 0.69, 0.35 | k = 2, 7, 0.69, 0.35 | k = 4, 14, 0.65, 0.34 |
| 1/32 (37) | 1 cluster | k = 3, 6, 0.64, 0.24 | k = 4, 8, 0.55, 0.37 | k = 8, 5, 0.56, 0.36 |
| 1/128 (12) | 28 clusters, coherence 0.02, ARI 0.01 | k = 5, 2, 0.70, 0.18 | k = 8, 4, 0.62, 0.20 | k = 14, 5, 0.54, 0.21 |

ARI against the labels of the full data. The target 200 gave the closest agreement or a tie at every
level; the full acquisition stays native under it.

## References

[^traag2019]: Traag VA, Waltman L, van Eck NJ. From Louvain to Leiden: guaranteeing well-connected communities. *Sci Rep* 9:5233 (2019). [doi:10.1038/s41598-019-41695-z](https://doi.org/10.1038/s41598-019-41695-z)
[^hennig2010]: Hennig C. Methods for merging Gaussian mixture components. *Adv Data Anal Classif* 4:3-34 (2010). [doi:10.1007/s11634-010-0058-3](https://doi.org/10.1007/s11634-010-0058-3). Hierarchical merging and the ridgeline ratio, as implemented in the fpc package (`mergenormals`, method `ridge.ratio`).
[^silverman1986]: Silverman BW. *Density Estimation for Statistics and Data Analysis*. Chapman and Hall, London (1986). [doi:10.1201/9781315140919](https://doi.org/10.1201/9781315140919). Normal-reference rule-of-thumb bandwidth.
[^fisher1936]: Fisher RA. The use of multiple measurements in taxonomic problems. *Ann Eugen* 7:179-188 (1936). [doi:10.1111/j.1469-1809.1936.tb02137.x](https://doi.org/10.1111/j.1469-1809.1936.tb02137.x)
[^ledoit2004]: Ledoit O, Wolf M. A well-conditioned estimator for large-dimensional covariance matrices. *J Multivar Anal* 88:365-411 (2004). [doi:10.1016/S0047-259X(03)00096-4](https://doi.org/10.1016/S0047-259X(03)00096-4)
[^satija2015]: Satija R, Farrell JA, Gennert D, Schier AF, Regev A. Spatial reconstruction of single-cell gene expression data. *Nat Biotechnol* 33:495-502 (2015). [doi:10.1038/nbt.3192](https://doi.org/10.1038/nbt.3192)
[^wolf2018]: Wolf FA, Angerer P, Theis FJ. SCANPY: large-scale single-cell gene expression data analysis. *Genome Biol* 19:15 (2018). [doi:10.1186/s13059-017-1382-0](https://doi.org/10.1186/s13059-017-1382-0)
[^deininger2011]: Deininger S-O, Cornett DS, Paape R, et al. Normalization in MALDI-TOF imaging datasets of proteins: practical considerations. *Anal Bioanal Chem* 401:167-181 (2011). [doi:10.1007/s00216-011-4929-z](https://doi.org/10.1007/s00216-011-4929-z)
[^singhal2024]: Singhal V, Chou N, Lee J, et al. BANKSY unifies cell typing and tissue domain segmentation for scalable spatial omics data analysis. *Nat Genet* 56:431-441 (2024). [doi:10.1038/s41588-024-01664-3](https://doi.org/10.1038/s41588-024-01664-3)
[^tomasi1998]: Tomasi C, Manduchi R. Bilateral filtering for gray and color images. *Proc. Sixth International Conference on Computer Vision (ICCV)*, 839-846 (1998). [doi:10.1109/ICCV.1998.710815](https://doi.org/10.1109/ICCV.1998.710815)
[^lloyd1982]: Lloyd SP. Least squares quantization in PCM. *IEEE Trans Inf Theory* 28:129-137 (1982). [doi:10.1109/TIT.1982.1056489](https://doi.org/10.1109/TIT.1982.1056489)
