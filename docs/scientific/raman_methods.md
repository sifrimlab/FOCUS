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

Channels are parallelized with `ThreadPoolExecutor(max_workers)`.

---

## 4. Background removal

Background segmentation is performed on a quick-stitched mosaic (`_quick_stitch`):

1. Blend tiles with distance-transform weights.
2. PCA to 1 component over non-zero mosaic pixels.
3. Contrast enhancement with CLAHE.
4. Clip at 95th percentile and compute Otsu threshold.
5. Scale threshold by `otsu_threshold_factor` (default 0.7).
6. Remove small components (`min_object_size`) and fill holes.
7. Keep contours with area >= `bg_min_area_fraction * image_area`.
8. Back-project mask to tiles and zero masked regions.

---

## 5. Spectral cleaning (RamanSPy)

Per tile-slice pipeline (`_process_tile_parallel`):

- Whitaker-Hayes despiking
- Savitzky-Golay denoising (`savgol_window`, `savgol_polyorder`)
- IASLS baseline correction
- min-max normalization

Zero-variance spectra are detected and excluded before processing; excluded spectra are restored as zeros.

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
