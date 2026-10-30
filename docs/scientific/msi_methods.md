# MSI Preprocessing Methods

Implementation reference for `focus/preprocessing/lipidomics.py`. `MsiDataset.process_dataset()`
iterates over samples, each represented by an `MsiSample`, applying the stages below in order.

---

## 1. Data model and inputs

FOCUS processes MSI data from imzML/IBD pairs (single mode or dual mode via `pos/` and `neg/`).

For each spectrum, metadata parsing (`_spectra_to_dict`, `_parse_imzml`) extracts:

- raster (pixel) coordinates from the `position x` / `position y` cvParams;
- physical coordinates from the `3DPositionX` / `3DPositionY` userParams;
- binary offsets and lengths for the m/z and intensity arrays;
- the scan type, `horizontal line scan` or `vertical line scan`, which sets the scan-line axis used by
  recalibration (§4.2). These are imzML controlled-vocabulary terms[^schramm2012].

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

Supported intensity normalization options are `none`, `tic`, `log`, `clr`, `tic_mean_scaled`,
`median` and `internal_standard` (§6).

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

The mass measured for one ion varies from pixel to pixel. Local composition, section thickness and
matrix deposition are sources of this mass shift, and recalibrating every spectrum against internal
calibrants reduces it[^mccann2021][^larocca2021]. Spectral alignment and recalibration are part of
published MSI processing tools[^rafols2020][^bemis2023]. FOCUS selects calibrants
from the data (§4.1), estimates one correction per spectrum (§4.2) and then builds a shared m/z
backbone from the recalibrated spectra (§4.3).

### 4.1 Recalibration reference selection

If no `recalibration_reference` is supplied, reference masses are selected automatically
(`_CalibrationReferenceSelector`), independently per ion mode, from every spectrum of every sample:
the tissue spectra when background detection ran (§3), all spectra otherwise. Selecting calibrants
from the data, instead of from an a priori list, avoids false calibrant matches that degrade the
recalibration[^larocca2021]. FOCUS design choice: the selection rules below (histogram, isolation,
scoring, spread and anchoring); none of them is taken from a published method.

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
grouping in §4.1.3 determines what counts as one candidate. Boundary splits are resolved there.

Per sample and ion mode the reduction accumulates, over occupied bins only,

\[
n_b = \#\{\text{peaks in bin } b\},
\qquad
\sigma_b = \sum_{m \,\in\, b} m ,
\qquad
q_b = \#\{\text{crowded peaks in bin } b\},
\]

via `np.bincount` (a single linear pass, no sort). Crowded peaks are defined in §4.1.2. The
representative mass of a bin is the exact mean \(\sigma_b / n_b\) of the raw values that fell in it.
The grid only indexes peaks; it never replaces a measured mass with a bin centre. Samples are folded
in one at a time and their raw peaks released immediately; what persists per sample is one `int32`
array of occupied bin indices. Retained memory is therefore \(O(\ln(m_{\max}/m_{\min})/w)\), i.e.
bounded by the **mass span and tolerance**, and independent of the number of spectra.

#### 4.1.2 Isolation

Matching (§4.2) takes the most intense peak inside a search window of half-width

\[
W = \max(5\tau,\ 50)\ \text{ppm}
\]

around a reference. A peak with a competitor in the same spectrum (another peak within \(W\) ppm
whose intensity is at least 0.1 times its own) can lose that match to the competitor. Such a peak is
**crowded** (`crowded_peaks`, numba). The test is made per spectrum: m/z drift spreads one calibrant
over neighbouring masses across spectra, but never puts two copies of it into one spectrum, so drift
alone never makes a calibrant crowded.

#### 4.1.3 Grouping, scoring and selection

The merged per-mode histogram is grouped within \(\tau\) ppm by the same weighted sliding window
used to build the m/z backbone (`cluster_unique_mz_labels`, sharing the growth rule of
`cluster_unique_mz_chunk`): bins are visited in ascending mass and a bin joins the current group
while it stays within \(\tau\) ppm of the group's running weighted centroid. A calibrant whose drift
range exceeds \(\tau\) occupies several neighbouring groups, so every group \(g\), at mass
\(\mu_g\), receives statistics pooled over its search window \(\mathcal{W}_g=\{h : |\mu_h-\mu_g| \le
W \cdot 10^{-6}\, \mu_g\}\):

\[
C_g = \sum_{h \in \mathcal{W}_g} \sum_{b \in h} n_b ,
\qquad
\hat\mu_g = \frac{\sum_{h \in \mathcal{W}_g} \sum_{b \in h} \sigma_b}{C_g},
\qquad
Q_g = \sum_{h \in \mathcal{W}_g} \sum_{b \in h} q_b ,
\]

and the coverage \(K_g\), the number of distinct samples with a peak in \(\mathcal{W}_g\). Presence
is collapsed to (sample, group) pairs before counting, so \(K_g \le S\), with \(S\) the number of
samples **having that ion mode**. A group is **isolated** when \(Q_g \le 0.1\, C_g\), and is scored
by

\[
\text{score}_g = C_g \cdot \frac{K_g}{S}.
\]

Groups are ordered by descending score with **ascending \(\mu_g\) as tie-break** (a `lexsort`,
giving a total order). Two chosen groups are always more than \(2W\) ppm apart, so their search
windows never overlap and no peak can match two references. Selection, with
\(N_\text{ref} = 10\) (hard-coded):

1. Internal standards (`internal_standard_mz`, when set): the group nearest each standard mass
   within \(W\), regardless of isolation. A standard with no group in reach is reported as not
   observed and skipped.
2. Pool: up to \(20 N_\text{ref}\) isolated groups in score order, each scoring at least 0.1 times
   the best isolated group. The score floor keeps rare (noise) peaks out of the pool.
3. Spread: the pool's \(\ln m\) span is cut into \(N_\text{ref}\) equal segments and the best pool
   member of each segment is taken, so the references cover the mass range.
4. Fill from the pool in score order until \(N_\text{ref}\) references are chosen.
5. Coverage: while a sample has no chosen reference, add the best group (isolated groups first)
   that covers it.

Each reference is reported at \(\hat\mu_g\), the mean of every occurrence within its search window:
a lower-variance estimate of the calibrant mass than any single observation. These means carry the
dataset's average drift. When internal standards are observed, the references are moved onto the
standards' mass scale. Internal calibration against known masses is the most effective correction of
mass shift[^larocca2021], and sprayed standards cover the whole section[^vandenbosch2023]. With
\(d_s = (\hat\mu_{g(s)} - m_s)/m_s \cdot 10^6\) the offset of standard \(s\) from its given mass
\(m_s\), every reference \(\mu\) is divided by \(1 + d(\mu)\cdot 10^{-6}\), where \(d\) is the
least-squares line through the \((m_s, d_s)\) when at least two standards span 25 % of the reference
m/z span, and \(\operatorname{median}_s d_s\) otherwise.

The references are shared by the whole dataset: one set per ion mode, used by every sample, and
stored in `.uns['recalibration_reference']`. The per-spectrum correction derived from them is §4.2.

### 4.2 Per-spectrum recalibration

Recalibration (`estimate_recalibration`) gives every spectrum its own m/z correction, as recommended
for MSI[^mccann2021][^larocca2021]. Each spectrum is aligned to the same reference peaks by a
per-spectrum warping function, the approach of MALDIquant `determineWarpingFunctions`[^gibb2012].
The matching rule is MALDIquant's as well: a reference matches the most intense peak inside a window
proportional to the reference mass[^gibb2012]; FOCUS also requires the peak intensity to be at least
`min_intensity_threshold`. FOCUS adaptation: the error model is linear in m/z, in ppm, and is
estimated in two levels, scan line then pixel. FOCUS design choice, for this split: pooling a whole
scan line gives many matches per estimate, so the line estimate is robust to the occasional false
match, and the pixel pass, centred on the line prediction, then follows the drift where it changes
along the line.

With references \(\mu_1 < \dots < \mu_R\), \(\bar\mu\) their mean, and \(l(i)\) the scan line of
spectrum \(i\), the ppm error of a match is

\[
e_{ij} = \frac{m_{ij} - \mu_j}{\mu_j} \cdot 10^{6}.
\]

**Scan lines.** The line axis is read from the imzML scan type (`horizontal line scan`, IMS:1000480,
or `vertical line scan`, IMS:1000481)[^schramm2012]; when absent, lines are taken as horizontal
(constant `position y`). Line indices are taken relative to the smallest coordinate, so 0-based and
1-based files are handled identically.

**Line level (wide window \(W\)).** Per line and reference, \(\tilde e_{lj} = \operatorname{median}_{i
\in l}\, e_{ij}\) over the matched spectra. A line slope \(b_l\) is fitted only when at least 3
references spanning at least 25 % of \(\mu_R - \mu_1\) are matched on the line: least squares of
\(\tilde e_{lj}\) on \(\mu_j - \bar\mu\), one rejection pass of the references whose residual exceeds
3 robust standard deviations (\(1.4826 \times\) MAD[^rousseeuw1993]), and a refit. Lines without a
supported slope take one from their neighbours (below). The line offset is

\[
a_l = \operatorname{median}_{j}\bigl(\tilde e_{lj} - b_l\,(\mu_j - \bar\mu)\bigr),
\]

estimated when the line holds at least 3 matched (spectrum, reference) pairs. Both \(b_l\) and
\(a_l\) are smoothed by a running median over 5 lines[^tukey1977], and lines still without an
estimate are filled by linear interpolation along the line index, holding the edge values. Every
line therefore receives a correction, including lines in which no calibrant clears
`min_intensity_threshold`. FOCUS design choice: the instrument state drifts continuously during the
acquisition, so a line without calibrants is corrected from its neighbours instead of being left
unchanged.

**Pixel level (window \(W\)).** References are matched again in a window of \(W\) ppm centred on the
line prediction \(\hat e_{ij} = a_{l(i)} + b_{l(i)}(\mu_j - \bar\mu)\). Centring on the prediction
keeps the calibrant in reach where the drift changes along the line (laser decay within a row moves
the row ends away from the line median), and the references are isolated within \(W\) (§4.1.2), so
the window does not admit a competing peak. A spectrum with at least
2 matches has the residual \(r_i = \operatorname{median}_j (e'_{ij} - \hat e_{ij})\). Smoothing
calibration coefficients over the sample's 2D positions follows Wolski et al.[^wolski2005]. FOCUS
adaptation: \(\delta_i\) is the median of the valid residuals over the 3 x 3 raster neighbourhood of
spectrum \(i\), itself included (`neighbourhood_nanmedian`), instead of a thin-plate spline, so the
correction keeps sharp changes such as tissue edges. A spectrum with no valid residual in its
neighbourhood takes \(\delta_i = 0\), the line model alone.

**Correction.** The error model of spectrum \(i\) is

\[
e_i(m) = a_{l(i)} + \delta_i + b_{l(i)}\,(m - \bar\mu)\quad\text{(ppm)},
\qquad
m' = \frac{m}{1 + e_i(m)\cdot 10^{-6}} .
\]

The model is estimated once per sample and ion mode from **all** spectra and applied to all of them.
The backbone (§4.3, built from the tissue spectra) and the intensity matrix (§5, built from all
spectra) therefore see the same correction. The applied correction at \(\bar\mu\) and the number of
pixel-level matches are written to `.obs` (§7).

### 4.3 Consensus backbone construction

FOCUS design choice: the backbone clustering below. The consensus backbone is built in two levels (`_compute_reference_mz`): a per-sample backbone is
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
and never exceeds the number of unique m/z. Adjacent chunks overlap by 5 % so clusters straddling a
boundary are not split, and the last chunk runs to the highest m/z.

---

## 5. Intensity binning to backbone

Each spectrum is binned onto the backbone (`interpolate_single`, numba, parallelized across spectra
via `joblib`): every peak adds its whole intensity to the single nearest backbone feature, provided
that feature lies within \(\tau\) ppm of the peak,

\[
j^\*(i) = \arg\min_j \delta_{ppm}(m_i, \hat m_j),
\qquad
\tilde I_{j^\*} \mathrel{+}= I_i \quad\text{if } \delta_{ppm}(m_i, \hat m_{j^\*}) \le \tau ,
\]

and peaks with no feature in reach are dropped. Assigning each peak to one bin follows MALDIquant
`binPeaks`[^gibb2012] and Cardinal peak alignment, which matches each detected peak to at most one
reference peak within a tolerance[^bemis2023]. FOCUS adaptation: the bin is the nearest backbone
feature within \(\tau\) (lower feature on an exact tie), and several peaks of one spectrum that reach
the same feature are summed. A peak is never split between features, so a residual m/z error smaller
than the distance to the next feature does not change any feature's intensity.

---

## 6. Intensity normalization

All methods are applied after binning, **independently per sample and ion mode** (each mode's matrix
is normalized before the positive/negative blocks are concatenated), by `normalize_intensities`:

- `none`: unchanged.
- `tic`: divide each spectrum by its total ion current (rows with TIC = 0 use divisor 1), so each
  spectrum sums to 1. TIC normalization can create artifacts when a few intense signals are confined
  to part of the section[^deininger2011].
- `log`: `log1p`. FOCUS design choice: \(\log(1+x)\) keeps zeros at zero.
- `clr`: centered log-ratio transform[^aitchison1982]. FOCUS adaptation: for each spectrum the log is
  taken over the nonzero entries only and centered by the mean log over that nonzero support;
  structural zeros are left at 0, so sparsity is preserved.
- `tic_mean_scaled`: divide each spectrum by \(f_s = T_s / \bar{T}\), where \(T_s\) is the spot's
  total ion current and \(\bar{T}\) the mean total ion current over that sample's spots for that ion
  mode (\(\bar{T}=0\) uses divisor 1; empty spots stay at 0). FOCUS design choice: `tic` multiplied
  by a per-sample constant, which keeps an interpretable absolute intensity scale. The mean is taken
  within each sample and ion mode, so it does not make intensities comparable across samples.
- `median`: divide each spectrum by the median of its intensities, which is more robust than TIC
  against intense, localized signals[^deininger2011][^fonville2012]. FOCUS adaptation: the median is
  taken over the spectrum's nonzero features, because binned centroid spectra are sparse and the
  median over all features is usually 0; spectra without signal use divisor 1. Matrix peaks are not
  excluded (Fonville et al. use the median of selected matrix-independent peaks[^fonville2012]).
- `internal_standard`: divide each spectrum by the intensity of the internal standards sprayed over
  the section, the pixel-wise normalization of quantitative MSI[^vandenbosch2023]. It is most
  effective for ions whose intensity is close to that of the standard[^barry2019]. FOCUS adaptation:
  one divisor per spectrum and ion mode, the summed raw intensity of the backbone features nearest
  each `internal_standard_mz` mass (each within \(\tau\) ppm, otherwise preprocessing stops with an
  error), instead of one class-matched standard per lipid class. FOCUS design choice: a spectrum
  whose divisor is 0 takes the 3 x 3 neighbourhood median of the nonzero divisors, then the sample
  median; a sample without the standard in any spectrum stops with an error.

Raw binned matrix is preserved in `.layers['raw']`; normalized matrix in `.X`.

---

## 6b. Per-sample clustering

After normalization, per-sample cluster labels are computed (`compute_cluster_labels`) and stored in
`.obs['cluster']`. They are consumed only by the alignment GUI for categorical spot colouring, and no
downstream algorithm uses them numerically. The routine therefore computes a fast Leiden-*like*
partition instead of an exact Leiden over every spot.

`compute_cluster_labels` groups spots with the unit planner shared with ST
([ST §3.6.1](st_methods.md#361-clustering-units)), using the cap criterion only, and builds
the same neighbourhood embedding ([ST §3.6.2](st_methods.md#362-neighbourhood-embedding)).
The modality setting `resolution` sets the neighbourhood: 1 ring for `standard`
(multicellular pixels, such as MALDI-MSI at tens of micrometres) and 2 rings for
`sub_cellular` (weak high-resolution pixels). ST clusters the units with its own steps
([ST §3.6](st_methods.md#36-per-sample-clustering)). The alignment GUI renders native spots
in both cases
([Alignment §4b](alignment_methods.md#4b-native-spot-display-and-compact-transport)).

For a sample with \(n\) spots and cap \(C = 250{,}000\):

- **\(n > C\)**: the spots are coarsened, which also aggregates the weak signal of an individual
  ultra-high-resolution MSI spot into a signal where PCA/Leiden can resolve structure. MSI
  coordinates are rotation-corrected raster centres, so the acquisition raster is inferred from
  `.obsm['spatial']` alone, and spots are grouped into k x k raster blocks with the
  smallest k that gives at most \(C\) occupied blocks. Interior blocks hold exactly k² spots, so
  block occupancy has no periodic component. When the coordinates do not sit on raster nodes,
  Voronoi supercells are used instead. All spots of a unit are **summed** into one pseudo-spot.
  Since summing breaks the per-spot normalization, the pseudo-spots are total-count normalized to
  the median (no `log1p`, matching the uncoarsened path).
- **\(n \le C\)**: no coarsening; Leiden runs on every spot, matrix used as-is.

Then feature selection, PCA, the neighbourhood embedding (each unit's PCs joined to the mean PCs of its
neighbourhood, weights 0.2 and 0.8), kNN and Leiden on that matrix. As in ST, at most 2,000 features
enter the PCA. They are the most variable ones by mean-binned normalized dispersion (Scanpy's
`highly_variable_genes`, `flavor="cell_ranger"`)[^zheng2017]. This is the ST selection step with a flavor that
does not assume log counts, because MSI intensities may be normalized by any of the
[intensity normalization](#6-intensity-normalization) methods, including CLR, which is negative. Samples with
2,000 features or fewer keep them all. Then
\(n_\text{pcs} = \min(50,\, n_\text{run}-1,\, n_\text{selected}-1)\),
`n_neighbors = min(15, n_run - 1)`, and Leiden at `resolution=0.5`, `flavor="igraph"`,
`n_iterations=2`, `directed=False`. Each unit's label then propagates back to every spot of the
unit.

The summed matrix, PCA and neighbourhood embeddings and neighbour graph live on a throwaway `AnnData` and are never
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
- `.layers['raw']`: pre-normalization binned intensities
- `.obsm['spatial']`: physical coordinates
- `.obsm['raster_coordinates']`: raster bounding boxes
- `.obs['foreground']`, `.obs['cluster']`, `.obs['sample_id']` (all categorical)
- `.obs['recalibration_ppm_<mode>']` (`float32`): applied correction \(a_{l(i)} + \delta_i\) at
  \(\bar\mu\) (§4.2), NaN in a sample without that ion mode
- `.obs['recalibration_matches_<mode>']` (`int16`): references matched at pixel level (§4.2)
- `.var['mz']`, `.var['mz_mode']`, `.var['lipid_annotation']`
- `.uns['spot_size']`: per-sample `[x,y]` (merged file stores per-sample dict)
- `.uns['recalibration_reference']`: the reference m/z per ion mode (§4.1)
- `.uns['focus_preprocessing_fingerprint']`: SHA-256 over the method version, the settings, the
  lipid database and every sample's inputs (imzML SHA-256 and `.ibd` size). Every sample depends on
  the whole dataset (shared references and backbone), so all files of one run carry the same key.

**Caching.** A run is skipped when every per-sample file and the merged file carry the current
fingerprint and the merged file holds the active samples. A changed input, setting or method
version rebuilds the dataset, and the pipeline then re-runs alignment and registration of the
modality. An interrupted run with an unchanged fingerprint resumes: cached samples are reused with
the stored references and backbone, and only the remaining samples are processed.

---

## 8. Parameters reflected by implementation

- `mass_tolerance` (default 10; must be an `int`, since `process_dataset` rejects floats)
- `frequency_threshold` (default 0.01)
- `intensity_normalization` (default `none`, in both the settings extractor `_extract_msi_settings`
  and the `process_dataset` signature)
- `recalibration_reference` (default `null`): per-ion-mode m/z lists, given as a mapping
  (`{"pos": [760.585, 798.541]}`) or as the GUI string `"pos: 760.585, 798.541; neg: 885.550"`
- `internal_standard_mz` (default `null`): same forms; m/z of the sprayed internal standards, always
  used as recalibration references and required by `internal_standard` normalization
- `min_intensity_threshold` (default 10000.0): minimum intensity of a calibrant match (§4.2)
- `detect_background` (default `false`; effective only when `lipid_annotation_db` is also set)
- `sample_type` (`tissue` or `microgrid`, default `tissue`)
- `lipid_annotation_db` (optional CSV/JSON with `db_name`, `ionized_mass`, `ion_mode`)
- `force_recomputing` (default `false`)

---

## References

[^mccann2021]: McCann A, Rappe S, La Rocca R, et al. Mass shift in mass spectrometry imaging: comprehensive analysis and practical corrective workflow. *Anal Bioanal Chem* 413:2831-2844 (2021). [doi:10.1007/s00216-021-03174-1](https://doi.org/10.1007/s00216-021-03174-1)
[^larocca2021]: La Rocca R, Kune C, Tiquet M, et al. Adaptive pixel mass recalibration for mass spectrometry imaging based on locally endogenous biological signals. *Anal Chem* 93:4066-4074 (2021). [doi:10.1021/acs.analchem.0c05071](https://doi.org/10.1021/acs.analchem.0c05071)
[^rafols2020]: Ràfols P, Heijs B, del Castillo E, et al. rMSIproc: an R package for mass spectrometry imaging data processing. *Bioinformatics* 36:3618-3619 (2020). [doi:10.1093/bioinformatics/btaa142](https://doi.org/10.1093/bioinformatics/btaa142)
[^bemis2023]: Bemis KA, Föll MC, Guo D, Lakkimsetty SS, Vitek O. Cardinal v3: a versatile open source software for mass spectrometry imaging analysis. *bioRxiv* (2023). [doi:10.1101/2023.02.20.529280](https://doi.org/10.1101/2023.02.20.529280). Peak alignment as described in the package documentation (`peakAlign`).
[^gibb2012]: Gibb S, Strimmer K. MALDIquant: a versatile R package for the analysis of mass spectrometry data. *Bioinformatics* 28:2270-2271 (2012). [doi:10.1093/bioinformatics/bts447](https://doi.org/10.1093/bioinformatics/bts447). Warping and binning as described in the package documentation (`determineWarpingFunctions`, `binPeaks`).
[^wolski2005]: Wolski WE, Lalowski M, Jungblut P, Reinert K. Calibration of mass spectrometric peptide mass fingerprint data without specific external or internal calibrants. *BMC Bioinformatics* 6:203 (2005). [doi:10.1186/1471-2105-6-203](https://doi.org/10.1186/1471-2105-6-203)
[^rousseeuw1993]: Rousseeuw PJ, Croux C. Alternatives to the median absolute deviation. *J Am Stat Assoc* 88:1273-1283 (1993). [doi:10.1080/01621459.1993.10476408](https://doi.org/10.1080/01621459.1993.10476408)
[^tukey1977]: Tukey JW. *Exploratory Data Analysis*. Addison-Wesley, Reading, MA (1977).
[^schramm2012]: Schramm T, Hester Z, Klinkert I, et al. imzML: a common data format for the flexible exchange and processing of mass spectrometry imaging data. *J Proteomics* 75:5106-5110 (2012). [doi:10.1016/j.jprot.2012.07.026](https://doi.org/10.1016/j.jprot.2012.07.026). Controlled-vocabulary terms from the imzML ontology (`imagingMS.obo`).
[^vandenbosch2023]: Vandenbosch M, Mutuku SM, Mantas MJQ, et al. Toward omics-scale quantitative mass spectrometry imaging of lipids in brain tissue using a multiclass internal standard mixture. *Anal Chem* 95:18719-18730 (2023). [doi:10.1021/acs.analchem.3c02724](https://doi.org/10.1021/acs.analchem.3c02724)
[^deininger2011]: Deininger SO, Cornett DS, Paape R, et al. Normalization in MALDI-TOF imaging datasets of proteins: practical considerations. *Anal Bioanal Chem* 401:167-181 (2011). [doi:10.1007/s00216-011-4929-z](https://doi.org/10.1007/s00216-011-4929-z)
[^fonville2012]: Fonville JM, Carter C, Cloarec O, et al. Robust data processing and normalization strategy for MALDI mass spectrometric imaging. *Anal Chem* 84:1310-1319 (2012). [doi:10.1021/ac201767g](https://doi.org/10.1021/ac201767g)
[^barry2019]: Barry JA, Ait-Belkacem R, Hardesty WM, et al. Multicenter validation study of quantitative imaging mass spectrometry. *Anal Chem* 91:6266-6274 (2019). [doi:10.1021/acs.analchem.9b01016](https://doi.org/10.1021/acs.analchem.9b01016)
[^aitchison1982]: Aitchison J. The statistical analysis of compositional data. *J R Stat Soc B* 44:139-160 (1982). [doi:10.1111/j.2517-6161.1982.tb01195.x](https://doi.org/10.1111/j.2517-6161.1982.tb01195.x)
[^zheng2017]: Zheng GXY, Terry JM, Belgrader P, et al. Massively parallel digital transcriptional profiling of single cells. *Nat Commun* 8:14049 (2017). [doi:10.1038/ncomms14049](https://doi.org/10.1038/ncomms14049). Dispersion normalization within mean bins as implemented in Scanpy (Wolf FA, Angerer P, Theis FJ. *Genome Biol* 19:15, 2018).
