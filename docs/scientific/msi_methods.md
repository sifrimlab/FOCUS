# MSI Preprocessing Methods

## 1. Data model and inputs

FOCUS processes MSI data from imzML/IBD pairs (single mode or dual mode via `pos/` and `neg/`).

For each spectrum, metadata parsing extracts:

- raster coordinates (`position x/y`)
- physical coordinates (`3DPositionX/Y`, fallback to raster if absent)
- binary offsets and lengths for m/z and intensity arrays

Supported intensity normalization options are `none`, `tic`, and `log`.

---

## 2. Spatial coordinate correction

### 2.1 Rotation correction

Physical coordinates are rotated to reduce scan-line tilt.

Procedure (`_correct_rotation_error`):

1. Select raster x-column with maximal occupancy.
2. Fit line \(y = kx+b\) on corresponding physical points.
3. Compute \(\theta=\arctan(k)\).
4. Rotate all physical points by \(-\theta\) around their centroid.

---

### 2.2 Dual ion mode affine harmonization

When both ion modes are present:

1. Remove unpaired raster positions.
2. Fit affine mapping from positive to negative coordinates by linear regression on augmented coordinates.
3. Transform positive coordinates and combine with negative coordinates by mean.
4. Normalize coordinates to origin and apply half-raster offset.

Resulting physical coordinates are shared across modes for that sample.

---

## 3. Tissue/background detection

If `detect_background=true`, foreground candidates are estimated per spot from spectral complexity features:

- Shannon entropy of normalized intensities
- detected peak count
- log1p(TIC)
- optional annotation-hit ratio (if lipid DB provided)

Features are min-max normalized and averaged to one composite score.

### `sample_type: tissue`

- Fit 1-component and 2-component GMM
- Select with BIC
- if unimodal selected: keep all valid spots
- else: classify by posterior on higher-mean component (threshold 0.5)
- apply raster morphological cleanup (`binary_fill_holes`, then `binary_opening`)

### `sample_type: microgrid`

- Otsu threshold on normalized score histogram
- effective threshold is `min(otsu, 25th_percentile)` to avoid excessive rejection
- no morphological cleanup

Foreground mask is stored in `.obs['foreground']`; all spots remain in matrix.

---

## 4. Recalibration and m/z backbone

### 4.1 Recalibration reference selection

If no `recalibration_reference` is supplied, references are selected from sampled spectra (~30% per sample) by a score combining global frequency and cross-sample coverage (`_find_calibration_reference`), up to 5 peaks per ion mode.

### 4.2 Per-row recalibration

For each reference mass and each spectrum row, pick highest-intensity peak within tolerance and estimate offset.

Row offset is mean of available peak offsets; corrected m/z is

\[
m'_i = m_i - \Delta_{row}
\]

with missing-row offsets left unchanged.

### 4.3 Consensus backbone construction

Consensus m/z is constructed by:

1. clustering unique m/z values within tolerance (`cluster_unique_mz_chunk`)
2. merging chunk centroids (`merge_chunks`)
3. optional frequency filtering at sample level (`frequency_threshold`)
4. global merge without frequency cutoff for final backbone

Distance criterion uses ppm tolerance:

\[
\delta_{ppm}(a,b)=\frac{|a-b|}{b}\times 10^6
\]

(or mean denominator in merge step).

Parallelization uses `ProcessPoolExecutor` with adaptive chunking based on available memory and CPU count.

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

- `none`: unchanged
- `tic`: divide row by TIC (rows with TIC=0 use divisor 1)
- `log`: `log1p`

Raw interpolated matrix is preserved in `.layers['raw']`; normalized matrix in `.X`.

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
- `.obs['foreground']`, `.obs['leiden']`, `.obs['sample_id']`
- `.var['mz']`, `.var['mz_mode']`, `.var['lipid_annotation']`
- `.uns['spot_size']`: per-sample `[x,y]` (merged file stores per-sample dict)

---

## 8. Parameters reflected by implementation

- `mass_tolerance` (default 10)
- `frequency_threshold` (default 0.01)
- `intensity_normalization` (default `none` in code path)
- `recalibration_reference` (default `null`)
- `min_intensity_threshold` (default `10000.0`)
- `detect_background` (default `false` in extracted settings)
- `sample_type` (`tissue` or `microgrid`, default `tissue`)
- `lipid_annotation_db` (optional CSV/JSON with `db_name`, `ionized_mass`, `ion_mode`)
- `force_recomputing` (default false)
