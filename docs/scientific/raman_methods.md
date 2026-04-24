# Raman Spectroscopy Imaging Preprocessing Methods

Confocal Raman spectral imaging produces spatially resolved hyperspectral data cubes in which each pixel carries a full Raman spectrum. FOCUS implements a complete preprocessing pipeline for Leica LIF-format acquisitions, covering data loading and metadata parsing, wavenumber axis computation, BaSiC illumination correction, background segmentation, per-tile spectral cleaning, and final tile stitching via ASHLAR.

---

## 1. Data Format

FOCUS reads Raman data from **Leica Image Format (LIF)** files, the native output format of Leica confocal systems (TCS SP8, STELLARIS). A LIF file bundles all acquisition images and their XML metadata into a single binary container.

Each tiled acquisition is stored as a multi-dimensional array with dimensions:

- **T**: tile index (number of stage positions)
- **C**: spectral channel index (number of detector wavelength bins, `lambda_steps`)
- **Y** × **X**: spatial pixel dimensions per tile (`scan_height` × `scan_width`)

Physical tile coordinates (stage positions in µm) are extracted from the `TileScanInfo` attachment in the LIF XML tree. The pixel size (µm/pixel) is derived from the physical scan length divided by the number of pixels along each spatial axis, with unit conversion applied when the LIF reports coordinates in metres.

The spectral axis is defined by a linearly spaced array of detector wavelengths from `lambda_begin` to `lambda_end` (in nm), each linearly mapped to a Raman wavenumber (see Section 2). The pump laser wavelength $\lambda_\text{Stokes}$ is parsed from the `LaserArray/Laser[@PumpWavelength]` attribute of the hardware settings block.

When multiple sequential scan ranges are acquired (e.g. two spectral windows covering different wavenumber regions), FOCUS concatenates the spectral channels and detects overlap regions by checking for non-monotonic wavenumber sequences, trimming the overlapping portion to the nearest matching wavenumber value.

---

## 2. Wavenumber Computation

The Raman shift axis is computed from the detector wavelength array and the pump laser wavelength. Let $\lambda_\text{Stokes}$ be the pump wavelength in nm, and let $\lambda_i$ be the detector wavelengths linearly spaced from `lambda_begin` to `lambda_end` across `lambda_steps` channels:

$$\lambda_i = \lambda_\text{begin} + \frac{i}{\lambda_\text{steps} - 1}\left(\lambda_\text{end} - \lambda_\text{begin}\right), \quad i = 0, 1, \ldots, \lambda_\text{steps}-1$$

The Raman wavenumber shift for channel $i$ is:

$$\tilde{\nu}_i = \left(\frac{1}{\lambda_\text{Stokes}} - \frac{1}{\lambda_i}\right) \times 10^7 \quad [\text{cm}^{-1}]$$

where the factor $10^7$ converts from nm$^{-1}$ to cm$^{-1}$. This yields the standard Stokes shift in wavenumber units. The wavenumber array $\{\tilde{\nu}_i\}$ is stored as float32 and serves as the spectral axis for all downstream processing and the output OME-TIFF channel labels.

---

## 3. BaSiC Illumination Correction

Confocal Raman images suffer from spatially non-uniform illumination (vignetting) caused by the Gaussian profile of the focused laser beam, as well as slowly varying fluorescence background that differs across tiles and spectral channels. Both effects introduce multiplicative and additive spatial biases that must be corrected before spectral analysis.

FOCUS applies the **BaSiC** (Background and Shading Correction) algorithm [@peng2017] to each spectral channel independently. BaSiC models the observed image $I$ as:

$$I(x, y) = F(x, y) \cdot \hat{I}(x, y) + B(x, y)$$

where $F$ is the spatially smooth flatfield (illumination profile), $\hat{I}$ is the true signal, and $B$ is the background offset. BaSiC estimates $F$ and $B$ jointly from a collection of images using a regularised low-rank matrix factorisation.

**Implementation**: BaSiCpy [@peng2017] requires JAX, which may conflict with other dependencies. FOCUS therefore isolates BaSiCpy in a dedicated conda environment (`FOCUS_BaSiCpy`) and invokes it as a subprocess via `conda run`. Each spectral channel is written to a temporary NumPy `.npy` file, processed independently by the BaSiCpy script, and the corrected channel is read back. Channels are processed in parallel using a `ThreadPoolExecutor`.

After per-channel correction, the full corrected tile array is globally normalised to $[0, 1]$:

$$I'_\text{corrected} = \frac{I_\text{corrected} - \min(I_\text{corrected})}{\max(I_\text{corrected}) - \min(I_\text{corrected})}$$

**Reference**: T. Peng et al., "A BaSiC tool for background and shading correction of optical microscopy images," *Nature Communications*, 8:14836, 2017.

---

## 4. Background/Tissue Segmentation

After BaSiC correction, tissue pixels are separated from background (non-tissue regions) using a combination of dimensionality reduction, contrast enhancement, and adaptive thresholding.

### 4.1 Quick Mosaic Assembly

A rapid distance-weighted stitched image is assembled from the BaSiC-corrected tiles for segmentation purposes only. For overlapping tile regions, intensities are blended using weights derived from the distance transform of a filled rectangular tile mask:

$$W(x, y) = \text{EDT}\!\left(\mathbf{1}_{[0,H]\times[0,W]}\right)(x, y)$$

where EDT denotes the Euclidean distance transform. This ensures smooth blending at tile boundaries. The per-pixel weighted average is:

$$\text{mosaic}(x, y) = \frac{\sum_t I_t(x, y)\, W_t(x, y)}{\sum_t W_t(x, y)}$$

### 4.2 PCA-Based Grayscale Projection

The hyperspectral mosaic (shape $C \times H \times W$) is reshaped to $(H \cdot W) \times C$ and non-zero pixels are projected onto the first principal component to obtain a single grayscale representative image:

$$\mathbf{z} = \text{PCA}_1\!\left(\{I(x, y)\}_{(x,y) \in \Omega}\right) \in \mathbb{R}^{|\Omega|}$$

where $\Omega$ is the set of non-zero pixels. The resulting 1D scores are normalised to $[0, 1]$ using the 2nd–98th percentile range.

### 4.3 CLAHE Enhancement

Contrast Limited Adaptive Histogram Equalization (CLAHE) is applied to the uint8-scaled grayscale image to enhance local contrast prior to thresholding:

$$\text{clipLimit} = 2.0, \quad \text{tileGridSize} = 8 \times 8 \text{ pixels}$$

CLAHE redistributes the histogram within local tiles, amplifying low-contrast tissue structures without saturating bright regions.

### 4.4 Otsu Thresholding with Intensity Clipping

To prevent saturation artefacts from skewing the threshold, the image is clipped at the 95th intensity percentile before thresholding:

$$I_\text{clipped}(x, y) = \min\!\left(I(x, y),\; P_{95}(I)\right)$$

Otsu's method is applied to the clipped image, maximising the between-class variance:

$$\tau_\text{Otsu} = \underset{t}{\arg\max}\; \sigma^2_B(t) = \underset{t}{\arg\max}\; w_\text{bg}(t)\, w_\text{fg}(t)\, \bigl[\mu_\text{bg}(t) - \mu_\text{fg}(t)\bigr]^2$$

The threshold is then scaled by the `otsu_threshold_factor` parameter $f$ (default 0.7) and applied to the original (unclipped) grayscale image:

$$\tau_\text{eff} = f \cdot \tau_\text{Otsu}$$

A factor $f < 1$ produces a more lenient threshold, capturing faint tissue signal that would otherwise be excluded. The resulting binary mask is thresholded at $\tau_\text{eff}$.

### 4.5 Morphological Refinement

The binary segmentation mask is refined in two steps:

1. **Small object removal**: connected components with area $< \text{min\_object\_size}$ pixels (default 500) are removed using `skimage.morphology.remove_small_objects`.
2. **Hole filling**: `scipy.ndimage.binary_fill_holes` closes enclosed background regions within tissue.
3. **Contour filtering**: connected components with area $< \text{bg\_min\_area\_fraction} \times H \times W$ (default 5% of total image area) are removed to eliminate residual debris.

The final tissue mask is back-projected to the individual tile coordinates, and background pixels in all tiles are zeroed.

---

## 5. Per-Tile Spectral Processing (RamanSPy Pipeline)

Spectral cleaning is performed independently on each tile using the RamanSPy pipeline [@ramanspy]. All operations are applied pixel-by-pixel; the tile is reshaped to $(N_\text{pixels}) \times C$ before processing and restored to $(C \times H \times W)$ afterwards.

### 5.1 Zero-Variance Filtering

Before applying the pipeline, pixels with no spectral variation are excluded. The **median absolute deviation (MAD) of forward differences** is used as a measure of spectral activity:

$$\text{MAD}(\Delta s_i) = \text{median}\!\left(|\Delta s_{i,k} - \text{median}(\Delta s_i)|\right), \quad \Delta s_{i,k} = s_{i,k+1} - s_{i,k}$$

Pixels with $\text{MAD}(\Delta s_i) = 0$ (all forward differences equal, i.e. constant or flat spectra) are excluded from processing. Their output spectra remain zero-valued. This prevents numerical instability in the downstream spectral algorithms.

### 5.2 Pipeline Steps

The following four operations are applied sequentially to each non-zero spectrum:

**Step 1 — Despiking (Whitaker-Hayes algorithm)**: Cosmic ray artefacts appear as narrow, extremely intense impulse spikes in individual spectra. The Whitaker-Hayes algorithm detects these by comparing each spectral point against a modified Z-score derived from the median absolute deviation of successive differences. Detected spikes are replaced by interpolated values from neighbouring channels.

**Step 2 — Denoising (Savitzky-Golay filter)**: A polynomial of order $p$ is fitted by least squares within a sliding window of width $w$ spectral points, and the central value is replaced by the fitted polynomial value. This preserves peak shape (positions and widths) while attenuating high-frequency noise:

$$s'_k = \sum_{j=-\lfloor w/2\rfloor}^{\lfloor w/2\rfloor} h_j\, s_{k+j}$$

where $h_j$ are the Savitzky-Golay convolution coefficients for polynomial order $p$. Parameters: `savgol_window` (default 7 points), `savgol_polyorder` (default 3).

**Step 3 — Baseline correction (IASLS)**: Fluorescence background produces a broad, slowly varying additive baseline that can dominate the Raman signal. The Iterative Asymmetric Least Squares (IASLS) algorithm estimates the baseline $b_k$ by solving a penalised least-squares problem with asymmetric weights:

$$b = \underset{b}{\arg\min} \sum_k w_k (s_k - b_k)^2 + \lambda \sum_k (\Delta^2 b_k)^2$$

where $\Delta^2$ is the second-difference operator and weights $w_k$ are set higher for points where $s_k > b_k$ (baseline candidates) and lower where $s_k \leq b_k$. The weights are updated iteratively until convergence.

**Step 4 — Normalization (min-max)**: The baseline-corrected spectrum is normalised to $[0, 1]$:

$$s''_k = \frac{s'_k - \min_k s'_k}{\max_k s'_k - \min_k s'_k}$$

This removes residual intensity scaling differences between pixels and renders spectra directly comparable.

**Parallelisation**: tiles are processed concurrently across CPU cores using `joblib.Parallel`. Each tile-slice combination (one tile per spectral window) is an independent processing unit, enabling near-linear scaling with core count up to `max_workers` (default 8).

---

## 6. ASHLAR Tile Stitching

High-quality stitching of corrected tiles into a seamless mosaic requires sub-pixel registration to correct for mechanical stage positioning errors. FOCUS uses **ASHLAR** (Alignment by Simultaneous Harmonization of Layer/Modality) [@muhlich2022] for this purpose.

### 6.1 Input Preparation

Before calling ASHLAR, tiles are converted to uint8 (scaled from the float32 $[0, 1]$ range) and written as per-cycle OME-TIFF files (one file per spectral window). Physical stage coordinates are embedded in the OME metadata (`PositionX`/`PositionY` in µm). The Leica y-axis convention is inverted to match OME-TIFF convention:

$$y'_t = \max_t(y_t) - (y_t - \min_t(y_t))$$

For alignment, ASHLAR requires a reference spectral channel. FOCUS selects the **highest mean-intensity channel** in the first spectral window (cycle 0):

$$c^* = \underset{c \in [\lambda_\text{begin,0},\, \lambda_\text{end,0}]}{\arg\max}\; \frac{1}{T} \sum_{t=1}^{T} \bar{I}_{t,c}$$

where $\bar{I}_{t,c}$ is the mean intensity of tile $t$ in channel $c$.

### 6.2 Registration Algorithm

ASHLAR performs phase correlation between adjacent tile pairs in the reference channel to estimate sub-pixel translational offsets. It then computes a globally consistent registration by minimising a least-squares objective over all pairwise offset estimates, allowing for smooth elastic deformation correction across the full mosaic field.

ASHLAR is executed as a subprocess in a dedicated conda environment (`FOCUS_ASHLAR`) via `conda run`, passing the OME-TIFF input files and the reference channel index as arguments. The output is a single full-resolution stitched OME-TIFF.

**Reference**: J. L. Muhlich et al., "Stitching and registering highly multiplexed whole-slide images of tissues and tumors using ASHLAR," *Bioinformatics*, 38(19):4613-4621, 2022.

---

## 7. Output

The final output of the Raman preprocessing pipeline is a **hyperspectral OME-TIFF** in which each image channel corresponds to one wavenumber bin $\tilde{\nu}_i$. The OME-XML metadata encodes the pixel size in µm and channel names. The file can be opened in any OME-TIFF-compatible viewer (e.g. QuPath, napari with the ome-zarr plugin) or loaded into FOCUS for downstream spatial co-registration and multi-modal integration.

The output file is stored at the path defined by `MODALITY_PREPROCESSING(source_path, sample_id, modality_name, "ome.tiff")`.

---

## 8. Parameter Selection Guidance

| Parameter | Default | Effect | Recommended Values |
|---|---|---|---|
| `savgol_window` | 7 | Savitzky-Golay smoothing window width. Larger values increase smoothing but may broaden narrow peaks. | 5–11 (odd integers only) |
| `savgol_polyorder` | 3 | Savitzky-Golay polynomial order. Higher order fits sharper features. Must be less than `savgol_window`. | 2–5 |
| `otsu_threshold_factor` | 0.7 | Multiplier on the Otsu threshold for tissue segmentation. Values $< 1$ are more lenient (include more tissue). | 0.5–1.0; reduce for low-signal samples |
| `bg_min_area_fraction` | 0.05 | Minimum connected-component area as fraction of total image area. Removes small debris. | 0.01–0.1 |
| `min_object_size` | 500 | Minimum pixel count for object retention in binary cleanup. | 100–2000 (scale with pixel size) |
| `max_workers` | 8 | Number of parallel workers for BaSiC correction and spectral cleaning. | $\leq$ physical CPU core count |
