# MSI Preprocessing Methods

Implementation reference for `focus/preprocessing/msi/`. `MsiDataset.process_dataset()`
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

**1. Pairing by stage position.** When every spectrum of both imzML files carries its stage
position (the `3DPositionX` and `3DPositionY` user parameters, read as the physical coordinates
\(\mathbf{r}\), rotation-corrected as in §2.1), the spectra are paired by position
(`_pair_by_stage`). Both acquisitions are in the instrument's stage frame and differ by one
translation \(\mathbf{t}\): the second polarity is acquired a fraction of a pixel away from the first.
With \(h\) the pixel pitch, the median distance from a positive spectrum to its nearest positive
neighbour, \(\mathbf{t}\) is the most common displacement from a positive spectrum to its nearest
negative spectrum, counted in cells of \(h/4\) and refined by the median of the displacements in the
most populated cell. Each positive spectrum \(i\) is paired with

\[
j(i) = \arg\min_j \bigl\lVert \mathbf{r}_{\text{neg},j} - (\mathbf{r}_{\text{pos},i} + \mathbf{t}) \bigr\rVert ,
\qquad \text{accepted when the distance is at most } h/2 ,
\]

and a negative spectrum claimed by two positive spectra goes to the closer one. Spectra without a
partner are removed. FOCUS design choice: the pairing rule. Pixel indices are not used for pairing,
because the two acquisitions can cover different areas and run their pixel axes in opposite
directions; mirroring one mode's indices maps each pixel onto its counterpart only when both modes
cover the same raster extent, and pairs spectra a constant distance apart otherwise. The cells of
\(h/4\) keep an offset of half a pitch, where two neighbours are equally near, from averaging to 0.
Each mode keeps its own pixel coordinates, which describe its own raster (scan lines in §4.2, pixel
neighbourhoods).

**2. Pairing by pixel index** (`_pair_by_pixel`), when at least one file has no stage positions. A
pixel→physical linear map is fitted for each mode by least squares,
\(\mathbf{r} \approx [\,\mathbf{p}\ \ \mathbf{1}\,]\,T\), giving \(T\in\mathbb{R}^{2\times2}\) (the
intercept row is discarded). The maps are normalized, \(\hat T = T/\lVert T\rVert_F\), and the
axis-flip \((f_x,f_y)\in\{\pm1\}^2\) is chosen to minimize the Frobenius mismatch between the
negative and positive maps,

\[
(f_x,f_y)=\arg\min_{f}\;\bigl\lVert \operatorname{diag}(f)\,\hat T_{\text{neg}} - \hat T_{\text{pos}}\bigr\rVert_F .
\]

If a flip is selected, the negative-mode pixel coordinates are mirrored about their own range. Only
pixel positions present in **both** modes are retained, and the negative indices are reordered so
paired rows share the same pixel coordinate. This pairs the right spectra only when both modes cover
the same raster extent; a difference in extent is reported as a warning.

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
(`detect_tissue_spots`). Tissue spectra are richer and more diverse than background, which is
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
(`_CalibrationReferenceSelector`), per sample and independently per ion mode, from every spectrum of
that sample: the tissue spectra when background detection ran (§3), all spectra otherwise. Selecting
calibrants from the data, instead of from an a priori list, avoids false calibrant matches that
degrade the recalibration[^larocca2021]. FOCUS design choice: one set of references per sample.
Recalibration corrects the mass shift within a sample, so each sample uses the ions its own spectra
hold; a set shared by the dataset can be nearly absent from a sample of different composition, whose
spectra then match no reference. The candidates are the sample's m/z features (§4.1.2), found by the
same detection that builds the backbone (§4.3) with a kernel half as wide (FWHM \(\tau/2\)), so that
two ions about half a tolerance apart are two candidates; the samples' mass scales are aligned
afterwards (§4.1.4). The selection rules (isolation, scoring, spread, anchoring and alignment) are FOCUS design
choices; none of them is taken from a published method.

#### 4.1.1 Logarithmic histogram

A calibrant is never recorded at exactly the same m/z twice, so peaks must be pooled within a
relative tolerance before they can be counted. Peaks are therefore indexed on a logarithmic grid.
For tolerance \(\tau\) (ppm) and \(\beta = 10\) bins per tolerance, the bin width in natural-log units
is

\[
w = \frac{\tau \cdot 10^{-6}}{\beta},
\qquad
k(m) = \left\lfloor \frac{\ln m}{w} \right\rfloor .
\]

This is the natural grid for a *relative* tolerance: for \(m_1 < m_2\) within \(\tau\) ppm,

\[
\ln m_2 - \ln m_1 = \ln\!\left(1 + \frac{m_2 - m_1}{m_1}\right) \approx \frac{m_2 - m_1}{m_1} \le \tau \cdot 10^{-6},
\]

so one uniform grid resolves the tolerance identically across the whole mass range.

Per sample and ion mode the histogram accumulates, over occupied bins only, every peak whatever its
intensity:

\[
n_b = \#\{\text{peaks in bin } b\},
\qquad
\sigma_b = \sum_{m \,\in\, b} m ,
\]

via `np.bincount` (a single linear pass, no sort), in blocks of at most \(2 \cdot 10^7\) peaks
(`peak_histogram`). The representative mass of a bin is the exact
mean \(\sigma_b / n_b\) of the raw values that fell in it; the grid only indexes peaks, it never
replaces a measured mass with a bin centre. The raw peaks are released immediately; what persists is
the occupied bins with their statistics, and the number of spectra \(M\) of the sample. Retained
memory is therefore \(O(\ln(m_{\max}/m_{\min})/w)\), bounded by the **mass span and tolerance** and
independent of the number of spectra.

#### 4.1.2 m/z features

A real ion recurs at the same m/z across the spectra of a sample, while noise centroids fall at
random m/z and form a background that varies slowly along the mass axis. A feature is therefore a
maximum of the peak density that rises significantly above its local background (`find_features`).
Peak intensity plays no part: an ion that recurs is a feature however weak it is, and noise is not,
however many centroids a spectrum holds.

1. **Density.** The counts are smoothed with a Gaussian kernel whose full width at half maximum is
   \(\tau\): \(d_b = \sum_k G_\sigma(b - k)\, n_k\), with \(\sigma = \beta / (2\sqrt{2 \ln 2})\) bins.
2. **Candidates** (`density_feature_ranges`). Every local maximum of \(d\) is a candidate \(j\). Its
   range \(R_j\) extends on each side to the lowest point of \(d\) before the neighbouring maximum
   (the valley), and at most \(\tau\) ppm (\(\beta\) bins) from its maximum.
3. **Background.** Per bin, \(\lambda_j = \max(f_j, c_j)\). The floor \(f_j\) is the running
   median[^tukey1977] of the peak counts in blocks of \(5\tau\), over \(50\tau\) on either side of the
   candidate (21 blocks; \(\pm 500\) ppm at \(\tau = 10\)), divided by the block width; blocks beyond
   the mass range are mirrored. The col \(c_j\) is the density at the valley toward each neighbouring
   maximum higher than \(j\), the larger of the two, and 0 when no neighbour is higher: the base of
   the topographic prominence of a peak, as in SciPy `peak_prominences`[^virtanen2020].
4. **Test.** With \(N_j = \sum_{b \in R_j} n_b\), the candidate is a feature when \(N_j \ge 5\) and
   \(P(X \ge N_j) < 10^{-6}\) for \(X \sim \text{Poisson}(\lambda_j |R_j|)\). Testing the count of a
   pile-up against a Poisson background rate estimated from its surroundings is the peak test of
   MACS[^zhang2008]. FOCUS adaptation: the background is the larger of a running-median floor and the
   col toward a higher neighbour, instead of the largest of several fixed-window rates.
5. **Merging.** Rejected candidates are removed and the ranges and cols recomputed, so the bins of a
   rejected maximum join its neighbours, until every candidate passes.
6. **Outputs.** A feature's m/z is the mean m/z of the peaks within \(\tau/2\) of its maximum (a
   window symmetric about the maximum and clipped to the range, so a uniform background does not pull
   it), and its presence is \(p_j = \min\bigl(1, (N_j - \lambda_j |R_j|) / M\bigr)\), the share of
   spectra holding it above the background.

FOCUS design choice, for the background: the floor follows the noise level of each mass region. The
median ignores the ions of the window as long as they occupy fewer than half of its blocks, and blocks
of \(5\tau\) hold enough noise peaks for their median not to fall below the mean, as the median of
small Poisson counts does. The col keeps a small maximum on the flank of a large ion (a noise
fluctuation, a drift tail) from becoming a feature, while the larger ion is measured against the
floor, and two ions separated by a significant dip stay two features. Without noise the floor is 0
and every ion recurring in at least 5 spectra is a feature; the frequency threshold (§4.3) decides
which are kept.

#### 4.1.3 Isolation, scoring and selection

Matching (§4.2) takes the most intense peak that passes the noise gate inside a search window of
half-width

\[
W = \max(5\tau,\ 50)\ \text{ppm}
\]

around a reference. What makes a good calibrant is therefore how this matching behaves on it, and
the selection measures that directly: the matching is replayed on the sample's spectra with each
candidate as a reference (`_replay_matching`). The candidates are the 1,000 features holding the most
peaks, plus the feature nearest each internal standard. For candidate \(j\), the window is centred on
the feature, and in each spectrum the match is a **hit** when it lies within the feature itself,
between the valleys toward its neighbouring features (so drift that spreads the ion wider than its
range is still a hit), and a **miss** when it lies elsewhere in the window: another ion was taken,
because a competitor outshone the calibrant or because the calibrant was absent. With \(h_j\) hits
and \(x_j\) misses over the \(M\) spectra, the candidate is **isolated** when
\(x_j \le 0.1\,(h_j + x_j)\), its score is \(h_j / M\), the share of spectra in which matching finds
it, and its reference mass \(\mu_j\) is the mean m/z of its hits, the sample's average position of what
matching finds. FOCUS design choice: in dense spectra most ions have neighbours within \(W\), and an
ion that wins its window in nearly every spectrum is still a sound calibrant; replaying the matching
measures how often it wins without a model of intensities, crowding or co-occurrence.

Features are ordered by descending score with **ascending \(\mu_j\) as tie-break** (a `lexsort`,
giving a total order). Two chosen references are always more than \(2W\) ppm apart, so their search
windows never overlap and no peak can match two references. Selection, with \(N_\text{ref} = 10\)
(hard-coded):

1. Internal standards (`internal_standard_mz`, when set): the feature nearest each standard mass
   within \(W\), regardless of isolation, at the mean m/z of its hits. A standard with no feature in reach is reported as not
   observed and skipped.
2. Pool: up to \(20 N_\text{ref}\) isolated features in score order, each scoring at least 0.1 times
   the best isolated feature. The score floor keeps rare peaks out of the pool.
3. Spread: the pool's \(\ln m\) span is cut into \(N_\text{ref}\) equal segments and the best pool
   member of each segment is taken, so the references cover the mass range.
4. Fill from the pool in score order until \(N_\text{ref}\) references are chosen.

When internal standards are observed, the references are moved onto the standards' mass scale.
Internal calibration against known masses is the most effective correction of mass
shift[^larocca2021], and sprayed standards cover the whole section[^vandenbosch2023]. With
\(d_s = (\mu_{j(s)} - m_s)/m_s \cdot 10^6\) the offset of standard \(s\) from its given mass
\(m_s\), every reference \(\mu\) is divided by \(1 + d(\mu)\cdot 10^{-6}\), where \(d\) is the
least-squares line through the \((m_s, d_s)\) when at least two standards span 25 % of the reference
m/z span, and \(\operatorname{median}_s d_s\) otherwise.

#### 4.1.4 Mass-scale alignment between samples

A sample's references sit at its own mean calibrant masses, so they carry its calibration offset,
and recalibration (§4.2) maps the sample onto that scale. Samples acquired at different calibrations
would keep their offsets, and one ion more than about \(\tau\) ppm apart in two samples becomes two
backbone features (§4.3) instead of one. One constant ppm shift per sample and ion mode puts the
references on a common scale (`mass_scale_shifts`). It is estimated from the samples' candidate
pools (§4.1.3, up to 200 isolated ions each), which share more ions between samples than the 10
references do. For samples \(s\) and \(t\) whose pools share at least 3 ions (one ion of each within
\(W\) ppm), \(\delta_{st}\) is the median ppm difference of the shared ions. The shifts \(c_s\)
minimize

\[
\sum_{(s,t)} k_{st}\,(c_s - c_t - \delta_{st})^2 ,
\]

with \(k_{st}\) the number of shared ions, under \(\sum_s c_s = 0\) over each group of samples
connected by such pairs, so the common scale is the samples' mean scale. Every reference \(\mu\) of
sample \(s\) is then divided by \(1 + c_s \cdot 10^{-6}\). A sample sharing fewer than 3 ions with
every other sample keeps its own scale and is reported. When an interrupted run resumes, the cached
samples' references are already on the common scale and hold \(c = 0\). When internal standards are
configured, they anchor every sample that observes them and no alignment runs. FOCUS design choice:
the median resists an ion paired with a different ion in the other sample, and the least-squares
solution uses the agreement of all sample pairs at once.

The references of each sample, after alignment, are stored in its `.uns['recalibration_reference']`
(per sample, then per ion mode, in the merged file). The per-spectrum correction derived from them is
§4.2.

### 4.2 Per-spectrum recalibration

Recalibration (`estimate_recalibration`) gives every spectrum its own m/z correction, as recommended
for MSI[^mccann2021][^larocca2021]. Each spectrum is aligned to the same reference peaks by a
per-spectrum warping function, the approach of MALDIquant `determineWarpingFunctions`[^gibb2012].
The matching rule is MALDIquant's as well: a reference matches the most intense peak inside a window
proportional to the reference mass[^gibb2012]; FOCUS also requires that peak to pass the noise gate
below. FOCUS adaptation: the error model is linear in m/z, in ppm, and is estimated in two levels,
scan line then pixel. FOCUS design choice, for this split: pooling a whole scan line gives many
matches per estimate, so the line estimate is robust to the occasional false match, and the pixel
pass, centred on the line prediction, then follows the drift where it changes along the line.

With references \(\mu_1 < \dots < \mu_R\), \(\bar\mu\) their mean, and \(l(i)\) the scan line of
spectrum \(i\), the ppm error of a match is

\[
e_{ij} = \frac{m_{ij} - \mu_j}{\mu_j} \cdot 10^{6}.
\]

**Noise gate** (`noise_threshold`, `recurrence_map`). A match must reach \(5 \cdot N_i\), with
\(N_i = 1.4826 \cdot \operatorname{MAD}\) of the intensities of the noise peaks of spectrum \(i\): its
peaks outside the sample's recurrent m/z ranges, the ranges of the sample's own features (§4.1.2)
widened by \(W\) on either side so drift does not move an ion out of them. A spectrum with fewer than
10 noise peaks has no gate. A noise estimate from the MAD of the signal is MALDIquant
`estimateNoise`, and peaks are kept above a multiple of it[^gibb2012]. FOCUS adaptation: the noise is
estimated from the centroids that do not recur instead of from the profile signal, and the multiple
is 5 instead of MALDIquant's default 2, because the centroids of noise are already local maxima of
it. FOCUS design choice: in a spectrum of mostly real ions, the MAD of all its peaks measures the
spread of the ions themselves, and a threshold on it removes most of them; the peaks that do not recur
are noise by the definition of §4.1.2, so their MAD measures the noise alone. The gate keeps chance
matches to noise out of spectra that hold no calibrant, and is relative to each spectrum, so it
follows a loss of intensity (a degrading laser, a weak region) instead of failing below a fixed
intensity.

**Scan lines.** The line axis is read from the imzML scan type (`horizontal line scan`, IMS:1000480,
or `vertical line scan`, IMS:1000481)[^schramm2012]; when absent, lines are taken as horizontal
(constant `position y`). Line indices are taken relative to the smallest coordinate, so 0-based and
1-based files are handled identically.

**Sample offset** (`sample_offset_ppm`). A sample can sit further than \(W\) from the dataset-wide
reference masses, for example after a calibration change between acquisitions. Per reference, the
peaks passing the noise gate within \(10\,W\) ppm (500 ppm at \(\tau = 10\)) are counted at every whole-ppm offset
(`offset_histogram`), and a reference supports an offset \(c\) when its peaks within \(\tau\) ppm of
\(c\) exceed, at Poisson tail probability below \(10^{-6}\), the count expected if its peaks were
spread evenly over the search range. The sample offset \(c_0\) is the offset supported by the most
references, ties going to the maximum of the summed per-reference histograms (each scaled to unit sum
and smoothed with a Gaussian kernel of FWHM \(\tau\)). The best support is then tested against
chance agreement. Other ions near each reference support offsets too, and over several references
some of them line up by chance; samples that share this chemistry then line up at the same wrong
offset. Reference \(j\) supports a share \(q_j\) of the offsets, so without a common offset the
support at one offset follows a Poisson-binomial law with probabilities \(q_j\), computed by the
recursion of Barlow and Heidtmann[^barlow1984]. Its upper tail at the best support, multiplied by
the number of independent offsets in the search range, \((20W + 1)/(2\lceil\tau\rceil + 1)\) (a
Bonferroni bound[^dunn1961]), must stay below \(10^{-3}\). Otherwise, or with fewer than 2
supporting references, \(c_0 = 0\). FOCUS design choice: the calibrants of a spectrum drift
together, so their offsets agree, while other ions near each reference fall at unrelated offsets.
With sharp calibrants and no other ions near the references, 3 agreeing references pass; 2 agree by
chance in about 2 % of samples over \(\pm 500\) ppm and do not.

**Line tracking.** The line-level matches below are first searched in a window of \(W\) ppm around
\(c_0\). Lines that do not reach an estimate are searched again around the drift of their estimated
neighbours: the per-line median of \(\tilde e_{lj}\) over the references, smoothed and filled along
the lines as below. This repeats until no further line is estimated. FOCUS design choice: drift
larger than the window, along the acquisition or across the whole sample, is followed line by line as
long as neighbouring lines differ by less than \(W\). Without it, the lines beyond the window would
take the correction of the last matched line, which leaves a stripe or a regional offset.

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
line therefore receives a correction, including lines in which no calibrant passes the noise gate.
FOCUS design choice: the instrument state drifts continuously during the
acquisition, so a line without calibrants is corrected from its neighbours instead of being left
unchanged.

**Pixel level (window \(W\)).** References are matched again in a window of \(W\) ppm centred on the
line prediction \(\hat e_{ij} = a_{l(i)} + b_{l(i)}(\mu_j - \bar\mu)\). Centring on the prediction
keeps the calibrant in reach where the drift changes along the line (laser decay within a row moves
the row ends away from the line median), and the references are isolated within \(W\) (§4.1.3), so
the window does not admit a competing peak. With \(\rho_{ij} = e'_{ij} - \hat e_{ij}\), a spectrum's
residual \(r_i\) is the median of the \(\rho_{ij}\) that lie within \(\tau\) ppm of
\(\operatorname{median}_j \rho_{ij}\), provided at least 3 of them do. FOCUS design choice: chance
matches, to noise in a spectrum without calibrants, spread over the whole window and rarely agree,
so they do not make a residual; three agreeing references are required because two chance matches
still agree often enough, in a sea of background spectra, to displace a few cells. Smoothing
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

A sample and ion mode in which fewer than half of the spectra have their own residual (3 agreeing
matches), and an ion mode with fewer than \(N_\text{ref}\) selected references, are reported as
warnings, with the recalibration summary and the selected masses.

### 4.3 Consensus backbone construction

The backbone holds one m/z feature per ion and is shared by every sample (`_BackboneBuilder`). It is
built in two stages from the recalibrated spectra: the tissue spectra when background detection ran
(§3), all spectra otherwise. Detecting the peaks of each sample, binning them across samples and
keeping the bins whose peaks occur in a minimum fraction of the spectra is the MALDIquant workflow
(`detectPeaks`, `binPeaks`, `filterPeaks` with `minFrequency`)[^gibb2012]. FOCUS adaptation: the
peaks of a sample are its m/z features (§4.1.2), found by recurrence above the local background
instead of by intensity; the frequency is measured against the sample's most present feature instead
of the number of spectra; and the binning is the density grouping below.

1. **Sample features.** Per sample and ion mode, the features of the recalibrated spectra
   (§4.1.1, §4.1.2), each with its m/z \(m_{s,j}\) and presence \(p_{s,j}\).
2. **Frequency filter.** A feature of sample \(s\) is kept when
   \(p_{s,j} \ge \texttt{frequency\_threshold} \cdot \max_{j'} p_{s,j'}\). Only the kept features'
   m/z and presence are retained.
3. **Grouping.** The kept features of all samples are placed on the grid of §4.1.1, each weighted by
   its presence, so every sample weighs alike whatever its number of spectra, and smoothed with the
   kernel of §4.1.2. Every maximum of this density is one backbone feature. It collects the sample
   features up to the lowest point of the density before each neighbouring maximum, and at most
   \(\tau\) ppm from its maximum; its m/z is their presence-weighted mean m/z. Grouping at the maxima
   of a kernel density estimate, each group extending down to the density minima on either side of
   its maximum, is the peak-density grouping of XCMS, which applies it to retention times within m/z
   slices[^smith2006]. FOCUS adaptation: the density runs along log m/z with a bandwidth set by
   \(\tau\), a group is also capped at \(\tau\) from its maximum, and every maximum becomes a
   feature; the frequency filter of step 2 replaces XCMS's minimum fraction of samples.

FOCUS design choice: noise is removed in each sample by the test of §4.1.2, before any frequency
filter, so `frequency_threshold` decides which real ions are kept and is the setting that controls
the number of features: an ion is kept when, in at least one sample, its presence is at least that
fraction of the sample's most present ion. A boundary between two features requires a dip in the
peak density, so one ion whose peaks spread wider than \(\tau\) keeps a single feature. In a microgrid
the cell ions recur only in the few cell spectra, and among all spectra they do not rise above the
background noise; their features come from the cell spectra kept by background detection (§3). Every
peak is binned into the features (§5).

Retained memory is, per sample and ion mode, the kept features' m/z and presence. The density of one
sample spans its mass range at \(\tau / 10\) ppm per bin while its features are found: about 2 million
bins (16 MB) for m/z 300 to 2,000 at \(\tau = 10\).

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

The input is the raw binned matrix `.layers['raw']`, whatever the `intensity_normalization`. For a
sample with \(n\) spots and cap \(C = 250{,}000\):

- **\(n > C\)**: the spots are coarsened, which also aggregates the weak signal of an individual
  ultra-high-resolution MSI spot into a signal where PCA/Leiden can resolve structure. MSI
  coordinates are rotation-corrected raster centres, so the acquisition raster is inferred from
  `.obsm['spatial']` alone, and spots are grouped into k x k raster blocks with the
  smallest k that gives at most \(C\) occupied blocks. Interior blocks hold exactly k² spots, so
  block occupancy has no periodic component. When the coordinates do not sit on raster nodes,
  Voronoi supercells are used instead. All spots of a unit are **summed** into one pseudo-spot.
- **\(n \le C\)**: no coarsening; Leiden runs on every spot.

Each row of that matrix (spot or pseudo-spot) is divided by the median of its nonzero values, then
`log1p`-transformed. Median normalization is more robust than TIC normalization against a few intense
signals[^deininger2011], and `log1p` is the transform the ST clustering applies to its counts
([ST §3.6](st_methods.md#36-per-sample-clustering)). FOCUS design choice: without the transform, the
principal components of raw intensities follow the absolute intensity of the few most abundant ions,
and the clusters slice their intensity levels. The median sets the `log1p` offset at a typical peak of
the row, so the transform does not depend on the instrument's intensity scale. The raw matrix is used
because every linear `intensity_normalization` (`none`, `tic`, `tic_mean_scaled`, `median`,
`internal_standard`) only rescales rows, which the median normalization replaces, and because `log1p`
needs non-negative values, which `clr` is not. The row normalization also removes the number of spots
a pseudo-spot holds.

Then feature selection, PCA, the neighbourhood embedding (each unit's PCs joined to the mean PCs of its
neighbourhood, weights 0.2 and 0.8), kNN and Leiden on that matrix. As in ST, at most 2,000 features
enter the PCA. They are the most variable ones by mean-binned normalized dispersion (Scanpy's
`highly_variable_genes`, `flavor="cell_ranger"`)[^zheng2017]. Samples with 2,000 features or fewer
keep them all. Then
\(n_\text{pcs} = \min(50,\, n_\text{run}-1,\, n_\text{selected}-1)\),
`n_neighbors = min(15, n_run - 1)`, and Leiden[^traag2019] at `resolution=0.5`, `flavor="igraph"`,
`n_iterations=2`, `directed=False`. Adjacent clusters that no density valley separates are then
merged as in ST, with the same cutoff of 0.8
([ST §3.6.3](st_methods.md#363-merging-splits-without-a-density-valley)). Each unit's label then
propagates back to every spot of the unit.

The summed matrix, PCA and neighbourhood embeddings and neighbour graph live on a throwaway `AnnData` and are never
persisted; only the per-spot label array is. Samples with fewer than 2 run-rows or fewer than 2
usable PCs, and samples resolving to a single cluster after merging, receive the single label `'0'`. PCA and Leiden
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
- `.uns['recalibration_reference']`: the sample's reference m/z per ion mode, on the common mass
  scale (§4.1); per sample, then per ion mode, in the merged file
- `.uns['focus_preprocessing_fingerprint']`: SHA-256 over the method version, the settings, the
  lipid database and every sample's inputs (imzML SHA-256 and `.ibd` size). Every sample depends on
  the whole dataset (aligned mass scales and backbone), so all files of one run carry the same key.

**Caching.** A run is skipped when every per-sample file and the merged file carry the current
fingerprint and the merged file holds the active samples. A changed input, setting or method
version rebuilds the dataset, and the pipeline then re-runs alignment and registration of the
modality. An interrupted run with an unchanged fingerprint resumes: cached samples are reused with
the stored references and backbone, and only the remaining samples are processed.

---

## 8. Parameters reflected by implementation

- `mass_tolerance` (default 10; must be an `int`, since `process_dataset` rejects floats)
- `frequency_threshold` (default 0.01): minimum presence of a sample feature, as a fraction of the
  sample's most present feature (§4.3)
- `intensity_normalization` (default `none`, in both the settings extractor `_extract_msi_settings`
  and the `process_dataset` signature)
- `recalibration_reference` (default `null`): per-ion-mode m/z lists, given as a mapping
  (`{"pos": [760.585, 798.541]}`) or as the GUI string `"pos: 760.585, 798.541; neg: 885.550"`
- `internal_standard_mz` (default `null`): same forms; m/z of the sprayed internal standards, always
  used as recalibration references and required by `internal_standard` normalization
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
[^gibb2012]: Gibb S, Strimmer K. MALDIquant: a versatile R package for the analysis of mass spectrometry data. *Bioinformatics* 28:2270-2271 (2012). [doi:10.1093/bioinformatics/bts447](https://doi.org/10.1093/bioinformatics/bts447). Peak detection, noise estimation, warping, binning and frequency filtering as described in the package documentation (`detectPeaks`, `estimateNoise`, `determineWarpingFunctions`, `binPeaks`, `filterPeaks`).
[^smith2006]: Smith CA, Want EJ, O'Maille G, Abagyan R, Siuzdak G. XCMS: processing mass spectrometry data for metabolite profiling using nonlinear peak alignment, matching, and identification. *Anal Chem* 78:779-787 (2006). [doi:10.1021/ac051437y](https://doi.org/10.1021/ac051437y). Peak-density grouping as implemented in xcms (`do_groupChromPeaks_density`).
[^wolski2005]: Wolski WE, Lalowski M, Jungblut P, Reinert K. Calibration of mass spectrometric peptide mass fingerprint data without specific external or internal calibrants. *BMC Bioinformatics* 6:203 (2005). [doi:10.1186/1471-2105-6-203](https://doi.org/10.1186/1471-2105-6-203)
[^barlow1984]: Barlow RE, Heidtmann KD. Computing k-out-of-n system reliability. *IEEE Trans Reliab* R-33:322-323 (1984). [doi:10.1109/TR.1984.5221843](https://doi.org/10.1109/TR.1984.5221843)
[^dunn1961]: Dunn OJ. Multiple comparisons among means. *J Am Stat Assoc* 56:52-64 (1961). [doi:10.1080/01621459.1961.10482090](https://doi.org/10.1080/01621459.1961.10482090)
[^zhang2008]: Zhang Y, Liu T, Meyer CA, et al. Model-based analysis of ChIP-Seq (MACS). *Genome Biol* 9:R137 (2008). [doi:10.1186/gb-2008-9-9-r137](https://doi.org/10.1186/gb-2008-9-9-r137). Peak significance against a local Poisson background rate.
[^virtanen2020]: Virtanen P, Gommers R, Oliphant TE, et al. SciPy 1.0: fundamental algorithms for scientific computing in Python. *Nat Methods* 17:261-272 (2020). [doi:10.1038/s41592-019-0686-2](https://doi.org/10.1038/s41592-019-0686-2). Peak prominence as defined for `scipy.signal.peak_prominences`.
[^rousseeuw1993]: Rousseeuw PJ, Croux C. Alternatives to the median absolute deviation. *J Am Stat Assoc* 88:1273-1283 (1993). [doi:10.1080/01621459.1993.10476408](https://doi.org/10.1080/01621459.1993.10476408)
[^tukey1977]: Tukey JW. *Exploratory Data Analysis*. Addison-Wesley, Reading, MA (1977).
[^schramm2012]: Schramm T, Hester Z, Klinkert I, et al. imzML: a common data format for the flexible exchange and processing of mass spectrometry imaging data. *J Proteomics* 75:5106-5110 (2012). [doi:10.1016/j.jprot.2012.07.026](https://doi.org/10.1016/j.jprot.2012.07.026). Controlled-vocabulary terms from the imzML ontology (`imagingMS.obo`).
[^vandenbosch2023]: Vandenbosch M, Mutuku SM, Mantas MJQ, et al. Toward omics-scale quantitative mass spectrometry imaging of lipids in brain tissue using a multiclass internal standard mixture. *Anal Chem* 95:18719-18730 (2023). [doi:10.1021/acs.analchem.3c02724](https://doi.org/10.1021/acs.analchem.3c02724)
[^deininger2011]: Deininger SO, Cornett DS, Paape R, et al. Normalization in MALDI-TOF imaging datasets of proteins: practical considerations. *Anal Bioanal Chem* 401:167-181 (2011). [doi:10.1007/s00216-011-4929-z](https://doi.org/10.1007/s00216-011-4929-z)
[^fonville2012]: Fonville JM, Carter C, Cloarec O, et al. Robust data processing and normalization strategy for MALDI mass spectrometric imaging. *Anal Chem* 84:1310-1319 (2012). [doi:10.1021/ac201767g](https://doi.org/10.1021/ac201767g)
[^barry2019]: Barry JA, Ait-Belkacem R, Hardesty WM, et al. Multicenter validation study of quantitative imaging mass spectrometry. *Anal Chem* 91:6266-6274 (2019). [doi:10.1021/acs.analchem.9b01016](https://doi.org/10.1021/acs.analchem.9b01016)
[^aitchison1982]: Aitchison J. The statistical analysis of compositional data. *J R Stat Soc B* 44:139-160 (1982). [doi:10.1111/j.2517-6161.1982.tb01195.x](https://doi.org/10.1111/j.2517-6161.1982.tb01195.x)
[^zheng2017]: Zheng GXY, Terry JM, Belgrader P, et al. Massively parallel digital transcriptional profiling of single cells. *Nat Commun* 8:14049 (2017). [doi:10.1038/ncomms14049](https://doi.org/10.1038/ncomms14049). Dispersion normalization within mean bins as implemented in Scanpy (Wolf FA, Angerer P, Theis FJ. *Genome Biol* 19:15, 2018).
[^traag2019]: Traag VA, Waltman L, van Eck NJ. From Louvain to Leiden: guaranteeing well-connected communities. *Sci Rep* 9:5233 (2019). [doi:10.1038/s41598-019-41695-z](https://doi.org/10.1038/s41598-019-41695-z)
