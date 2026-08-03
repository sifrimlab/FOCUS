# MSI Preprocessing Methods

Implementation reference for `focus/preprocessing/lipidomics.py`. `MsiDataset.process_dataset()`
iterates over samples, each represented by an `MsiSample`, applying the stages below in order.

---

## 1. Data model and inputs

FOCUS processes MSI data from imzML/IBD pairs (single mode or dual mode via `pos/` and `neg/`).

For each spectrum, metadata parsing (`_spectra_to_dict`, `_parse_imzml`) extracts:

- raster (pixel) coordinates from the `position x` / `position y` cvParams;
- physical coordinates from the `3DPositionX` / `3DPositionY` userParams;
- binary offsets and lengths for the m/z and intensity arrays.

**Physical-coordinate fallback.** True physical coordinates are not always present. Let
\(\mathbf{p}_i=(p_{i,x},p_{i,y})\) be the pixel index of spectrum \(i\) and
\(\mathbf{s}=(s_x,s_y)\) the per-axis raster size in micrometers (read from the `pixel size x/y`
scan settings, defaulting to \((1,1)\) when absent). Spectra missing `3DPosition*` have their
physical coordinate reconstructed from the pixel grid scaled to micrometers,

\[
\mathbf{r}_i = \mathbf{p}_i \odot \mathbf{s},
\]

applied either to the whole dataset (no spectrum carries physical coordinates) or only to the
affected spectra (mixed case). This keeps the inter-spot spacing physically consistent with the
half-raster centering applied later (§2.2). Prior to any processing the IBD payload is validated:
a truncated file (smaller than the largest `offset + length × itemsize`) raises `ValueError`, and
spectra containing non-finite m/z or intensity values are dropped.

Supported intensity normalization options are `none`, `tic`, `log`, `clr`, and `tic_mean_scaled`.

---

## 2. Spatial coordinate correction

### 2.1 Rotation correction

Physical coordinates are rotated to reduce scan-line tilt.

Procedure (`_correct_rotation_error`):

1. Select the raster x-column with maximal occupancy, \(x^\* = \arg\max_x \#\{i : p_{i,x}=x\}\),
   and take the physical points lying on it.
2. Fit a line \(y = kx+b\) to those points by ordinary least squares (slope \(k\) only is used).
3. Compute the tilt angle \(\theta=\arctan(k)\).
4. Rotate **all** physical points by \(-\theta\) about their centroid \(\bar{\mathbf{r}}\):

\[
\mathbf{r}_i' = R(-\theta)\,(\mathbf{r}_i-\bar{\mathbf{r}}) + \bar{\mathbf{r}},
\qquad
R(\alpha)=\begin{bmatrix}\cos\alpha & -\sin\alpha\\ \sin\alpha & \cos\alpha\end{bmatrix}.
\]

This removes the scan-line tilt while preserving the centroid and all inter-spot distances.

---

### 2.2 Dual ion mode affine harmonization

When both ion modes are present the two acquisitions must be expressed in a single shared frame
(`_filter_unpaired_spots`, `initialize_sample`).

**1. Pixel-axis orientation detection.** The two modes may be acquired with mirrored pixel axes.
For each mode a pixel→physical linear map is fitted by least squares,
\(\mathbf{r} \approx [\,\mathbf{p}\ \ \mathbf{1}\,]\,T\), giving \(T\in\mathbb{R}^{2\times2}\) (the
intercept row is discarded). The maps are normalized, \(\hat T = T/\lVert T\rVert_F\), and the
axis-flip \((f_x,f_y)\in\{\pm1\}^2\) is chosen to minimize the Frobenius mismatch between the
negative and positive maps,

\[
(f_x,f_y)=\arg\min_{f}\;\bigl\lVert \operatorname{diag}(f)\,\hat T_{\text{neg}} - \hat T_{\text{pos}}\bigr\rVert_F .
\]

If a flip is selected, the negative-mode pixel coordinates are mirrored about their own range
before pairing (this prevents matching opposite ends of the scan).

**2. Unpaired-spot removal.** Only pixel positions present in **both** modes are retained
(structured-array intersection); negative indices are reordered so paired rows share the same
pixel coordinate.

**3. Affine harmonization.** A 2D affine map is fitted from positive to negative physical
coordinates on augmented inputs \(A=[\,\mathbf{r}_{\text{pos}}\ \ \mathbf{1}\,]\) (one least-squares
fit per output axis), and applied to the positive coordinates. The shared coordinate is the
midpoint of the transformed positive and the negative sets,

\[
\mathbf{r}_i^{\text{shared}} = \tfrac{1}{2}\bigl(\mathcal{A}(\mathbf{r}_{\text{pos},i}) + \mathbf{r}_{\text{neg},i}\bigr).
\]

**4. Origin normalization and centering.** Coordinates are shifted so the minimum is the origin,
then offset to the raster-cell center by \(\mathbf{s}/2\) (a half-raster shift, restricted to the
longer axis for non-square rasters).

The resulting physical coordinates are shared across modes for that sample.

---

## 3. Tissue/background detection

If `detect_background=true` **and** a `lipid_annotation_db` is configured, foreground (tissue/cell)
spots are separated from background spots per spot from spectral-complexity features
(`_detect_tissue_spots`). Tissue spectra are richer and more diverse than background, which is
dominated by uniformly sprayed matrix/standards.

!!! warning "Gated on the annotation database"
    The detector is invoked from `load_payload` only when `annotation_db is not None`. With
    `detect_background=true` and no database, detection is skipped, `filtered_idx` stays `None`, and
    `foreground_mask` marks every spot as foreground.

**Per-spot features.** For spot \(i\) with raw centroided intensities \(I_i=(I_{i,1},\dots,I_{i,k_i})\):

- **Peak count** \(n_i = k_i\), the number of detected peaks.
- **Total ion current** \(\mathrm{TIC}_i = \sum_{m} I_{i,m}\) (sum over the spot's *raw* peaks). This
  is the raw-peak TIC, distinct from the on-grid TIC used for normalization in §6.
- **Shannon entropy** of the intensity distribution \(p_{i,m}=I_{i,m}/\mathrm{TIC}_i\), restricted to
  \(p_{i,m}>0\):

\[
H_i = -\sum_{m:\,p_{i,m}>0} p_{i,m}\,\log_2 p_{i,m}.
\]

- **Optional annotation-hit ratio** (when a lipid DB and `mass_tolerance` are supplied): the
  fraction of the spot's peaks that match a database ionized mass within ppm tolerance,
  \(\#\{\text{unique matched peaks}\}/n_i\).

The feature set is \(\{H_i,\ n_i,\ \log(1+\mathrm{TIC}_i),\ (\text{hit ratio})_i\}\). Each feature is
min-max normalized over valid spots (\(n_i>0\)) to \([0,1]\), and the composite score is their mean:

\[
\text{score}_i = \frac{1}{F}\sum_{f=1}^{F} \frac{x^{(f)}_i - \min_j x^{(f)}_j}{\max_j x^{(f)}_j - \min_j x^{(f)}_j}.
\]

### `sample_type: tissue`

A contiguous section is typically class-imbalanced (e.g. 95 % tissue / 5 % background), which
defeats Otsu. The scores are modeled with Gaussian mixtures and selected by BIC:

- Fit a 1-component and a 2-component GMM (\(n_\text{init}=3\)) to \(\{\text{score}_i\}\).
- If \(\mathrm{BIC}_1 \le \mathrm{BIC}_2\) (unimodal preferred): keep **all** valid spots.
- Otherwise classify by posterior on the higher-mean component \(c^\*=\arg\max_c \mu_c\): spot \(i\)
  is foreground iff \(P(c^\* \mid \text{score}_i) \ge 0.5\).
- **Spatial cleanup**: rasterize the mask on the pixel grid, then apply `binary_fill_holes`
  followed by `binary_opening` with a \(3\times3\) structuring element.

### `sample_type: microgrid`

Isolated single cells on a mostly-background grid; spatial cleanup is disabled (it would erase
real cells). A 1D Otsu threshold maximizes between-class variance over the 256-bin score histogram.
This is the same criterion used for image segmentation in
[microscopy §3](microscopy_methods.md#3-background-removal-remove_background), applied here to the
composite score instead of to pixel intensities:

\[
t^\* = \arg\max_{t}\ \sigma_b^2(t),\qquad
\sigma_b^2(t)=\omega_\text{bg}(t)\,\omega_\text{fg}(t)\,\bigl(\mu_\text{bg}(t)-\mu_\text{fg}(t)\bigr)^2,
\]

with \(\omega\) the class weights (mass fractions) and \(\mu\) the class means below/above \(t\). To
avoid discarding weak single-cell signals, the applied threshold is floored at the 25th percentile:
\(\;t_\text{eff} = \min(t^\*,\ Q_{25})\). A spot is foreground iff \(\text{score}_i \ge t_\text{eff}\).

Degenerate cases (all features constant, or too few valid spots) fall back to keeping all spots.
The foreground mask is stored in `.obs['foreground']`; **all** spots remain in the matrix.

---

## 4. Recalibration and m/z backbone

### 4.1 Recalibration reference selection

If no `recalibration_reference` is supplied, reference masses are selected automatically
(`_CalibrationReferenceSelector`), independently per ion mode, from **every** spectrum of every
sample. The candidate pool is the annotation-matched m/z when a `lipid_annotation_db` is available,
and all m/z otherwise.

#### 4.1.1 Logarithmic reduction

A calibrant is never recorded at exactly the same m/z twice, so candidates must be pooled within a
relative tolerance before they can be counted. Peaks are therefore indexed on a logarithmic grid.
For tolerance \(\tau\) (ppm) and subdivision \(\beta=2\), the bin width in natural-log units is

\[
w = \frac{\tau \cdot 10^{-6}}{\beta},
\qquad
k(m) = \left\lfloor \frac{\ln m}{w} \right\rfloor .
\]

This is the natural grid for a *relative* tolerance: for \(m_1 < m_2\) within \(\tau\) ppm,

\[
\ln m_2 - \ln m_1 = \ln\!\left(1 + \frac{m_2 - m_1}{m_1}\right) \approx \frac{m_2 - m_1}{m_1} \le \tau \cdot 10^{-6},
\]

so one uniform grid resolves the tolerance identically across the whole mass range, and two peaks
within tolerance lie at most \(\beta\) bins apart. Bins are narrower than the tolerance, so the
grouping in §4.1.2 determines what counts as one candidate. Boundary splits are resolved there.

Per sample and ion mode the reduction accumulates, over occupied bins only,

\[
n_b = \#\{\text{peaks in bin } b\},
\qquad
\sigma_b = \sum_{m \,\in\, b} m ,
\]

via `np.bincount` (a single linear pass, no sort). The representative mass of a bin is the exact
mean \(\sigma_b / n_b\) of the raw values that fell in it. The grid only indexes peaks; it never
replaces a measured mass with a bin centre. Samples are folded in one at a time and their raw
peaks released immediately; what persists per sample is one `int32` array of occupied bin indices.
Retained memory is therefore \(O(\ln(m_{\max}/m_{\min})/w)\), i.e. bounded by the **mass span and
tolerance**, and independent of the number of spectra.

#### 4.1.2 Grouping and scoring

The merged per-mode histogram is grouped within \(\tau\) ppm by the same weighted sliding window
used to build the m/z backbone (`cluster_unique_mz_labels`, sharing the growth rule of
`cluster_unique_mz_chunk`): bins are visited in ascending mass and a bin joins the current group
while it stays within \(\tau\) ppm of the group's running weighted centroid. Group \(g\) then has

\[
c_g = \sum_{b \in g} n_b ,
\qquad
\mu_g = \frac{\sum_{b \in g} \sigma_b}{c_g},
\qquad
k_g = \#\{\, s : \exists\, b \in g \text{ with a peak from sample } s \,\} .
\]

\(k_g\) is counted over **distinct samples**: presence is collapsed to
\((\text{sample}, \text{group})\) pairs before counting, so a sample whose jitter spreads one
calibrant across several bins of a group contributes exactly once and \(k_g \le S\).

With \(S\) the number of samples **having that ion mode** (so mixed single-/dual-mode cohorts score
against the samples that could contribute), groups are scored by

\[
\text{score}_g = c_g \cdot \left(\frac{k_g}{S}\right)^{\alpha},\qquad \alpha = 1,
\]

i.e. occurrence frequency weighted by cross-sample coverage. Groups are ordered by descending score
with **ascending \(\mu_g\) as tie-break** (a `lexsort`, giving a total order) and taken greedily
until at least \(N_\text{ref}=5\) (hard-coded) are selected **and** every sample is covered (a group
covers a sample when that sample has a peak in it). A second pass then adds any further group that
covers a still-uncovered sample. The selected \(\mu_g\) are returned in ascending order.

Because grouping precedes scoring, the \(N_\text{ref}\) references are \(N_\text{ref}\) *distinct*
calibrants separated by more than \(\tau\), each reported at the weighted centroid \(\mu_g\) of all
its measurements. That centroid is a lower-variance estimate of the true mass than any single
observation.

The references are shared by the whole dataset: one set per ion mode, used by every sample. The
per-sample quantity is the offset \(\Delta_x\) derived from them (§4.2).

### 4.2 Per-row recalibration

Recalibration removes a systematic per-row (per scan-line) m/z drift (`_recalibrate_mz_vector`).
For each reference mass \(\mu_j\) and each spectrum, the highest-intensity peak within tolerance
\(|m-\mu_j|\le \mu_j\cdot \text{ppm}\cdot10^{-6}\) (and above `min_intensity_threshold`, if set) is
matched, giving a local offset \(m_\text{match}-\mu_j\). Offsets are accumulated in a
\((X,Y,N_\text{ref})\) array; the offset for raster row \(x\) is the mean over that row's columns and
reference masses, ignoring unmatched entries:

\[
\Delta_x = \operatorname*{nanmean}_{y,\,j}\bigl(m^{(x,y)}_{\text{match},j} - \mu_j\bigr).
\]

Each m/z in row \(x\) is corrected by \(m' = m - \Delta_x\). Rows with no matches to **any**
reference mass (all-NaN offsets) are left unchanged.

### 4.3 Consensus backbone construction

The consensus backbone is built in two levels (`_compute_reference_mz`): a per-sample backbone is
computed with frequency filtering, then the per-sample backbones are merged globally **without**
a frequency cutoff (`frequency_threshold=0`) to form the final reference m/z vector.

Within each level, all m/z are rounded (6 decimals), sorted, and uniquified with counts, then:

1. **Chunked clustering** (`cluster_unique_mz_chunk`, run in parallel; see below). A sliding window
   grows a weighted centroid \(c\): the next candidate \(a\) joins the cluster while its ppm distance
   to the **running centroid** is within tolerance, and \(c\) is updated as the count-weighted mean.
2. **Chunk merging** (`merge_chunks`). Adjacent chunk centroids are merged when within tolerance,
   combining them by weighted average.
3. **Boundary re-consolidation.** Because merging can leave boundary centroids closer than tolerance,
   the weighted sliding-window clustering is re-run once on the merged result, guaranteeing
   consecutive reference peaks are \(\ge\) tolerance apart.
4. **Frequency filter** (per-sample level only): clusters are kept when their accumulated weight
   \(w\) satisfies \(w \ge \texttt{frequency\_threshold}\cdot \max_k w_k\).

The two phases use **different ppm denominators**, matching the implementation:

\[
\delta_{ppm}^{\text{cluster}}(a,c)=\frac{|a-c|}{c}\times 10^6
\quad\text{(candidate vs. running centroid)},
\qquad
\delta_{ppm}^{\text{merge}}(a,b)=\frac{|a-b|}{(a+b)/2}\times 10^6
\quad\text{(symmetric mean denominator)}.
\]

Parallelization uses a `ProcessPoolExecutor` over CPU cores; the chunk count is chosen from the
number of unique m/z, item size, and available memory (`_calculate_chunks_for_consensus_estimation`),
and adjacent chunks overlap by 5 % so clusters straddling a boundary are not split.

---

## 5. Intensity interpolation to backbone

Each raw peak distributes intensity to reference bins within ppm tolerance using inverse-distance weighting:

\[
w_{ij}=\frac{1}{\delta_{ppm}(m_i,\hat m_j)+\varepsilon},\quad \varepsilon=10^{-9}
\]

\[
\tilde I_j \mathrel{+}= I_i\,\frac{w_{ij}}{\sum_{j'} w_{ij'}}
\]

Implemented in numba (`interpolate_single`) and parallelized across spectra via `joblib`.

---

## 6. Intensity normalization

Applied after interpolation:

All methods are applied **independently per ion mode** (each mode's matrix is normalized before the positive/negative blocks are concatenated):

- `none`: unchanged
- `tic`: divide row by TIC (rows with TIC=0 use divisor 1); each spectrum then sums to 1
- `log`: `log1p`
- `clr`: sparsity-preserving centered log-ratio. For each spectrum, the log is taken over the nonzero entries only and centered by the mean log over that nonzero support; structural zeros are left at 0, so sparsity is preserved.
- `tic_mean_scaled`: divide each spectrum by the scaling factor \(f_s = T_s / \bar{T}\), where \(T_s\) is the spot's total ion current and \(\bar{T}\) is the mean total ion current over that sample's spots for that ion mode (\(\bar{T}=0\) uses divisor 1; empty spots stay at 0). Each spectrum is thus rescaled to total \(\bar{T}\). Equivalent to `tic` multiplied by a per-sample constant. It removes per-spot total-intensity variation like `tic`, but preserves an interpretable absolute intensity scale instead of compressing every spectrum to sum 1. The mean is taken within each sample and ion mode, so it does not make intensities comparable across samples.

Raw interpolated matrix is preserved in `.layers['raw']`; normalized matrix in `.X`.

---

## 6b. Per-sample clustering

After normalization, per-sample cluster labels are computed (`compute_cluster_labels`) and stored in
`.obs['cluster']`. They are consumed only by the alignment GUI for categorical spot colouring, and no
downstream algorithm uses them numerically. The routine therefore computes a fast Leiden-*like*
partition instead of an exact Leiden over every spot.

`compute_cluster_labels` is shared with [ST §3.6](st_methods.md#36-per-sample-clustering), and its
coarsening grid and 100,000-row cap are shared with the alignment GUI's display binning
([Alignment §4b](alignment_methods.md#4b-display-coarsening-for-large-spot-sets)).

For a sample with \(n\) spots and cap \(C = 100{,}000\):

- **\(n > C\)**: the spots are coarsened, which also aggregates the weak signal of an individual
  ultra-high-resolution MSI spot into a signal where PCA/Leiden can resolve structure. A uniform
  spatial grid of at most \(C\) cells is laid over the coordinates (never finer than the native spot
  spacing, so cells cannot fall between samples), and all spots in a cell are **summed** into one
  pseudo-spot. Since summing breaks the per-spot normalization, the pseudo-spots are total-count
  normalized to the median (no `log1p`, matching the unbinned path).
- **\(n \le C\)**: no binning; Leiden runs on every spot, matrix used as-is.

Then \(\text{PCA} \to \text{kNN} \to \text{Leiden}\) on that matrix, with
\(n_\text{pcs} = \min(50,\, n_\text{run}-1,\, n_\text{vars}-1)\),
`n_neighbors = min(15, n_run - 1)`, and Leiden at `resolution=0.5`, `flavor="igraph"`,
`n_iterations=2`, `directed=False`. Each cell's label then propagates back to every spot that
contributed to it.

The binned matrix, PCA embedding and neighbour graph live on a throwaway `AnnData` and are never
persisted; only the per-spot label array is. Samples with fewer than 2 run-rows or fewer than 2
usable PCs, and samples resolving to a single cluster, receive the single label `'0'`. PCA and Leiden
run with `random_state=0`.

---

## 7. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.h5ad
```

Merged:

```text
{dataset_path}/merged/preprocessing/{modality}_merged_processed.h5ad
```

Key fields:

- `.X`: normalized intensities (CSR)
- `.layers['raw']`: pre-normalization interpolated intensities
- `.obsm['spatial']`: physical coordinates
- `.obsm['raster_coordinates']`: raster bounding boxes
- `.obs['foreground']`, `.obs['cluster']`, `.obs['sample_id']` (all categorical)
- `.var['mz']`, `.var['mz_mode']`, `.var['lipid_annotation']`
- `.uns['spot_size']`: per-sample `[x,y]` (merged file stores per-sample dict)

---

## 8. Parameters reflected by implementation

- `mass_tolerance` (default 10; must be an `int`, since `process_dataset` rejects floats)
- `frequency_threshold` (default 0.01)
- `intensity_normalization` (default `none`, in both the settings extractor `_extract_msi_settings`
  and the `process_dataset` signature)
- `recalibration_reference` (default `null`)
- `min_intensity_threshold` (default 10000.0)
- `detect_background` (default `false`; effective only when `lipid_annotation_db` is also set)
- `sample_type` (`tissue` or `microgrid`, default `tissue`)
- `lipid_annotation_db` (optional CSV/JSON with `db_name`, `ionized_mass`, `ion_mode`)
- `force_recomputing` (default `false`)
