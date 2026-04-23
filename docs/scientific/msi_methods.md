# MSI Preprocessing Methods

Mass spectrometry imaging (MSI) generates spatially resolved molecular profiles across tissue sections. FOCUS implements a complete preprocessing pipeline for imzML-formatted MSI data, covering file parsing, coordinate correction, cross-modal alignment, m/z axis calibration, intensity interpolation, tissue segmentation, and normalization.

---

## 1. Data Format

MSI data are stored in the imzML/IBD format pair. The **imzML** file is an XML document that encodes per-spectrum scan metadata: pixel coordinates (integer grid positions), optional physical coordinates (in µm, stored as `3DPositionX`/`3DPositionY` user parameters), and binary offset pointers for each spectrum's m/z and intensity arrays. The accompanying **IBD** file is a flat binary array containing the raw spectral data at the byte offsets referenced by the imzML metadata.

Two acquisition modes are supported:

- **Processed mode**: each spectrum stores its own m/z array (variable length). This is the mode handled by FOCUS, and is the standard output of instruments such as MALDI FTICR and MALDI Orbitrap platforms.
- **Continuous mode**: a single global m/z array is shared by all spectra (fixed length). FOCUS does not apply m/z clustering in continuous mode, as the instrument already defines a common axis.

Pixel coordinates index the laser raster grid, whereas physical coordinates record the actual stage position in µm. These two coordinate systems are in general related by a rotation and translation, since the instrument stage may not be perfectly aligned with the detector pixel grid.

FOCUS parses both coordinate systems from the imzML XML tree using the `{http://psi.hupo.org/ms/mzml}` namespace, reading pixel positions from `cvParam[@name="position x/y"]` and physical positions from `userParam[@name="3DPositionX/Y"]`.

---

## 2. Rotation Correction

Small rotational misalignment between the physical laser raster and the pixel coordinate grid introduces a shear into any spatial analysis that relies on physical coordinates. FOCUS corrects this by fitting a linear model to the laser scan lines and rotating all physical coordinates by the estimated angle.

**Algorithm** (`MsiSample._correct_rotation_error`):

1. Identify the pixel column $x^*$ that contains the largest number of acquisition points (most populated column in the raster):

$$x^* = \underset{x}{\arg\max}\; |\{i : x_i^\text{pix} = x\}|$$

2. Extract the subset of physical points $\{(x_i^\text{phys}, y_i^\text{phys})\}$ whose pixel $x$-coordinate equals $x^*$. Along a single laser scan line, all pixels share the same integer $x^\text{pix}$; hence these points sample the physical trajectory of one raster column.

3. Fit ordinary least-squares regression of $y^\text{phys}$ on $x^\text{phys}$ to obtain the slope $k$:

$$y^\text{phys} = k \cdot x^\text{phys} + b$$

4. Estimate the rotation angle:

$$\theta = \arctan(k)$$

5. Apply a clockwise rotation of angle $\theta$ around the centroid $(\bar{x}, \bar{y})$ of all physical coordinates:

$$\begin{pmatrix}x'\\y'\end{pmatrix} = R(-\theta)\begin{pmatrix}x - \bar{x}\\y - \bar{y}\end{pmatrix} + \begin{pmatrix}\bar{x}\\\bar{y}\end{pmatrix}$$

where the rotation matrix is:

$$R(\alpha) = \begin{pmatrix}\cos\alpha & -\sin\alpha\\\sin\alpha & \cos\alpha\end{pmatrix}$$

The corrected physical coordinates $\{(x'_i, y'_i)\}$ replace the raw physical coordinates in all subsequent steps. This correction assumes that the misalignment is a pure rotation (no scaling or non-linear distortion), which is a valid approximation for the small angles ($\lesssim 1^\circ$) typically encountered in MALDI instruments.

---

## 3. Dual Ion Mode Alignment

When a sample is acquired in both positive and negative ion modes (`double_ion_mode=True`), the two pixel grids may not overlap perfectly due to small differences in stage positioning between acquisitions. FOCUS aligns the two grids using an affine transformation.

**Unpaired spot removal**: Pixel coordinates present in one mode but absent from the other are first removed. The larger coordinate set is filtered by set intersection against the smaller, ensuring a one-to-one correspondence between positive and negative spectra.

**Affine alignment** (`MsiSample.initialize_sample`): Let $\{P_i\} \in \mathbb{R}^2$ and $\{N_i\} \in \mathbb{R}^2$ denote the matched positive and negative physical coordinate arrays respectively. An affine map $f : \mathbb{R}^2 \to \mathbb{R}^2$ is fitted by ordinary least squares:

$$\hat{x}^\text{neg} = A_{xx}\, x^\text{pos} + A_{xy}\, y^\text{pos} + b_x$$
$$\hat{y}^\text{neg} = A_{yx}\, x^\text{pos} + A_{yy}\, y^\text{pos} + b_y$$

where the augmented design matrix includes a bias column. Separate linear models are fitted for each output coordinate ($\hat{x}^\text{neg}$ and $\hat{y}^\text{neg}$).

The transformed positive coordinates $\{P'_i\} = f(\{P_i\})$ are then corrected for the raster half-pitch offset (to represent the centre of each pixel rather than its corner), and the final physical coordinates are taken as the element-wise mean:

$$C_i = \frac{P'_i + N_i}{2}$$

This averaged coordinate set is used for all downstream spatial operations. Coordinates are subsequently zero-shifted so that the minimum coordinate is at the origin, and half the raster pitch is added to position each coordinate at its pixel centre.

---

## 4. M/Z Calibration

### 4.1 Problem Statement

In processed-mode MSI, each spectrum is acquired independently and may exhibit a slightly different m/z axis due to instrument drift, detector non-linearity, or temperature fluctuations. Constructing a meaningful feature matrix requires a common reference m/z axis shared across all spectra in the dataset. FOCUS builds this axis via a two-stage clustering procedure: within-spectrum clustering followed by cross-spectrum merging.

### 4.2 Within-Spectrum Clustering

For each spectrum, the raw m/z values are first deduplicated and sorted. A sliding-window algorithm clusters nearby values into consensus peaks using a weighted centroid (`cluster_unique_mz_chunk`, compiled with Numba for performance).

Given a sorted array of unique m/z values $\{m_1, m_2, \ldots, m_n\}$ with occurrence counts $\{c_1, c_2, \ldots, c_n\}$, the algorithm proceeds as follows:

Starting from index $j = 1$, maintain a running weighted centroid:

$$\hat{m} = \frac{\sum_{k \in \text{cluster}} m_k c_k}{\sum_{k \in \text{cluster}} c_k}$$

A candidate value $m_{j+1}$ is merged into the current cluster if its ppm distance to the current centroid is within tolerance:

$$\delta_\text{ppm}(m_{j+1}, \hat{m}) = \frac{|m_{j+1} - \hat{m}|}{\hat{m}} \times 10^6 \leq \delta_\text{tol}$$

where $\delta_\text{tol}$ is the `mass_tolerance` parameter (default 10 ppm). When a new value is merged, the centroid is updated online. When the tolerance is exceeded, the current cluster is closed (its centroid and total weight are recorded), and a new cluster is initiated.

This procedure produces a per-spectrum **consensus list** $\{(\hat{m}_k, w_k)\}$ of cluster centroids and weights.

### 4.3 Cross-Spectrum Merging

Consensus lists from all spectra are merged pairwise using a two-pointer algorithm (`merge_chunks`). Given two sorted consensus arrays $(\hat{m}^\text{A}, w^\text{A})$ and $(\hat{m}^\text{B}, w^\text{B})$, pointers $i$ and $j$ advance simultaneously:

- If $\delta_\text{ppm}(\hat{m}^\text{A}_i, \hat{m}^\text{B}_j) \leq \delta_\text{tol}$, the two clusters are merged into a single peak with weighted average m/z and summed weight:

$$\hat{m}_\text{merged} = \frac{\hat{m}^\text{A}_i \, w^\text{A}_i + \hat{m}^\text{B}_j \, w^\text{B}_j}{w^\text{A}_i + w^\text{B}_j}, \quad w_\text{merged} = w^\text{A}_i + w^\text{B}_j$$

The ppm distance here uses the arithmetic mean of the two centroids as the denominator:

$$\delta_\text{ppm}(\hat{m}^\text{A}_i, \hat{m}^\text{B}_j) = \frac{|\hat{m}^\text{A}_i - \hat{m}^\text{B}_j|}{(\hat{m}^\text{A}_i + \hat{m}^\text{B}_j)/2} \times 10^6$$

- If $\hat{m}^\text{A}_i < \hat{m}^\text{B}_j$ (outside tolerance), the left peak is emitted unchanged and $i$ advances.
- Otherwise the right peak is emitted and $j$ advances.

Remaining entries in the longer array are appended. This merge is applied iteratively across all spectra, producing a global consensus m/z axis.

### 4.4 Frequency Filtering

After global merging, peaks observed in fewer than `frequency_threshold` $\times \max(w_k)$ cumulative acquisitions are discarded. The default `frequency_threshold = 0.01` retains peaks observed in at least 1% of the maximum accumulated weight, effectively removing noise peaks that appear in only a handful of spectra.

### 4.5 Computational Implementation

Because the global m/z array may contain millions of entries, the initial within-spectrum clustering is parallelised across CPU cores using Python's `concurrent.futures.ProcessPoolExecutor`. The unique m/z array is partitioned into overlapping chunks (5% overlap to prevent boundary artefacts), each chunk processed independently by `cluster_unique_mz_chunk`, and the results merged sequentially by `merge_chunks`. The number of chunks is determined adaptively based on available RAM and CPU core count, capped at 32.

---

## 5. Intensity Interpolation onto the Reference Grid

Once the global reference m/z axis $\{\hat{m}_j\}_{j=1}^{M}$ has been established, each spectrum's raw (m/z, intensity) pairs must be mapped onto this common axis. FOCUS employs inverse-distance-weighted (IDW) interpolation (`interpolate_single`, compiled with Numba).

For each observed peak $(m_i, I_i)$ in a spectrum, the reference peaks that fall within the ppm tolerance window are identified via binary search:

$$[\hat{m}_j] : \hat{m}_j \in \left[m_i(1 - \epsilon),\; m_i(1 + \epsilon)\right], \quad \epsilon = \delta_\text{tol} \times 10^{-6}$$

For each matching reference peak $\hat{m}_j$, the ppm distance to the observed peak is computed:

$$\delta_{ij} = \frac{|m_i - \hat{m}_j|}{m_i} \times 10^6$$

The weight assigned to reference peak $\hat{m}_j$ from observed peak $m_i$ is:

$$w_{ij} = \frac{1}{\delta_{ij} + \varepsilon_0}, \quad \varepsilon_0 = 10^{-9}$$

where $\varepsilon_0$ prevents division by zero when an observed peak falls exactly on a reference bin. The intensity contribution of peak $m_i$ is distributed across all matching reference bins proportionally to their weights:

$$\tilde{I}_j \mathrel{+}= I_i \cdot \frac{w_{ij}}{\sum_{j'} w_{ij'}}$$

This ensures that the total intensity of each observed peak is conserved and distributed to nearby reference bins in inverse proportion to their ppm distance. Reference bins that receive no contribution from any observed peak retain a value of zero. The interpolation is parallelised across spectra using `joblib.Parallel`.

---

## 6. Tissue/Background Detection

A critical preprocessing step for tissue section experiments is separating spectral-rich tissue pixels from the background matrix (glass slide, MALDI matrix spray, or mounting medium). FOCUS implements separate strategies for contiguous tissue sections (`sample_type = "tissue"`) and microfluidic grid experiments (`sample_type = "microgrid"`).

### 6.1 Per-Spot Feature Extraction

For each spot $s$, up to four scalar features are computed from its raw spectrum $\{(m_k, I_k)\}$:

**Shannon entropy** — measures spectral diversity:

$$H_s = -\sum_k p_k \log_2 p_k, \quad p_k = \frac{I_k}{\text{TIC}_s}, \quad \text{TIC}_s = \sum_k I_k$$

**Peak count** — number of detected m/z peaks:

$$N_s = |\{k : I_k > 0\}|$$

**Log-TIC** — dynamic-range-compressed total ion current:

$$T_s = \log(1 + \text{TIC}_s)$$

**DB hit ratio** (optional, requires a lipid annotation database) — fraction of observed peaks that match a known lipid within the mass tolerance:

$$R_s = \frac{|\{k : \exists\, m^* \in \mathcal{D},\, \delta_\text{ppm}(m_k, m^*) \leq \delta_\text{tol}\}|}{N_s}$$

where $\mathcal{D}$ is the set of ionized masses in the annotation database filtered by ion mode. When the annotation database is provided, this feature adds chemical specificity to the spectral complexity score.

### 6.2 Composite Score

Each feature array is min-max normalised over the set of valid spots (spots with $N_s > 0$), and the features are averaged into a scalar score:

$$\text{score}_s = \frac{1}{F} \sum_{f=1}^{F} \frac{\phi_s^{(f)} - \min_s \phi^{(f)}}{\max_s \phi^{(f)} - \min_s \phi^{(f)}}$$

where $F \in \{3, 4\}$ is the number of features.

### 6.3 Thresholding: Tissue Mode (GMM + BIC)

For tissue sections, background pixels are typically a small minority (e.g. tissue margins, holes). Otsu thresholding tends to split the dominant tissue distribution rather than separating it from background. FOCUS instead applies Gaussian mixture model (GMM) selection via the Bayesian Information Criterion (BIC):

1. Fit a 1-component GMM ($K = 1$) and a 2-component GMM ($K = 2$, with $n_\text{init} = 3$ random restarts) to the score distribution $\{\text{score}_s\}$ of valid spots.

2. Compute BIC for each model. For a GMM with $K$ components, parameters $\Theta$, and $n$ observations:

$$\text{BIC}_K = -2\ln\hat{\mathcal{L}}(\Theta) + d_K \ln n$$

where $d_K$ is the number of free parameters.

3. Model selection: if $\text{BIC}_1 \leq \text{BIC}_2$, the score distribution is unimodal and all valid spots are retained as tissue. Otherwise:

4. Classify each spot by its posterior probability on the **higher-mean** component:

$$\hat{c}_s = \underset{k}{\arg\max}\; \mu_k, \quad P(\text{tissue}\,|\,\text{score}_s) = p(\hat{c}_s\,|\,\text{score}_s; \hat{\Theta})$$

Spots with $P(\text{tissue}\,|\,\text{score}_s) \geq 0.5$ are classified as foreground.

### 6.4 Thresholding: Microgrid Mode (Otsu with Floor)

For microfluidic cell grids, cells are spatially isolated and background constitutes the majority of pixels. A conventional Otsu threshold is computed on the (0–255 scaled) score histogram:

$$\tau_\text{Otsu} = \underset{t}{\arg\max}\; \frac{w_\text{bg}(t)}{n} \cdot \frac{w_\text{fg}(t)}{n} \cdot \bigl(\mu_\text{bg}(t) - \mu_\text{fg}(t)\bigr)^2$$

To protect weak single-cell signals, the threshold is floored at the 25th percentile of valid scores:

$$\tau_\text{eff} = \min\!\left(\tau_\text{Otsu},\; Q_{25}\bigl(\{\text{score}_s\}\bigr)\right)$$

This guarantees that at most 75% of spots are discarded as background, even when single-cell signals are faint.

### 6.5 Morphological Cleanup (Tissue Mode Only)

After GMM classification, the binary foreground mask is rasterised onto the pixel coordinate grid and subjected to morphological operations to remove isolated noise pixels and fill internal holes:

1. **Hole filling**: `scipy.ndimage.binary_fill_holes` — fills enclosed background regions within the tissue mask.
2. **Binary opening**: `scipy.ndimage.binary_opening` with a $3 \times 3$ square structuring element — removes small isolated foreground islands.

Morphological cleanup is not applied in microgrid mode, as it would incorrectly merge spatially adjacent cells or fill the gaps between grid positions.

The foreground mask is stored in `.obs['foreground']` of the output AnnData object but **all spots** (foreground and background) are retained in the data matrix to allow downstream reanalysis.

---

## 7. Intensity Normalization

After interpolation, intensities are optionally normalised. Three strategies are available (parameter: `intensity_normalization`):

| Strategy | Code | Formula |
|---|---|---|
| No normalization | `"none"` | $I'_k = I_k$ |
| Total ion current | `"tic"` | $I'_k = I_k / \text{TIC}$ |
| Log-transform | `"log"` | $I'_k = \log(1 + I_k)$ |

For TIC normalization, spectra with $\text{TIC} = 0$ are left unchanged (the divisor is set to 1) to avoid division by zero. Raw (pre-normalization) interpolated intensities are preserved in `.layers['raw']`.

---

## 8. M/Z Recalibration

FOCUS implements an optional per-row (per-scan-line) recalibration step (`_recalibrate_mz_vector`) that corrects systematic m/z drift along the instrument's slow scan axis. A set of **reference m/z values** $\{r_j\}$ — selected automatically from the dataset as the most frequently observed and highest-coverage peaks — serves as an internal calibrant.

For each reference peak $r_j$ in each spectrum $s$, the highest-intensity observed peak within $\pm r_j \delta_\text{tol} \times 10^{-6}$ Da is identified (subject to an optional minimum intensity gate, `min_intensity_threshold`). The m/z offset for spectrum $s$ at reference $j$ is:

$$\delta_{sj} = m_{sj}^{\text{obs}} - r_j$$

The per-row offset is computed as the mean offset across all reference peaks for all spectra sharing the same pixel row $x$:

$$\Delta_x = \left\langle\delta_{sj}\right\rangle_{s : x_s = x,\; j}$$

Each spectrum in row $x$ has its m/z values shifted by $-\Delta_x$:

$$m'_i = m_i - \Delta_x$$

Reference selection (`_find_calibration_reference`) uses a greedy strategy that scores candidate m/z values by their global occurrence frequency weighted by cross-sample coverage, then selects up to five candidates per ion mode while ensuring all samples are covered.

---

## 9. Output Data Structure

The preprocessing pipeline produces per-sample and merged AnnData (`.h5ad`) files with the following structure:

| Slot | Content |
|---|---|
| `.X` | Normalised intensity matrix (sparse CSR, float32), shape $(N_\text{spots} \times M_\text{features})$ |
| `.layers['raw']` | Raw interpolated intensities (pre-normalization, sparse CSR, float32) |
| `.obs['foreground']` | Boolean foreground mask from tissue detection |
| `.obs['leiden']` | Leiden cluster labels (per-sample, resolution 0.5) |
| `.obsm['spatial']` | Physical coordinates in µm (float32, shape $N \times 2$) |
| `.obsm['raster_coordinates']` | Raster pixel bounding boxes (int32, shape $N \times 2 \times 2$) |
| `.var['mz']` | Reference m/z values (float32) |
| `.var['mz_mode']` | Ion mode per feature (`"pos"` or `"neg"`) |
| `.var['lipid_annotation']` | Lipid annotation string per feature (if database provided) |
| `.uns['spot_size']` | Raster pixel size in µm, shape $(2,)$ |

---

## 10. Parameter Selection Guidance

| Parameter | Default | Effect | Recommended Range |
|---|---|---|---|
| `mass_tolerance` | 10 ppm | Controls m/z clustering radius and interpolation window. Larger values merge more peaks. | 5–15 ppm for Orbitrap; 15–30 ppm for TOF |
| `frequency_threshold` | 0.01 | Minimum relative frequency for reference peak retention. Higher values yield a smaller, more reliable axis. | 0.005–0.05 |
| `intensity_normalization` | `"tic"` | Removes total ion count variation due to matrix heterogeneity. Use `"log"` for highly dynamic ranges. | `"tic"` for tissue; `"log"` for single-cell/microgrids |
| `min_intensity_threshold` | 10000.0 | Intensity gate for recalibration peak matching. Should exceed detector noise floor. | $10^3$–$10^5$ (instrument-dependent) |
| `detect_background` | `True` | Enables tissue/background segmentation. Disable for samples with uniform coverage. | `True` for tissue sections |
| `sample_type` | `"tissue"` | Selects GMM+BIC (`"tissue"`) or Otsu+floor (`"microgrid"`) segmentation strategy. | `"tissue"` for sections; `"microgrid"` for single-cell grids |
| `recalibration_reference` | `None` | External reference m/z values. If `None`, selected automatically from the dataset. | Provide known lock masses when available |
