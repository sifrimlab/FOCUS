# Raman Preprocessing Methods

## 1. Input and metadata extraction

Raman preprocessing ingests Leica `.lif` files.

From LIF XML, FOCUS extracts per tiled acquisition:

- tile count and tile coordinates
- scan width/height
- spectral channel count
- wavelength bounds (`lambda_begin`, `lambda_end`)
- pump wavelength (`PumpWavelength`)
- pixel size (unit-normalized to micrometers)

Only tiled acquisitions (`tile_number >= 2`) are processed.

If multiple spectral scans are present, channels are concatenated and overlapping spectral regions are trimmed.

---

## 2. Wavenumber axis

For wavelength channel \(\lambda_i\) (nm) and pump wavelength \(\lambda_{stokes}\) (nm), implementation uses:

\[
\tilde\nu_i = \left(\frac{1}{\lambda_i} - \frac{1}{\lambda_{stokes}}\right)\times 10^7
\]

stored as float32.

---

## 3. BaSiC correction

BaSiC runs channel-wise in external conda environment `FOCUS_BaSiCpy` via `conda run`.

Flow:

1. Save per-channel temporary `.npy` arrays.
2. Run `tools/BaSiCpy/main.py` for each channel.
3. Reload corrected channels and assemble corrected tensor.
4. Global min-max normalize corrected tensor to `[0,1]`.

Channels are parallelized with `ThreadPoolExecutor(max_workers)`. The BaSiCpy subprocess is forced onto CPU by setting `JAX_PLATFORM_NAME=cpu` in its environment, so a GPU is not used for this step.

---

## 4. Background removal

Background segmentation is performed on a quick-stitched single-channel mosaic
(`_quick_stitch`), then back-projected to the tiles:

1. **Distance-transform feathered blend.** Each tile is weighted by the Euclidean distance
   transform of its footprint, normalized to its maximum,
   \(w(u,v)=\mathrm{EDT}(u,v)/\max \mathrm{EDT}\) — so pixels near a tile center dominate and seams
   are suppressed. The mosaic is the weighted sum divided by the accumulated weights,
   \(M = \bigl(\sum_t w_t\,T_t\bigr) \big/ \bigl(\sum_t w_t\bigr)\) (per channel).
2. **PCA to one component.** Over the non-zero mosaic pixels, the \(C\)-channel vectors are projected
   onto their first principal component to a scalar grayscale image, then contrast-stretched between
   the 2nd and 98th percentiles to \([0,1]\).
3. **CLAHE.** Contrast-limited adaptive histogram equalization (`clipLimit=2.0`, `tileGridSize=8×8`)
   on the uint8 grayscale.
4. **Otsu segmentation.** Clip the mosaic at its 95th percentile, compute the Otsu threshold (between-
   class-variance criterion, as in [microscopy](microscopy_methods.md#3-background-removal-remove_background)),
   then **scale it by** `otsu_threshold_factor` (default 0.7) and binarize the unclipped mosaic.
5. **Morphological cleanup.** Remove small connected components (size `min_object_size`) and fill holes.
6. **Contour area filter.** Keep external contours with area \(\ge\) `bg_min_area_fraction` × (image area);
   fill them to form the tissue mask.
7. **Back-projection.** Map the mosaic mask back to each tile's coordinates and zero the masked
   (background) regions of the BaSiC-corrected tiles.

---

## 5. Spectral cleaning (RamanSPy)

Each tile is reshaped to a list of per-pixel spectra and run through a fixed RamanSPy pipeline
(`_process_tile_parallel`), in order:

1. **Whitaker–Hayes despiking** — cosmic-ray spike removal: points whose modified
   \(z\)-score of the consecutive-difference series exceeds a threshold are flagged as spikes and
   replaced by a local mean of neighboring (non-spike) points.
2. **Savitzky–Golay denoising** — least-squares fit of a degree-`savgol_polyorder` (default 3)
   polynomial within a sliding window of `savgol_window` points (default 7), evaluated at the window
   center; smooths noise while preserving peak shape.
3. **IASLS baseline correction** — Improved Asymmetric Least Squares: estimates a smooth baseline
   \(z\) minimizing an asymmetrically weighted penalized least-squares objective
   \(\sum_i w_i (y_i-z_i)^2 + \lambda \lVert D^2 z\rVert^2\) (with extra first-derivative regularization),
   where weights \(w_i\) penalize points above the baseline far less than those below, then subtracts \(z\).
4. **Min–max normalization** — each spectrum is rescaled to \([0,1]\).

**Zero-variance spectra** (constant across channels, detected via a MAD test) are excluded **before**
processing and restored as zeros afterward, so empty/flat pixels do not distort the pipeline.

Tiles are processed in parallel with `joblib`.

---

## 6. ASHLAR stitching

Stitching runs in external environment `FOCUS_ASHLAR` via `tools/ASHLAR/main.py`.

Preparation:

- flip y-axis convention for coordinates
- convert corrected tiles to uint8
- write cycle-wise OME-TIFF inputs with physical metadata and plane positions
- choose alignment channel as highest mean-intensity channel in first spectral cycle

ASHLAR outputs a stitched OME-TIFF that is renamed to final preprocessing output path.

---

## 7. Caching and intermediates

Pipeline caches stage outputs as:

- `basic_corrected_tiles.npy`
- `segmented_tiles.npy`
- `raman_corrected_tiles.npy`

These are reused unless `force_recomputing=true`, then deleted at end of dataset processing.

---

## 8. Parameters reflected by implementation

- `force_recomputing` (default false)
- `max_workers` (default 8)
- `savgol_window` (default 7)
- `savgol_polyorder` (default 3)
- `bg_min_area_fraction` (default 0.05)
- `otsu_threshold_factor` (default 0.7)
- `min_object_size` (default 500)

---

## 9. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.ome.tiff
```

The resulting hyperspectral OME-TIFF is used downstream for alignment and, currently, spot-interpolation registration.
