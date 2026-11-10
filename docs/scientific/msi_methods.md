# MSI Preprocessing Methods

Implementation reference for `focus/preprocessing/msi/`. `MsiDataset.process_dataset()`
processes the samples of one MSI modality in the order of §3, each sample represented by an
`MsiSample`.

---

## 1. Data model and inputs

FOCUS processes MSI data from imzML/IBD pairs (single mode or dual mode via `pos/` and `neg/`).

For each spectrum, metadata parsing (`_spectra_to_dict`, `_parse_imzml`) extracts:

- raster (pixel) coordinates from the `position x` / `position y` cvParams;
- physical coordinates from the `3DPositionX` / `3DPositionY` userParams;
- binary offsets and lengths for the m/z and intensity arrays, identified by their `m/z array` and
  `intensity array` cvParams (in the array or in its referenceable parameter group), then by the
  usual group ids `mzArray` and `intensities`;
- the scan type, `horizontal line scan` or `vertical line scan`, which sets the scan-line axis used by
  recalibration (§4.2). These are imzML controlled-vocabulary terms[^schramm2012].

**Physical-coordinate fallback.** True physical coordinates are not always present. Let
\(\mathbf{p}_i=(p_{i,x},p_{i,y})\) be the pixel index of spectrum \(i\) and
\(\mathbf{s}=(s_x,s_y)\) the per-axis raster size in micrometers (read from the `pixel size x/y`
scan settings, read as decimal numbers). Spectra missing `3DPosition*` have their
physical coordinate reconstructed from the pixel grid scaled to micrometers,

\[
\mathbf{r}_i = \mathbf{p}_i \odot \mathbf{s},
\]

applied either to the whole dataset (no spectrum carries physical coordinates) or only to the
affected spectra (mixed case). This keeps the inter-spot spacing physically consistent with the
half-raster centering applied later (§2.2). The pixel index is stored as \((p_y, p_x)\) and is
reordered to \((p_x, p_y)\) before the product, so each axis takes its own raster size.

**Pixel size.** A file that declares one axis only uses it for both. A file that declares none takes,
per axis, the least-squares slope of the stage position on the pixel index, and reports it; without
a pixel size and without stage positions for every spectrum, preprocessing stops with `ValueError`,
because the micrometre scale of the sample is unknown. FOCUS design choice: the pixel size is
`uns['spot_size']` and sets every downstream footprint, so it is never assumed.

**Encoding check.** The IBD arrays are read uncompressed (m/z as 32- or 64-bit floats, intensities as 32- or
64-bit floats or integers, integers cast to float32). A file whose file description, referenceable
parameter groups or first spectrum declare a binary compression other than `no compression` (zlib,
MS-Numpress) raises `ValueError`[^schramm2012]. The same terms also declare the spectrum type,
`profile spectrum` or `centroid spectrum`; §1.1 decides from the stored spectra whether the declaration
holds, and §1.2 converts profile spectra to centroids before anything else reads them.

Prior to any processing the IBD payload is validated:
a truncated file (smaller than the largest `offset + length × itemsize`) raises `ValueError`, and
spectra containing non-finite m/z or intensity values are dropped. Peaks at m/z 0 or below are
dropped at read: the logarithmic m/z grid (§4.1.1) and the ppm tolerance are undefined there.

### 1.1 Spectrum representation

Everything after this section works on peak lists, so the spectra are classified from their content
before they are used; the header is only a hint. FOCUS design choice: the content decides because
exporters mislabel files in both directions. The probe reads up to 512 evenly spaced spectra (spectra
with fewer than 8 points say nothing about the sampling and are skipped) and is deterministic.

- **Grid step.** The m/z axis is cut into 64 log-spaced bands; the grid step of a band is the median
  spacing of consecutive m/z values in it, over all probed spectra.
- **Sampled profile.** A spectrum is a sampled profile when at least 75% of its spacings lie within
  25% of the grid step of their band. Zero-trimmed profiles (stored runs of points around each peak)
  qualify, as do continuous files that share one m/z axis. A list of peaks has irregular spacings.
- **Resolution.** The sampling of a profile is judged from its largest apexes: the 10 largest apexes
  of each profile spectrum that stand at least 10 noise deviations above the baseline of §1.2. For
  each, the full width at half maximum above the baseline is measured in grid steps by interpolating
  the crossings. The median over the probe is the number of points per peak, and the median grid step
  is reported in Da.

| Probed spectra | Content | Declared `profile` | Declared `centroid` or nothing |
|---|---|---|---|
| At least 90% sampled profiles, at least 3 points per peak, grid step at most 0.1 Da | resolved profile | centroided | centroided; a warning when `centroid` was declared |
| At least 90% sampled profiles, fewer points per peak or a coarser grid | not resolved | `ValueError` with the measured values | read as a peak list, as before |
| Fewer than 10% sampled profiles | peak list | read as centroids, with a warning | read as centroids |
| Anything between | mixed | `ValueError` | `ValueError` |

FOCUS design choice for the thresholds: with fewer than 3 points across a peak the apex cannot be
located from its neighbours (the fit below needs one point on each flank and a curvature), so such a
file is refused instead of picked unreliably. A regular grid coarser than 0.1 Da is not a profile at
any resolving power used in imaging, and a ladder of isotopes in a centroid file has that regularity.
A profile stored with one point per peak is indistinguishable from a peak list and is read as one.

### 1.2 Centroiding of profile spectra

A resolved profile is converted to centroids once per run, in the loading step, so that validation,
the registration of the two polarities and every later read see a peak list. FOCUS adaptation of the
standard local-maximum peak detection (MALDIquant `detectPeaks` with a noise-scaled threshold and
`estimateNoise` from the median absolute deviation[^gibb2012][^rousseeuw1993]); the grid-aware baseline
and the merging rule below are FOCUS design choices.

1. **Runs.** Two consecutive points are neighbours when they are closer than 1.5 grid steps of their
   band; a larger spacing ends a run.
2. **Baseline and noise, per band.** The median and \(1.4826 \cdot \mathrm{MAD}\) of the signal over
   the sampling grid of the band, where the grid positions without a stored point count as zeros. A
   sparse or zero-trimmed profile therefore gets baseline and noise 0, and a dense profile a real
   estimate. Values are interpolated between band centres in log m/z; a band without enough grid
   positions takes its nearest band.
3. **Apexes.** A local maximum of a run (a plateau gives its lowest-m/z point) is a peak when its height
   above the baseline is positive and at least 3 noise deviations[^long1983]. Unlike a vendor export,
   the noise maxima above that limit are kept: the noise gates of §4.2 and §6 are calibrated on
   centroids that include them, and about one maximum in a thousand points of pure noise passes.
4. **One peak per ion.** Two apexes of a run are one peak unless the signal between them falls below
   half of the lower one above the baseline; the higher apex is kept. This merges a noisy flat top and
   unresolved shoulders, which binning would otherwise add twice (§8).
5. **Position and height.** A Gaussian through the apex and its two neighbours (a parabola on the
   logarithm of the signal above the baseline, valid for any spacing); a parabola on the signal when a
   neighbour is not above the baseline; the apex point itself when a neighbour is not on the grid. The
   centroid intensity is the fitted apex height above the baseline, never lower than the apex point.
   FOCUS design choice: height and not area, as in vendor centroids, because the area of a peak grows
   with its width, which grows with m/z at constant resolving power, and would reweight the
   normalizations of §9 and the signal-to-noise limits of §4.2 and §6.

The conversion reads the profile once, in batches of at most 4 million points, with one thread per
available CPU; the batches are written in spectrum order, so the result does not depend on the number
of threads. The centroids are stored in a scratch IBD (m/z float64, heights float32) next to the
sample's outputs, deleted when the run ends or fails, and never part of the output. Spectra with
non-finite values or arrays of different lengths cannot be picked and are dropped with the corrupt
spectra of §1. The step reports the points per peak and the mean number of peaks per spectrum.

Limits. The fit locates a symmetric peak to within 3% of its width at 3 to 10 points per peak and a
strongly skewed one (right flank twice as wide) to within 15%. The picker is not the instrument
vendor's centroiding, which can use lock masses or peak-shape models; an export of centroided spectra
remains the most faithful input.

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

**1. Content registration** (`pairing.register`). The positive positions are registered onto the
negative ones from the spectral content of both acquisitions, so already paired acquisitions,
stage frames shifted or rotated between the two runs (a reloaded slide) and partially overlapping
scans are handled by one procedure. Stage positions (`3DPositionX`, `3DPositionY`, rotation-corrected
as in §2.1) are the physical coordinates \(\mathbf{r}\) when every spectrum of both files carries
them; otherwise the pixel grid scaled by the raster size is used, and the search also covers the
mirrored handedness, because pixel axes can run in opposite directions in the two files.

*Content.* During the binary scan (§1) every spectrum's intensity is summed in 48 log-spaced m/z
bands from 50 to 3000. Per polarity the band composition \(\log(1 + 10^4\, b_k / \sum_k b_k)\) and the
log total are standardized, and their first 6 principal components (fitted on a seeded subsample of
30,000 spectra) are the channels of that polarity.

*Images.* The channels are rasterized on a grid of cell \(c\): each spectrum is splatted bilinearly
onto its four nearest cell centres, which leaves no moire of empty and doubled cells on a rotated
lattice, and each image is high-passed by subtracting its masked Gaussian low-pass (normalized
convolution[^knutsson1993], \(\sigma = \min(8h, L/6)\) with \(h\) the pixel pitch and \(L\) the shorter
footprint side). Smooth fields correlate at any pose; fine structure correlates only at the true one.

*Score.* For a rotation \(\theta\) (and handedness) of the positive positions about their centroid,
every translation \(\mathbf{t}\) is scored at once in the Fourier domain by the masked normalized
cross-correlation[^padfield2012] \(r_{ij}(\mathbf{t})\) between positive channel \(i\) and negative
channel \(j\), computed only where both footprints overlap. The score is sign-invariant and
multi-channel, because the two polarities share anatomy but not intensity relations (a region bright
in one polarity can be dark in the other):

\[
z(\theta,\mathbf{t}) = \Bigl(\tfrac{1}{K^2}\textstyle\sum_{i,j} r_{ij}(\mathbf{t})^2\Bigr)^{1/2}
\sqrt{n(\mathbf{t})/n_{\min}} ,
\]

the root mean squared correlation over the \(K^2\) channel pairs (an RV-type association between
the two channel sets[^robert1976]), weighted by the root of the overlap \(n\) relative to the smaller
footprint \(n_{\min}\). The weight keeps the global search off small chance overlaps; poses
overlapping less than 5% of the smaller footprint are not scored.

*Search.* (i) Coarse: cells of \(4h\) (at most \(L/24\)), every 3 degrees over the full circle.
(ii) Rotation refinement on cells of \(2h\), \(\pm 3\) degrees every 0.25 degrees, translations within
two coarse cells of the coarse pose and without the overlap weight (a local search compares poses of
nearly equal overlap); the window follows the best angle while it lies on its edge, and a parabola
through the best angle and its neighbours gives the final value. (iii) Tile fit at native resolution:
the overlap is cut into tiles of \(24h\), each tile's residual shift is searched within \(4h\), and a
rotation and translation are fitted to the tile shifts by weighted least squares (weights: tile peak
scores); tiles further than \(1.5h\) from the fit are dropped and the fit repeated, up to three
times. Whole-image angle sweeps are flat for small rotations; tile shifts constrain the angle by the
lever arm across the overlap. (iv) Lattice snap: lattice offsets are periodic in the pitch, so in each tile the displacements
from the mapped positive positions to their nearest negative ones are averaged as phases,
\(\arg \langle e^{2\pi i\, d/h} \rangle\) per axis. When at least half the tiles (and at least
three) have a phase coherence \(|\langle e^{2\pi i\, d/h}\rangle| \ge 0.8\) on both axes
(commensurate lattices), the tile offsets are unwrapped against their nearest neighbours and a
rotation and translation are fitted to them, twice. The snap lands commensurate lattices exactly on
each other and settles a half-pitch displacement on one consistent side; rotated lattices are left
at the tile-fit pose. On the MEL acquisition the content places the negative spots half a row
(about 10 µm) from the positive ones although both files record the same stage positions; either
neighbouring row is an equally valid partner, and the snap pairs every spectrum with the same one.

*Verification.* The coarse pose is accepted when its score exceeds by a factor 1.3 every competing
pose: the best translation more than 8 coarse cells away at the same rotation, the best pose at any
rotation more than 10 degrees away, and the best pose of the other handedness. The coarse search is
repeated with the even and with the odd components; both poses must map the positive positions
within 2 coarse cells (median) of the final pose. A pose failing either test stops the sample with
`PolarityPairingError`: no pairing is produced rather than a wrong one. FOCUS design choice: the
procedure, the band content, the score and the thresholds, calibrated in the table below.

*Stage fallback* (`MsiCoordinates._stage_pose`), for `sample_type: cell_culture` only. A culture
varies along smooth gradients and its fine structure is mostly scan-line stripes, which differ
between the polarities, so the content cannot fix a pose: the high-passed score has no distinct
peak, and without the high pass the gradients match at any rotation. When the verification fails on
a culture whose two files both carry stage positions, the spectra are paired by the recorded stage
positions and a warning reports that the content does not verify the pairing. The pose maps each
positive position to the negative frame through the recorded stage frame: the positive scan-line
rotation correction (§2.1) is undone and the negative one applied, each fitted as the rigid map from
the mode's recorded stage positions to its corrected coordinates. Every other sample type, and a
culture without stage positions, stops as above. FOCUS design choice: a culture is acquired with
both polarities in one stage frame far more often than a section is reloaded between them, and a
section's structure keeps the content verification for the cases where the frames differ. On
`19FULL` (a culture, 20 µm rasters tilted about 18 degrees and 1.5 degrees apart, partially
overlapping) the fallback pairs 119,896 spectra with a median residual of 7.9 µm.

*Pairing* (`pairing.pair`). Each mapped positive spectrum is paired with the nearest negative
spectrum within half a pixel diagonal; a negative spectrum claimed twice goes to the closer positive
one. Spectra without a partner are removed. When stage positions are present and the registered pose
differs from the recorded stage pose by more than one pitch (median), a detail line reports that the
recorded stage frames are not shared. Each mode keeps its own pixel coordinates, which describe its own raster
(scan lines in §4.2, pixel neighbourhoods).

Calibration on the two dual-polarity acquisitions of `test_data` (MEL: shared stage frame; MEL2: NEG
stage frame recorded about 1.7 mm away, same raster and tissue pixels) and transformed copies of MEL.
"Correct" counts pairs whose two spectra share the pixel index in the source data.

| Case | Registered pose | Margin | Pairs | Consistent |
|---|---|---|---|---|
| MEL as acquired | 0.00 deg, (0.0, 0.0) µm | 4.49 | 182,865 | 100% |
| MEL, NEG shifted by (+2500, −300) µm | 0.00 deg, (+2500.0, −300.0) µm | 4.49 | 182,865 | 100% |
| MEL, NEG rotated 7 deg and shifted | 7.00 deg, (+800.0, +400.0) µm | 3.71 | 182,865 | 100% |
| MEL, NEG rotated −90 deg | −90.00 deg, (+100.0, +50.0) µm | 4.75 | 182,865 | 100% |
| MEL, 20% overlap, shifted | 0.00 deg, (−1200.0, +680.0) µm | 1.83 | 36,199 | 99.99% |
| MEL, 40% overlap, rotated 3 deg | 3.00 deg | 2.93 | 73,032 | 100% |
| MEL2 as acquired | 0.00 deg, (+1716.2, −116.0) µm | 3.03 | 131,282 | 100% |
| MEL2, 30% overlap | 0.00 deg, (+1716.2, −116.0) µm | 2.15 | 38,707 | 100% |
| MEL POS with MEL2 NEG (different tissue) | stopped | 1.17 | none | |
| MEL2 POS with MEL NEG (different tissue) | stopped | 1.02 | none | |

"Consistent" is the share of pairs with the most common pixel-index offset, so a half-row choice
(MEL, 20% overlap: one row from the recorded offset) counts as consistent. The stage-position pairing
used before paired 71,325 MEL2 spectra 172 pixels apart and dropped 46% of them. The rotated cases
rotate both lattices; the tests (`tests/msi/test_dual_polarity_pairing.py`) also cover a tissue
rotated under two axis-aligned rasters, where every pair lies within one pitch of the true partner.
Registration adds 20 to 30 s per dual-polarity sample of 130,000 to 180,000 spectra.

**2. Affine harmonization.** A 2D affine map is fitted from positive to negative physical
coordinates on augmented inputs \(A=[\,\mathbf{r}_{\text{pos}}\ \ \mathbf{1}\,]\) (one least-squares
fit per output axis), and applied to the positive coordinates. The shared coordinate is the
midpoint of the transformed positive and the negative sets,

\[
\mathbf{r}_i^{\text{shared}} = \tfrac{1}{2}\bigl(\mathcal{A}(\mathbf{r}_{\text{pos},i}) + \mathbf{r}_{\text{neg},i}\bigr).
\]

**3. Origin normalization and centering.** Coordinates are shifted so the minimum is the origin,
then offset to the raster-cell center by \(\mathbf{s}/2\) (a half-raster shift, restricted to the
longer axis for non-square rasters).

The resulting physical coordinates are shared across modes for that sample.

---

## 3. Processing order

| Step | Spectra | Output |
|---|---|---|
| 1. Loading MSI data | all | coordinates, ion mode pairing (§1, §2) |
| 2. Selecting recalibration references | all | per-sample calibrants (§4.1) |
| 3. Aligning sample mass scales | - | calibrants on a common mass scale (§4.1.4) |
| 4. Recalibrating spectra and selecting features | all, then foreground | per-spectrum recalibration (§4.2), foreground (§5), per-sample features (§6) |
| 5. Building global m/z backbone | - | shared features (§7) |
| 6. Annotating features | - | `var['lipid_annotation']` (§6.2) |
| 7. Binning intensities | all | binned and normalized matrices (§8, §9), cluster labels (§10) |
| 8. Merging samples | - | merged file (§11) |

Steps 2, 4 and 7 each read the sample's IBD once. Step 4 is one pass: the spectra are recalibrated,
the foreground is detected on the recalibrated m/z, and the sample's features are selected from its
foreground spectra. FOCUS design choice, for this order: calibrants are selected from every spectrum
before any foreground is known, so matrix and background ions present on the whole slide are eligible
calibrants (on a microgrid the cells cover a few percent of the spectra), and foreground detection
compares ions at their recalibrated m/z, so a drifting ion is one ion.

---

## 4. Recalibration

The mass measured for one ion varies from pixel to pixel. Local composition, section thickness and
matrix deposition are sources of this mass shift, and recalibrating every spectrum against internal
calibrants reduces it[^mccann2021][^larocca2021]. Spectral alignment and recalibration are part of
published MSI processing tools[^rafols2020][^bemis2023]. FOCUS selects calibrants
from the data (§4.1) and estimates one correction per spectrum (§4.2); the shared m/z backbone is
built from the recalibrated spectra (§6, §7).

### 4.1 Recalibration reference selection

If no `recalibration_reference` is supplied, reference masses are selected automatically
(`_CalibrationReferenceSelector`), per sample and independently per ion mode, from every spectrum of
that sample, before its foreground is detected (§3). Selecting
calibrants from the data, instead of from an a priori list, avoids false calibrant matches that
degrade the recalibration[^larocca2021]. FOCUS design choice: one set of references per sample.
Recalibration corrects the mass shift within a sample, so each sample uses the ions its own spectra
hold; a set shared by the dataset can be nearly absent from a sample of different composition, whose
spectra then match no reference. The candidates are the sample's m/z features (§4.1.2), found by the
same detection that selects the sample features (§6.1) with a kernel half as wide (FWHM \(\tau/2\)), so that
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
and every ion recurring in at least 5 spectra is a feature; the frequency threshold (§6.1) and the S/N filter (§6.3) decide
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
backbone features (§7) instead of one. One constant ppm shift per sample and ion mode puts the
references on a common scale (`mass_scale_shifts`). It is estimated from the samples' candidate
pools (§4.1.3, up to 200 isolated ions each), which share more ions between samples than the 10
references do; the samples are compared in dataset order. For samples \(s\) and \(t\) whose pools share at least 3 ions (one ion of each within
\(W\) ppm), \(\delta_{st}\) is the median ppm difference of the shared ions. The shifts \(c_s\)
minimize

\[
\sum_{(s,t)} k_{st}\,(c_s - c_t - \delta_{st})^2 ,
\]

with \(k_{st}\) the number of shared ions, under \(\sum_s c_s = 0\) over each group of samples
connected by such pairs, so the common scale is the samples' mean scale. Every reference \(\mu\) of
sample \(s\) is then divided by \(1 + c_s \cdot 10^{-6}\). A sample sharing fewer than 3 ions with
every other sample keeps its own scale and is reported. Every sample stores its pool on its own scale
and its shift \(c_s\) (`uns['recalibration_pool']`). When an interrupted run resumes, the cached
samples enter the least squares with their stored pools and their shifts held fixed, in dataset
order, so the remaining samples receive the shifts of a fresh run: the fresh solution minimizes the
same sum, and holding part of it at that solution leaves the rest optimal. When internal standards are
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
Matching a reference within a window proportional to the reference mass is MALDIquant's rule as
well[^gibb2012]. FOCUS adaptation: the match is the most intense peak of the window that passes the
noise gate below, instead of the closest peak; the references are isolated within the window
(§4.1.3), so the most intense peak is the calibrant, and a noise peak closer to the reference mass is
not taken. FOCUS adaptation: the error model is linear in m/z, in ppm, and is estimated in two levels,
scan line then pixel. FOCUS design choice, for this split: pooling a whole scan line gives many
matches per estimate, so the line estimate is robust to the occasional false match, and the pixel
pass, centred on the line prediction, then follows the drift where it changes along the line.

With references \(\mu_1 < \dots < \mu_R\), \(\bar\mu\) their mean, and \(l(i)\) the scan line of
spectrum \(i\), the ppm error of a match is

\[
e_{ij} = \frac{m_{ij} - \mu_j}{\mu_j} \cdot 10^{6}.
\]

**Noise gate** (`spectrum_noise`, `noise_threshold`, `recurrence_map`). A match must reach
\(\tilde N_i + 5 \cdot N_i\), with \(\tilde N_i\) the median and
\(N_i = 1.4826 \cdot \operatorname{MAD}\) of the intensities of the noise peaks of spectrum \(i\): its
peaks outside the sample's recurrent m/z ranges, the ranges of the sample's own features (§4.1.2)
widened by \(W\) on either side so drift does not move an ion out of them. A spectrum with fewer than
10 noise peaks has no gate. A noise estimate from the MAD of the signal is MALDIquant
`estimateNoise`, and peaks are kept above a multiple of it[^gibb2012]; the median is the location of
the robust estimate[^rousseeuw1993]. FOCUS adaptation: the noise is
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
Foreground detection (§5), the sample features (§6) and the intensity matrix (§8) therefore see the
same correction. The applied correction at \(\bar\mu\) and the number of
pixel-level matches are written to `.obs` (§7).

A sample and ion mode in which fewer than half of the spectra have their own residual (3 agreeing
matches), and an ion mode with fewer than \(N_\text{ref}\) selected references, are reported as
warnings, with the recalibration summary and the selected masses.

## 5. Foreground detection

Foreground detection (`MsiForeground.detect`) runs on the recalibrated spectra of every ion mode
and keeps the union of the per-mode foregrounds. It always runs and needs no lipid database;
`sample_type` sets the detector and its spatial cleanup (`focus.preprocessing.msi.technology`). The
foreground is stored in `.obs['foreground']` and the score in `.obs['foreground_score']`; **all**
spectra remain in the matrix. A sample in which no foreground is found keeps every spectrum with
peaks as foreground, with a warning.

| `sample_type` | Experiment | Score | Classifier | Cleanup |
|---|---|---|---|---|
| `tissue` | section on a clean or empty background | recurrent ions (§5.1) | mixture (§5.2) | fill holes, 3 x 3 opening |
| `tissue_noisy` | section on a background rich in noise centroids | recurrent ions, 3 x 3 median on the raster | mixture | fill holes, 5 x 5 opening, components below 10 % of the largest removed |
| `cell_culture` | uniform culture, domains along gradients | recurrent ions | mixture | 3 x 3 majority vote |
| `microgrid` | isolated cells of about one pixel in matrix | local outliers (§5.4) | hysteresis | none |

### 5.1 Recurrent-ion score

The recurrent ions of a sample and ion mode are its m/z features over every spectrum (§4.1.2). For
spectrum \(i\), with \(\tilde N_i\) and \(N_i\) the median and 1.4826 MAD of its noise peaks (§4.2),

\[
\text{score}_i = \ln\bigl(1 + \#\{j : \text{spectrum } i \text{ holds a peak of feature } j
\text{ with intensity} \ge \tilde N_i + 3 N_i\}\bigr)
\]

(`detections_per_spectrum`); a spectrum without a noise estimate counts every feature peak. The
threshold is the 3-sigma detection limit[^long1983]. FOCUS design choice: noise centroids never recur,
so they add nothing to the score however many a spectrum holds, and a background of matrix ions holds
only those ions, below the tissue that holds them and its own. Entropy, peak count and TIC, the
features of the previous detector, rise on a background of noise centroids, which holds more peaks
and a flatter intensity distribution than tissue.

### 5.2 Mixture classification

A 1- and a 2-component Gaussian mixture are fitted to the scores of the spectra with peaks
(\(n_\text{init} = 3\) for two components) and compared by BIC[^schwarz1978]. Every such spectrum is
foreground when

- BIC prefers one component;
- the two components overlap: Ashman's \(D = \sqrt{2}\,|\mu_1-\mu_2| / \sqrt{s_1^2+s_2^2} < 2\), the
  criterion for a clean separation of two Gaussians[^ashman1994];
- the lower component holds more than half of the recurrent ions of the higher one, \(e^{\mu_1}-1 >
  0.5\,(e^{\mu_2}-1)\).

Otherwise a spectrum is foreground when its posterior of the higher-mean component is at least 0.5.
FOCUS design choice, for the last two rules: BIC also prefers two components when two tissue regions
differ in their number of ions (about 350 and 390 ions per spectrum in two regions of the section
of [preprocessing audit §9](../development/preprocessing_audit.md#9-msi-preprocessing-redesign)), and a
background holds a small subset of the tissue ions. A background is separated only when the score
modes are clean and far apart.

### 5.3 Spatial cleanup

The mask is rasterized on the pixel grid. Hole filling and opening with a square structuring
element are morphological operators[^soille2004]; a majority vote is a rank filter[^soille2004]. FOCUS
design choice, per preset:

- `tissue`: holes are filled and a 3 x 3 opening removes isolated pixels, because a section is one
  contiguous object.
- `tissue_noisy`: the score is median-filtered over 3 x 3 pixels before classification, the opening
  is 5 x 5 and connected components below 10 % of the largest one are removed, because isolated
  background spectra with a few chance detections are frequent.
- `cell_culture`: a 3 x 3 majority vote (a pixel takes the class of at least 5 of the 9 pixels of
  its neighbourhood) without hole filling. A gap between cells wider than about two pixels stays
  background, and a single misclassified pixel takes the class of its neighbours.

### 5.4 Microgrid cells

A microgrid acquisition covers the whole slide, so background spectra are the large majority and a
cell covers one or a few spectra (`detect_microgrid_spots`). The detection models the background and
flags the spectra that depart from it.

1. **Background ions**: the recurrent ions (§5.1) present in at least half of the spectra, over their
   whole feature range: matrix, sprayed standards, slide coating. They are found on recalibrated
   m/z, so drift does not spread a background ion over several m/z ranges.
2. **Spot features**: \(\ln(1 + \text{cell intensity})\) and the cell fraction of the TIC, the cell
   intensity being the summed intensity of the peaks outside the background ions. TIC is left out:
   on a microgrid it is dominated by matrix ions, and ion suppression can make it lower on cells.
3. **Local background**: each spot feature is rasterized and the median over a 9 x 9 window is
   subtracted, removing deposition gradients and line drift; the residuals are converted to robust
   z-scores (median and 1.4826 MAD[^rousseeuw1993]) and averaged.
4. **Hysteresis**[^canny1986]: seeds need \(z \ge \max(4,\ \Phi^{-1}(1 - 0.01/n))\), so pure noise
   gives 0.01 expected seeds over the \(n\) spectra; spectra at \(z \ge 2\) that are 8-connected to a
   seed join it. Components above 25 spectra are reported (debris, matrix crystals) and kept.

FOCUS design choice: the background model, the local median and the thresholds. A cell is foreground
without spatial cleanup, which would remove one-pixel cells.

---

## 6. Per-sample feature selection

The features of each sample and ion mode are selected from its foreground spectra
(`select_sample_features`) in the order frequency, annotation, intensity, so intensity never decides
which ions are candidates and an annotated ion is never removed by the intensity filter. Detecting
the peaks of each sample, filtering them by frequency and binning them across samples is the
MALDIquant workflow (`detectPeaks`, `filterPeaks` with `minFrequency`, `binPeaks`)[^gibb2012]. FOCUS
adaptation: the peaks of a sample are its m/z features (§4.1.2), found by recurrence above the local
background instead of by intensity; the frequency is measured against the sample's most present
feature instead of the number of spectra; the intensity filter follows the frequency filter.

### 6.1 Frequency

The features of the foreground spectra (§4.1.1, §4.1.2) are kept when
\(p_{s,j} \ge 0.01 \cdot \max_{j'} p_{s,j'}\). Peak intensity plays no part. FOCUS design choice: the
fraction 0.01 is fixed. Noise is removed by the recurrence test (§4.1.2) and recurrent ions at the
noise level by the S/N filter (§6.3), so the frequency rule only trims rare ions: in a microgrid, the
cell ions recur in the cell spectra only, which are the foreground.

### 6.2 Annotation

With a `lipid_annotation_db`, every frequent feature is annotated with the database entries of its
ion mode whose ionized mass lies within \(\tau\) ppm (`annotate_masses`). A plain ppm window without
isotope or adduct logic matches noise in a large database. The number of annotations expected by
chance is estimated by annotating the features again at masses shifted by \(\pm 3, \pm 5, \pm 7\)
window widths (\(2\tau\) ppm each) and averaging the matched features (`chance_annotations`), a
target-decoy estimate[^elias2007]; METASPACE controls annotation FDR with decoy adducts[^palmer2017].
FOCUS adaptation: decoy mass shifts instead of decoy adducts, and an estimate instead of an FDR
threshold. When more than half of the annotations are expected by chance a warning reports that
annotated features are not a reliable exemption from §6.3. The backbone (§7) is annotated again on its
merged m/z.

### 6.3 S/N filter

The filter compares the foreground intensities of each feature with a noise profile measured on the
**background** spectra of the same sample (`snr_filter`), so the foreground never enters its own
noise estimate.

- **Noise profile** (`noise_profile`): the non-recurrent peaks (outside the recurrence map of §4.2, so
  matrix ions are excluded) of the background spectra are counted per block of \(5\tau\) of the
  feature grid and per level of \(\log_{10}\) intensity. At feature \(j\), \(\mu_N(m_j)\) and
  \(\sigma_N(m_j)\) are the median and 1.4826 MAD of the noise peaks in the blocks within \(50\tau\) of
  it, the layout of the local floor of §4.1.2, with intensities taken as uniform in log within a level.
- **Test**: for every foreground spectrum holding feature \(j\), the most intense peak in its range
  gives \((I - \mu_N(m_j)) / \sigma_N(m_j)\). The feature is kept when the median over those spectra is
  at least 3, the 3-sigma detection limit[^long1983], tested as "at least as many spectra at or above 3
  as below". A signal-to-noise threshold over a MAD noise estimate is MALDIquant
  `detectPeaks`[^gibb2012]. FOCUS adaptation: the threshold applies to a feature over the spectra
  holding it instead of to a peak, and the noise comes from other spectra.
- **Exemptions**: annotated features (§6.2) and features whose window holds fewer than 50 noise peaks
  are kept.
- **Skipped**: the filter does not run, every feature is kept and the reason is reported, when the
  detector separated no background (§5.2), when there are fewer than \(\max(100,\ 0.01\,n)\)
  background spectra, or when no feature window holds 50 noise peaks.
- **Report**: the removed features and their share of the foreground TIC; a share above 5 % is a
  warning that the background may not represent the noise of the foreground.

FOCUS design choice: centroided data are already thresholded by the instrument software, so in a
clean sample the few non-recurrent peaks of a tissue spectrum are weak real ions; their MAD measures
signal, and a per-spectrum estimate would remove real features. The background holds no tissue ions,
so it measures the noise alone, and a sample without one is not filtered. Chemical noise is usually
higher on tissue than off tissue, so a background profile errs toward keeping features.

### 6.4 Feature width

The width of feature \(j\) is \(\sigma_j = \text{IQR}_j / 1.349\) in ppm (`feature_widths`), the
interquartile range of the histogram bins of its range weighted by their peaks above the local floor
\(f_j\) (§4.1.2), each bin's peaks taken as uniform over the bin; \(\text{IQR}/1.349\) equals the
standard deviation for normal scatter[^rousseeuw1993]. FOCUS design choice: subtracting the floor
keeps the noise of the range from widening it. The width sets how close features share a peak
(§8); on m/z stored on a sampling grid it is quantized to that grid.

Retained per sample and ion mode: the kept features' m/z, presence, width and annotation flag.

---

## 7. Backbone construction

The backbone holds one m/z feature per ion and is shared by every sample (`_BackboneBuilder`,
`merge_features`). The kept features of all samples (§6) are pooled per ion mode and sorted, and every
group whose span exceeds \(\tau\) ppm is split at its largest gap between consecutive features,
recursively, until every group spans at most \(\tau\) (`split_groups`). Grouping sorted peak masses
by recursive splitting at the largest gap until the members agree within a tolerance is MALDIquant
`binPeaks`[^gibb2012]. FOCUS adaptation: the criterion is the span of the group instead of each
member's deviation from the group mean, so no two merged features are more than \(\tau\) apart. Per
group:

\[
m = \frac{\sum_k p_k m_k}{\sum_k p_k},
\qquad
\sigma^2 = \frac{\sum_k p_k \bigl(\sigma_k^2 + d_k^2\bigr)}{\sum_k p_k},
\qquad d_k = \frac{m_k - m}{m}\,10^6 ,
\]

with \(p_k\) the presence of member \(k\), so every sample weighs alike whatever its number of
spectra, and \(\sigma\) clipped to \([\tau/10,\ \tau/2.3548]\) (one grid bin to a FWHM of \(\tau\)). A
group is annotated when any member was. FOCUS design choice: every sample feature lands in exactly
one group, so no feature is lost, while one ion seen a few ppm apart in two samples becomes one
feature, its width including the spread between samples. The m/z and width are stored as
`var['mz']` and `var['mz_sigma_ppm']` (float32), and binning (§8) uses the stored values.

---

## 8. Intensity binning

Each spectrum is binned onto the backbone of its ion mode (`bin_spectra_csr`, numba, chunks of
spectra in parallel via `joblib`). Peak \(p\) at m/z \(m_p\) with intensity \(I_p\) reaches every
feature \(j\) within \(\tau\) ppm with the Gaussian weight

\[
G_{pj} = \exp\!\Bigl(-\tfrac12 \bigl(d_{pj} / \sigma_j\bigr)^2\Bigr),
\qquad d_{pj} = \frac{m_p - m_j}{m_j}\,10^6 ,
\qquad
X_{ij} = \sum_{p \in i} I_p\,\frac{G_{pj}}{\sum_k G_{pk}} ,
\]

the sum running over the features within \(\tau\) ppm of the peak, and peaks with no feature in
reach are dropped. The fraction is the responsibility of feature \(j\) for peak \(p\) in a mixture
of Gaussians of equal weights, the E-step of the EM algorithm[^dempster1977], and grouping peaks
with a Gaussian kernel along the mass axis is the density grouping of XCMS[^smith2006]. FOCUS
adaptation: the widths are the features' own (§6.4) and the responsibilities split each peak's
intensity instead of estimating the mixture. FOCUS design choice: a feature alone in reach takes
every peak in reach whole, whatever its mass error, so a residual calibration error never scales an
intensity; features closer than the tolerance share a peak in proportion to their weights, so the
share moves continuously with the peak's position instead of switching from one feature to the
other; and the intensity of every peak in reach is conserved. A kernel that also attenuates an
isolated feature's peaks with their mass error was evaluated and rejected: on centroids stored on
the instrument's sampling grid (5 ppm steps in the data of
[preprocessing audit §9](../development/preprocessing_audit.md#9-msi-preprocessing-redesign)), the
per-line recalibration offsets moved the peaks against the Gaussian and produced line stripes and up to
19 % intensity loss. The kernel writes CSR rows directly, without a dense \(n \times F\) matrix. The
binned matrix is `.layers['raw']`.

---

## 9. Intensity normalization

Every method divides each spectrum by one divisor at a fixed scale (`normalize_intensities`),
independently per sample and ion mode, before the positive and negative blocks are concatenated.
Samples share the scale by construction, so the per-sample files equal the rows of the merged file
without a pass over the whole dataset. A spectrum without signal keeps divisor 1.

- `none` (default): unchanged.
- `tic`: the total ion current over the features divided by their number \(F\), so a spectrum sums
  to \(F\) and its mean feature is 1, the scale of Cardinal `normalize(method = "tic")`[^bemis2015].
  TIC normalization can create artifacts when a few intense signals are confined to part of the
  section[^deininger2011].
- `median`: the median of the spectrum's nonzero features, so a typical peak is 1; more robust than
  TIC against intense, localized signals[^deininger2011][^fonville2012]. FOCUS adaptation: the median
  is taken over the nonzero features, because binned centroid spectra are sparse; matrix peaks are not
  excluded (Fonville et al. use the median of selected matrix-independent peaks[^fonville2012]).
- `rms`: \(\sqrt{\sum_j X_{ij}^2 / F}\), the root mean square: the vector norm divided by
  \(\sqrt{F}\). Vector norm normalization is one of the methods Deininger et al. evaluated for
  MALDI imaging[^deininger2011], and Cardinal offers it as `rms`[^bemis2015].
- `internal_standard`: the summed binned intensity of the backbone features nearest each
  `internal_standard_mz` mass (each within \(\tau\) ppm, otherwise preprocessing stops with an error),
  the pixel-wise normalization of quantitative MSI[^vandenbosch2023]. It is most effective for ions
  whose intensity is close to that of the standard[^barry2019]. FOCUS adaptation: one divisor per
  spectrum and ion mode instead of one class-matched standard per lipid class. FOCUS design choice: a
  spectrum whose divisor is 0 takes the 3 x 3 neighbourhood median of the nonzero divisors, then the
  sample median; a sample without the standard in any spectrum stops with an error.

The settings `log`, `clr` and `tic_mean_scaled` were removed: log and centred log-ratio
transforms[^aitchison1982] apply downstream, and `tic` now has a scale shared by every sample.
The normalized matrix is `.X`.

---

## 10. Per-sample clustering

After normalization, per-sample cluster labels are stored in `.obs['cluster']`. They colour spots
in the alignment GUI only. MSI runs the clustering shared with ST, described in
[Alignment Clustering](spatial_clustering.md), through `label_sample`:

- **Mode.** From `sample_type`: `domains` for `tissue` and `tissue_noisy`, ordered `gradient` levels
  for `cell_culture`, `foreground` for `microgrid`
  ([Alignment Clustering §1](spatial_clustering.md#1-presets)).
- **Spectra.** Foreground spectra with at least one peak (§4); background spectra are
  `unassigned` and take no part in any unit.
- **Aggregation.** `resolution: standard` clusters native pixels (summed into k x k raster blocks
  only above 250,000 spectra); `resolution: sub_cellular` sums pixels into raster blocks until the
  median block detects 200 features. `microgrid` never aggregates.
- **Neighbourhood.** 1 ring for `resolution: standard`, 2 rings for `sub_cellular`, edge-preserving
  ([Alignment Clustering §4](spatial_clustering.md#4-neighbourhood-embedding-and-partition));
  none for `microgrid`.
- **Representation.** The normalized spectra `.X` (§9) averaged over each unit, then `log1p`;
  `intensity_normalization: none` divides each summed spectrum by the median of its nonzero
  features instead ([Alignment Clustering §3](spatial_clustering.md#3-representation)).

`.obs['clustering_eligible']` records eligibility, `.uns['focus_clustering']` the provenance
(including `sample_type`) and `.uns['cluster_colors']` the colours of the categories. The merged
file keeps the per-sample provenance in `.uns['focus_clustering'][sample_id]` and the colours of
the merged categories. The alignment GUI renders native spots
([Alignment §4b](alignment_methods.md#4b-native-spot-display-and-compact-transport)).

---

## 11. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.h5ad
```

Merged:

```text
{dataset_path}/merged/preprocessing/{modality}_merged_processed.h5ad
```

Key fields:

- `.X`: normalized intensities (float32 CSR, §9)
- `.layers['raw']`: binned intensities before normalization (float32 CSR, §8)
- `.obsm['spatial']`: physical coordinates
- `.obsm['raster_coordinates']`: raster bounding boxes
- `.obs['foreground']`, `.obs['cluster']`, `.obs['sample_id']` (all categorical)
- `.obs['foreground_score']` (`float32`): the detector's score (§5.1, robust z for microgrids)
- `.obs['recalibration_ppm_<mode>']` (`float32`): applied correction \(a_{l(i)} + \delta_i\) at
  \(\bar\mu\) (§4.2), NaN in a sample without that ion mode
- `.obs['recalibration_matches_<mode>']` (`int16`): references matched at pixel level (§4.2)
- `.var['mz']`, `.var['mz_sigma_ppm']` (float32, §7), `.var['mz_mode']`, `.var['lipid_annotation']`
- `.uns['spot_size']`: per-sample `[x,y]` (merged file stores per-sample dict)
- `.uns['recalibration_reference']`: the sample's reference m/z per ion mode, on the common mass
  scale (§4.1); per sample, then per ion mode, in the merged file
- `.uns['recalibration_pool']`: per ion mode, the calibrant pool on the sample's own scale and its
  mass-scale shift (§4.1.4); per sample in the merged file
- `.uns['focus_preprocessing_fingerprint']`: SHA-256 over the method version, the settings, the
  fixed calibration of `focus.preprocessing.msi.technology`, the lipid database and every sample's
  inputs (imzML SHA-256 and `.ibd` size). Every sample depends on the whole dataset (aligned mass
  scales and backbone), so all files of one run carry the same key.

Per-sample and merged files are written to a temporary file and renamed, so an interrupted write
leaves no partial cache. The merged file is concatenated on disk and its per-sample `.uns` entries are
written into it in place.

**Caching.** A run is skipped when every per-sample file and the merged file carry the current
fingerprint and the merged file holds the active samples. A changed input, setting or method
version rebuilds the dataset, and the pipeline then re-runs alignment and registration of the
modality. An interrupted run with an unchanged fingerprint resumes: cached samples are reused with
their stored references, pools and backbone, and only the remaining samples are processed, with the
outputs of a fresh run (§4.1.4; the backbone is binned from its stored float32 values in both cases).

---

## 12. Parameters reflected by implementation

- `mass_tolerance` (default 10, ppm, int or float): feature kernel (§4.1.2), backbone merge span
  (§7), binning reach (§8) and annotation window (§6.2)
- `sample_type` (`tissue`, `tissue_noisy`, `cell_culture`, `microgrid`; default `tissue`): foreground
  detection and cleanup (§5), clustering neighbourhood (§10)
- `intensity_normalization` (`none`, `tic`, `median`, `rms`, `internal_standard`; default `none`, §9)
- `recalibration_reference` (default `null`): per-ion-mode m/z lists, given as a mapping
  (`{"pos": [760.585, 798.541]}`) or as the GUI string `"pos: 760.585, 798.541; neg: 885.550"`
- `internal_standard_mz` (default `null`): same forms; m/z of the sprayed internal standards, always
  used as recalibration references and required by `internal_standard` normalization
- `lipid_annotation_db` (optional CSV/JSON with `db_name`, `ionized_mass`, `ion_mode`)
- `resolution` (`standard`, `sub_cellular`): clustering preset (§10)
- `force_recomputing` (default `false`)

Removed in `msi-13`, rejected by configuration validation with the reason: `detect_background`
(detection always runs and `sample_type` calibrates it), `frequency_threshold` (fixed, §6.1),
`min_intensity_threshold` (replaced by the S/N filter, §6.3), and the normalizations `log`, `clr`
(use `none`) and `tic_mean_scaled` (use `tic`).

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
[^long1983]: Long GL, Winefordner JD. Limit of detection: a closer look at the IUPAC definition. *Anal Chem* 55:712A-724A (1983). [doi:10.1021/ac00258a724](https://doi.org/10.1021/ac00258a724)
[^schwarz1978]: Schwarz G. Estimating the dimension of a model. *Ann Stat* 6:461-464 (1978). [doi:10.1214/aos/1176344136](https://doi.org/10.1214/aos/1176344136)
[^ashman1994]: Ashman KM, Bird CM, Zepf SE. Detecting bimodality in astronomical datasets. *Astron J* 108:2348 (1994). [doi:10.1086/117248](https://doi.org/10.1086/117248)
[^soille2004]: Soille P. *Morphological Image Analysis: Principles and Applications*, 2nd ed. Springer, Berlin (2004). [doi:10.1007/978-3-662-05088-0](https://doi.org/10.1007/978-3-662-05088-0)
[^canny1986]: Canny J. A computational approach to edge detection. *IEEE Trans Pattern Anal Mach Intell* PAMI-8:679-698 (1986). [doi:10.1109/TPAMI.1986.4767851](https://doi.org/10.1109/TPAMI.1986.4767851). Hysteresis thresholding.
[^elias2007]: Elias JE, Gygi SP. Target-decoy search strategy for increased confidence in large-scale protein identifications by mass spectrometry. *Nat Methods* 4:207-214 (2007). [doi:10.1038/nmeth1019](https://doi.org/10.1038/nmeth1019)
[^palmer2017]: Palmer A, Phapale P, Chernyavsky I, et al. FDR-controlled metabolite annotation for high-resolution imaging mass spectrometry. *Nat Methods* 14:57-60 (2017). [doi:10.1038/nmeth.4072](https://doi.org/10.1038/nmeth.4072)
[^bemis2015]: Bemis KD, Harry A, Eberlin LS, et al. Cardinal: an R package for statistical analysis of mass spectrometry-based imaging experiments. *Bioinformatics* 31:2418-2420 (2015). [doi:10.1093/bioinformatics/btv146](https://doi.org/10.1093/bioinformatics/btv146). Normalization as described in the package documentation (`normalize`, methods `tic` and `rms`).
[^dempster1977]: Dempster AP, Laird NM, Rubin DB. Maximum likelihood from incomplete data via the EM algorithm. *J R Stat Soc B* 39:1-22 (1977). [doi:10.1111/j.2517-6161.1977.tb01600.x](https://doi.org/10.1111/j.2517-6161.1977.tb01600.x)
[^padfield2012]: Padfield D. Masked object registration in the Fourier domain. *IEEE Trans Image Process* 21:2706-2718 (2012). [doi:10.1109/TIP.2011.2181402](https://doi.org/10.1109/TIP.2011.2181402)
[^robert1976]: Robert P, Escoufier Y. A unifying tool for linear multivariate statistical methods: the RV-coefficient. *J R Stat Soc C* 25:257-265 (1976). [doi:10.2307/2347233](https://doi.org/10.2307/2347233)
[^knutsson1993]: Knutsson H, Westin CF. Normalized and differential convolution. *Proc IEEE Conf Comput Vis Pattern Recognit*, 515-523 (1993). [doi:10.1109/CVPR.1993.341081](https://doi.org/10.1109/CVPR.1993.341081)
