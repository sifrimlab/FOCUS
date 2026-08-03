# Raman Spectroscopy Imaging

## Overview

Raman spectroscopy imaging records a full spectrum at every scanned position, producing a label-free, spatially resolved biochemical map of the section. FOCUS reads **tiled** hyperspectral acquisitions stored in Leica Image Format (`.lif`) files and writes one stitched, multi-channel OME-TIFF per sample.

`RamanDataset.process_dataset()` handles one sample at a time and runs five steps per sample, reported on the console and in the GUI as `1/5` … `5/5`:

| Step | Method | What it does |
|------|--------|--------------|
| `1/5` | `load_source()` | Reads the `.lif` file: tile pixel data, tile stage coordinates, pixel size, and the spectral axis |
| `2/5` | `basic_correct()` | BaSiC illumination correction, one spectral channel at a time, inside the `FOCUS_BaSiCpy` environment |
| `3/5` | `remove_background()` | Segments tissue on a quick-stitched preview mosaic and zeroes background pixels in the tiles |
| `4/5` | `process_raw_tiles()` | RamanSPy spectral cleaning per tile: despike → denoise → baseline → min-max |
| `5/5` | `ashlar_stitch()` | Stitches the tiles into the final OME-TIFF inside the `FOCUS_ASHLAR` environment |

Every step runs for every sample. No step can be switched off from the configuration; the configuration only changes the parameters listed under [Processing parameters](#processing-parameters).

!!! warning "External conda environments required"
    Steps `2/5` and `5/5` run as subprocesses in separate conda environments (`FOCUS_BaSiCpy`, `FOCUS_ASHLAR`). Both must exist before Raman data is processed.

!!! note "Per-sample failures do not stop the run"
    `process_dataset()` wraps each sample in a `try`/`except`. When a sample raises (a missing conda environment, an unreadable `.lif`, a failing ASHLAR call), the line `Error processing sample <sample_id>: <error>` is printed to the console, that sample is left out of the returned `{sample_id: path}` mapping, and processing continues with the next sample.

---

## Prerequisites

FOCUS's installation script creates the auxiliary environments automatically. It scans the
`tools/` directory and builds one `FOCUS_<Name>` environment per subfolder from that
subfolder's `requirements.txt`. No extra flag is needed:

```bash
bash install.sh          # Windows (PowerShell): .\install.ps1
```

`tools/` currently holds `BaSiCpy/` and `ASHLAR/`, so two environments are created:

| Environment | Purpose |
|-------------|---------|
| `FOCUS_BaSiCpy` | Per-channel illumination correction using BaSiCpy (JAX, CPU backend) |
| `FOCUS_ASHLAR` | Tile stitching using ASHLAR |

!!! note "Java requirement"
    ASHLAR reads the tile files through Bio-Formats, which requires Java. The installer runs
    `conda install -c conda-forge openjdk` in every tool environment it creates, including
    `FOCUS_ASHLAR`, so no system-level Java installation is needed.

Both environments are invoked by FOCUS through `conda run`; no manual activation is needed. `basic_correct()` verifies up front that `conda` is on `PATH` and that a conda environment whose path contains `FOCUS_BaSiCpy` exists, and raises `RuntimeError` if either check fails.

---

## Input format

| Format | Description |
|--------|-------------|
| `.lif` | Leica Image Format; tiled hyperspectral images with embedded XML metadata |

`load_source()` scans the modality directory and loads the **first** file whose name ends in `.lif`; if there is none, it raises `FileNotFoundError`.

Inside the file, FOCUS processes only elements that are tile scans, meaning those whose tile count is at least 2. Elements with fewer tiles, which include single-field acquisitions and any automatically stitched image saved next to the tiles, are skipped. For each processed element FOCUS reads the tile count, the per-tile stage coordinates in µm, the two spatial axis sizes and their pixel sizes in µm, the number of spectral steps, the excitation wavelength range, and the laser pump wavelength.

### Wavenumber axis

For each element the scanned wavelength axis is `numpy.linspace(lambda_begin, lambda_end, lambda_steps)` and is converted to Raman shift with

```
ν̃ = (1/λ − 1/λ_stokes) × 10⁷
```

where `λ_stokes` is the laser `PumpWavelength` from the LIF hardware settings. The factor `10⁷` converts nm⁻¹ to cm⁻¹. The result is stored as a `float32` array on `RamanImage.wavenumbers`.

The wavenumber axis is used as the spectral axis for the RamanSPy pipeline in step `4/5`. It lives in memory for the duration of the run and is **not** written to the output OME-TIFF or to any sidecar file.

### Several spectral scans in one file

When a `.lif` holds more than one tile-scan element, their tile arrays are concatenated along the channel axis in file order, their wavenumber vectors are concatenated in the same order, their tile coordinates are stacked per scan, and their pixel sizes are averaged. Each element's channel range is recorded as a *spectra slice*; those slices are the blocks that step `4/5` cleans independently and that step `5/5` writes as separate ASHLAR cycles. Concatenation requires all elements to share the same tile count and tile size.

If the concatenated wavenumber vector is not monotonic, the scans overlap spectrally. FOCUS locates the first break in monotonicity, finds the channel in the preceding block whose wavenumber is closest to the wavenumber at the break, and drops every channel from that index up to the break, removing it from the wavenumber vector, from the tile array, and from the recorded slice boundaries. It prints `Detected overlapping wavenumbers at index N. Removing overlapping region.` One such region is removed per file.

### Intensity scaling on load

Tiles are read as `float32` and rescaled once, using the maximum over the whole stack:

| Stack maximum | Action |
|---------------|--------|
| ≤ 1.0 | left unchanged |
| > 1.0 and ≤ 255.0 | divided by 255 |
| > 255.0 and ≤ 65535.0 | divided by 65535 |
| > 65535.0 | `ValueError: Expected input data in range [0, 255] or [0, 65535].` |

---

## Directory layout

Place one `.lif` file per sample inside `<sample_id>/<modality_name>/`:

```
dataset_root/
├── sample_A/
│   └── raman/
│       └── sample_A_scan.lif
├── sample_B/
│   └── raman/
│       └── sample_B_scan.lif
```

---

## Preprocessing steps

### 1/5: Load the LIF file

The LIF XML is traversed to collect, per tile-scan element, the spatial axis sizes and pixel sizes (µm), the spectral step count, the tile count, the tile stage coordinates (µm), the excitation wavelength range, and the laser pump wavelength. Tile pixel data is then read plane by plane into a `float32` array of shape `(tiles, channels, rows, columns)`, the wavenumber axis is computed, multiple scans are merged, and the intensity range is normalised as described above.

An element whose tile coordinates or pixel size are missing raises `ValueError`. An element missing its tile count, spectral step count, or a spatial axis size prints `Warning: Image '<name>' is missing required metadata. Probably corrupted scan` and is skipped.

### 2/5: BaSiC illumination correction

Each spectral channel is corrected independently for uneven illumination with [BaSiCpy](https://github.com/peng-lab/BaSiCPy). For one channel, FOCUS writes that channel's tile stack to `basic_input_<c>.npy` in the sample's preprocessing directory, runs `tools/BaSiCpy/main.py` through `conda run -n FOCUS_BaSiCpy`, waits up to 10 seconds for `basic_output_<c>.npy` to appear, loads it, and deletes both temporary files. `main.py` fits a `BaSiC` model on the channel's tiles and applies it to the same tiles.

Channels are dispatched concurrently through a `ThreadPoolExecutor` with `max_workers` threads. Once every channel is back, the whole corrected stack is min-max normalised **globally** to `[0, 1]` (a single minimum and maximum over all tiles and channels) and cached to `basic_corrected_tiles.npy`.

!!! note "CPU-only backend"
    `JAX_PLATFORM_NAME=cpu` is set both in the subprocess environment and at the top of `tools/BaSiCpy/main.py`. A GPU, if present, is not used for this step.

### 3/5: Background removal

Tissue is segmented once, on a preview mosaic, and the resulting mask is then applied to the tiles.

1. **Quick stitch.** The BaSiC-corrected tiles are placed on a common canvas using their stage coordinates divided by the pixel size. Each tile is weighted by the Euclidean distance transform of its own footprint, normalised to its maximum, so a pixel near the tile centre carries more weight than one near its border; the canvas is the weighted sum divided by the accumulated weights, per channel.
2. **PCA to grayscale.** Pixels that are zero in every channel are excluded, and the remaining channel vectors are projected onto their first principal component. The scores are clipped to their 2nd to 98th percentiles and rescaled to `[0, 1]`; the excluded pixels stay at 0.
3. **CLAHE.** The grayscale image is converted to `uint8` and equalised with `cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))`. This is the preview mosaic used by the remaining sub-steps.
4. **Otsu threshold.** Otsu's threshold is computed on a copy of the preview mosaic clipped at its 95th percentile, multiplied by `otsu_threshold_factor`, truncated to an integer, and then applied to the unclipped preview mosaic.
5. **Morphological cleanup.** Connected components (4-connectivity) of `min_object_size` pixels or fewer are removed, then holes are filled.
6. **Contour filter.** External contours are extracted; those whose area is at least `bg_min_area_fraction × (mosaic height × mosaic width)` are filled to become the tissue mask. If no contour is found at all, `Warning: No contours found; cannot refine background mask.` is printed and the mask from the previous sub-step is kept.
7. **Back-projection.** The mask is cut back into per-tile masks at each tile's mosaic position and multiplied into the BaSiC-corrected tiles, so background pixels become exactly 0 in every channel. The masked tiles are cached to `segmented_tiles.npy` and replace the BaSiC-corrected tiles for the rest of the pipeline.

### 4/5: Spectral cleaning with RamanSPy

Each `(tile, spectra slice)` pair is one work unit, so every scan's channel block is cleaned separately with its own wavenumber sub-range. Within a unit, the tile is reshaped to one spectrum per pixel and run through a fixed [RamanSPy](https://ramanspy.readthedocs.io) pipeline:

1. `despike.WhitakerHayes()`: cosmic-ray spike removal
2. `denoise.SavGol(window_length=savgol_window, polyorder=savgol_polyorder)`: Savitzky-Golay smoothing
3. `baseline.IASLS()`: improved asymmetric least-squares baseline subtraction
4. `normalise.MinMax()`: each spectrum rescaled to `[0, 1]`

Only `savgol_window` and `savgol_polyorder` are configurable; the other three steps use the RamanSPy defaults.

Before the pipeline runs, spectra whose consecutive-difference series has a median absolute deviation of exactly 0 are removed from the unit. These are flat spectra, such as background pixels zeroed in step `3/5`. The Whitaker-Hayes modified z-score divides by that MAD. Their pixels are written back as zeros. If every spectrum in a unit is flat, the unit returns all zeros without running the pipeline.

Work units are dispatched with `joblib.Parallel(n_jobs=max_workers)`. The cleaned stack is cached to `raman_corrected_tiles.npy`.

### 5/5: ASHLAR stitching

The cleaned tiles are prepared for stitching: tile *y* coordinates are mirrored within their own range (Leica → OME-TIFF convention), `NaN` values are replaced by 0, and the data is scaled by 255 and cast to `uint8`.

The alignment channel is chosen from the first spectra slice: for each channel in that slice, FOCUS takes the largest per-tile mean intensity across tiles, and picks the channel where that value is highest. It is reported as `5/5 - Stitching tiles with ASHLAR using channel <n> as reference`.

Each spectra slice is then written as one ASHLAR cycle file, `ashlar_input_cycle_<n>.ome.tiff`, holding one image per tile with `PhysicalSizeX`/`PhysicalSizeY` in µm and per-plane `PositionX`/`PositionY` in µm, zlib-compressed.

`tools/ASHLAR/main.py` runs through `conda run -n FOCUS_ASHLAR`. It sorts the cycle files by name, aligns the tiles of the first cycle with `reg.EdgeAligner` (`max_shift=15` µm) on the chosen channel, aligns every further cycle onto that result with `reg.LayerAligner`, and writes all cycles as one pyramid with `reg.PyramidWriter`. The number of pyramid levels is derived from the mosaic size so the smallest level holds at most 9,000,000 pixels (3000 × 3000). The cycle input files are deleted afterwards, and FOCUS renames `ashlar_output.ome.tiff` to the final output name.

!!! note "Square pixels"
    ASHLAR reads the physical pixel size from the cycle files and rejects inputs whose `PhysicalSizeX` and `PhysicalSizeY` differ by more than a relative tolerance of `1e-4`, with `Can't handle non-square pixels`.

---

## Processing parameters

Set under `processing_settings` for the modality. Values that are absent fall back to the defaults below, which are the class constants on `RamanImage`.

| Name | Type | Default | Used by | Description |
|------|------|---------|---------|-------------|
| `savgol_window` | `int` | `7` | `4/5` | Savitzky-Golay window length, in channels |
| `savgol_polyorder` | `int` | `3` | `4/5` | Savitzky-Golay polynomial order |
| `otsu_threshold_factor` | `float` | `0.7` | `3/5` | Multiplier applied to the Otsu threshold; below `1.0` it lowers the threshold and keeps more pixels as tissue |
| `bg_min_area_fraction` | `float` | `0.05` | `3/5` | Minimum contour area, as a fraction of the preview mosaic area, for a region to be kept as tissue |
| `min_object_size` | `int` | `500` | `3/5` | Connected components of this many pixels or fewer are removed from the mask |
| `max_workers` | `int` | `8` | `2/5`, `4/5` | Threads for BaSiC channels; `joblib` workers for spectral-cleaning units |
| `force_recomputing` | `bool` | `False` | all | Recompute every step even when the output or an intermediate cache exists |

Constraints enforced downstream, not by FOCUS: `savgol_polyorder` must be smaller than `savgol_window`, and `savgol_window` must not exceed the number of channels in a spectra slice. SciPy raises `ValueError` otherwise. The other numeric parameters are passed through unvalidated.

---

## Caching

Steps `2/5`, `3/5` and `4/5` each write their result into the sample's preprocessing directory:

| File | Written by |
|------|------------|
| `basic_corrected_tiles.npy` | `2/5` |
| `segmented_tiles.npy` | `3/5` |
| `raman_corrected_tiles.npy` | `4/5` |

When a cache file exists and `force_recomputing` is `False`, the step loads it instead of recomputing, and reports `(Using cached results)`. Step `1/5` has no cache: the `.lif` file is re-read on every run that does not already have a final OME-TIFF, because the tile coordinates, pixel size and slice boundaries are needed by the later steps.

All three caches are deleted once the sample has produced its final OME-TIFF. They therefore survive only an interrupted or failed run, which is when they let a rerun resume mid-sample.

!!! warning "Caches do not record the parameters they were computed with"
    A cache is reused whenever the file is present. Changing `savgol_window` after an interrupted run, for example, does not invalidate `raman_corrected_tiles.npy`. Set `force_recomputing: true` (or delete the sample's preprocessing directory) after changing any parameter.

---

## Output

One OME-TIFF per sample:

```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Written by | ASHLAR's `PyramidWriter`, from `tools/ASHLAR/main.py` |
| Data type | `uint8` (the tiles handed to ASHLAR are `uint8`; the in-memory tiles before that are `float32`) |
| Channels | One per spectral channel that survived overlap trimming, ordered by scan: the first scan's channels first |
| Compression | Adobe Deflate with a horizontal predictor, 1024 × 1024 tiles |
| Pyramid | Successive 2× levels, enough of them that the smallest level holds at most 9,000,000 pixels (3000 × 3000) |
| OME metadata | Creator string and `PhysicalSizeX`/`PhysicalSizeY` in µm |

The OME-XML written by ASHLAR carries no channel names and no wavenumber values, so the spectral axis cannot be recovered from the output file. No FOCUS stage reads the physical pixel size back either: alignment and registration work in Raman **pixel** coordinates.

No merged file is produced for Raman; the returned mapping contains one entry per successfully processed sample.

---

## Registration

`raman` is compatible with one registration type:

```yaml
registration_type: raman_pixel_interpolation
```

Each pixel of the stitched OME-TIFF is treated as a spot located at its pixel coordinate `(column, row)`, with the spectral channels as its feature vector. Each anchor spot receives the Gaussian-weighted average of the pixels inside its footprint. The anchor spots are the reference modality's spots, expressed in Raman pixel coordinates by the alignment stage. The footprint comes from the anchor's `uns['spot_size']`, interpreted in Raman pixels, and falls back to `[1.0, 1.0]` when the anchor carries none; it is not derived from the LIF pixel size.

Because the stitched file has no channel names, the registered features are named `Channel_0` … `Channel_<C-1>`. See [Registration](../pipeline/registration.md#raman_pixel_interpolation) for details.

---

## Config example

```yaml
modalities:
  - name: raman
    type: raman
    processing_settings:
      savgol_window: 7
      savgol_polyorder: 3
      otsu_threshold_factor: 0.7
      bg_min_area_fraction: 0.05
      min_object_size: 500
      max_workers: 8
      force_recomputing: false
    registration_type: raman_pixel_interpolation
```
