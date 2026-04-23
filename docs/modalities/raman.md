# Raman Spectroscopy

## Overview

Raman Spectroscopy Imaging provides label-free, spatially resolved biochemical maps of tissue sections based on inelastic photon scattering. FOCUS processes tiled hyperspectral cubes acquired on Leica confocal systems and exported as Leica Image Format (`.lif`) files.

The preprocessing pipeline reads tile metadata and spectral axis information directly from the LIF XML, applies BaSiC illumination correction per spectral channel, performs background/tissue segmentation on the quick-stitched mosaic, applies a per-tile spectral cleaning pipeline (despiking, denoising, baseline correction, normalisation via RamanSPy), and stitches the corrected tiles into a final hyperspectral OME-TIFF pyramid using ASHLAR.

!!! warning "External conda environments required"
    Two steps (BaSiC correction and ASHLAR stitching) run in separate conda environments. These must be installed before processing Raman data.

---

## Prerequisites

FOCUS ships an installation script that creates the required conda environments:

```bash
bash install.sh --raman
```

This creates two environments:

| Environment | Purpose |
|-------------|---------|
| `FOCUS_BaSiCpy` | Per-channel illumination correction using BaSiCpy (JAX/CPU backend) |
| `FOCUS_ASHLAR` | Multi-cycle tile stitching using ASHLAR |

!!! note "Java requirement"
    ASHLAR requires **Java 21** at runtime. Ensure `java` is on `PATH` before running the pipeline.

Both environments are invoked by FOCUS as subprocesses via `conda run`; no manual activation is needed.

---

## Input Format

| Format | Description |
|--------|-------------|
| `.lif` | Leica Image Format; contains tiled hyperspectral images with embedded XML metadata |

FOCUS auto-detects tile count, tile coordinates (µm), scan dimensions, spectral range (`lambda_begin`, `lambda_end`), spectral step count, and pump laser wavelength from the LIF XML. Wavenumbers are computed as:

```
Raman_shift (cm⁻¹) = (1/λ_emission − 1/λ_pump) × 10⁷
```

When multiple spectral scans are present in one LIF file (e.g. segmented acquisitions), FOCUS concatenates them along the channel axis and resolves any overlapping wavenumber regions automatically.

---

## Directory Layout

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

## Preprocessing Steps

1. **LIF metadata parsing** — the LIF XML is traversed to extract scan height, scan width, spectral step count, tile count, tile stage coordinates (µm), pixel size (µm/pixel), and wavelength range for each tiled image element. Non-tiled entries (single-field acquisitions) are ignored.

2. **Wavenumber computation** — the spectral axis is converted from detector wavelength to Raman wavenumber (cm⁻¹) using the pump laser wavelength (`lambda_stokes`) read from the LIF hardware settings. The resulting `float32` array of length `N_channels` is stored with the dataset.

3. **BaSiC illumination correction** — raw tiles are corrected channel-by-channel for uneven illumination using [BaSiCpy](https://github.com/peng-lab/BaSiCPy). Each channel is written to a temporary `.npy` file, corrected in the `FOCUS_BaSiCpy` environment, and reloaded. Channels are processed in parallel up to `max_workers` threads. Corrected tiles are globally normalised to `[0, 1]` and cached to disk.

4. **Background / tissue segmentation** — the BaSiC-corrected tiles are quickly stitched using distance-transform blending. PCA is applied to the `(H×W, C)` spectral matrix to obtain a single-component grayscale image, which is enhanced with CLAHE. Otsu thresholding with a multiplicative factor (`otsu_threshold_factor`) determines the tissue mask. Small objects below `min_object_size` pixels are removed; holes are filled; contours smaller than `bg_min_area_fraction × image_area` are discarded. The tile masks are back-projected from the mosaic and applied to the corrected tiles (background pixels zeroed).

5. **Per-tile spectral cleaning via RamanSPy** — the segmented tiles are processed through a fixed RamanSPy pipeline: Whitaker-Hayes despiking, Savitzky-Golay denoising (`savgol_window`, `savgol_polyorder`), IASLS baseline correction, and min-max normalisation. Zero-variance spectra (e.g. pure background pixels) are excluded and restored as zeros after processing. Tiles are processed in parallel across `max_workers` workers. Results are cached to disk.

6. **ASHLAR tile stitching** — corrected tiles and their stage coordinates are written as per-cycle OME-TIFF input files. ASHLAR runs in the `FOCUS_ASHLAR` conda environment to produce a seamlessly stitched mosaic. The alignment reference channel is automatically chosen as the highest mean intensity channel in the first spectral cycle.

7. **OME-TIFF pyramid output** — the stitched hyperspectral mosaic is saved as a multi-resolution OME-TIFF. Channels correspond to individual Raman shift values.

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `savgol_window` | `int` | `7` | Savitzky-Golay filter window length (must be odd and > `savgol_polyorder`) | Positive odd integer |
| `savgol_polyorder` | `int` | `3` | Savitzky-Golay polynomial order | Positive integer < `savgol_window` |
| `otsu_threshold_factor` | `float` | `0.7` | Multiplicative factor applied to the Otsu threshold during tissue segmentation; lower values include more pixels as tissue | `0.0` – `2.0` |
| `bg_min_area_fraction` | `float` | `0.05` | Minimum tissue contour area as a fraction of total mosaic area; contours below this are removed | `0.0` – `1.0` |
| `min_object_size` | `int` | `500` | Minimum connected component size (pixels) retained in the tissue mask | Positive integer |
| `max_workers` | `int` | `8` | Maximum parallel workers for BaSiC correction and spectral cleaning | Positive integer |
| `force_recomputing` | `bool` | `False` | Reprocess all steps even if cached intermediate files exist | `True`, `False` |

!!! tip "Caching"
    Each pipeline stage writes its result to disk (`basic_corrected_tiles.npy`, `segmented_tiles.npy`, `raman_corrected_tiles.npy`). If processing is interrupted, FOCUS resumes from the last cached stage unless `force_recomputing=True`.

---

## Registration

!!! warning "Only `spot_interpolation` is compatible"
    Raman is treated as a pixel image for preprocessing but outputs spectral data that is registered using spot-based interpolation. `feature_extraction` is **not** compatible.

`spot_interpolation` maps anchor spots from the reference modality onto the Raman coordinate space and computes Gaussian-weighted averages of the spectral channels falling within each anchor spot's footprint.

```yaml
registration_type: spot_interpolation
```

---

## Output

The ASHLAR stitching step produces a multi-resolution OME-TIFF per sample at:

```
<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Data type | `uint8` (ASHLAR output); source tiles are `float32` |
| Channels | One per Raman shift value (wavenumber in cm⁻¹) |
| Compression | zlib |
| Pixel metadata | Physical size in µm per axis embedded in OME-XML |
| Pyramid | Multi-resolution levels for efficient viewer access |

Channel names in the OME-XML correspond to the wavenumber array computed from the LIF metadata, enabling direct spectral axis access in downstream viewers.

---

## Config Example

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
    registration_type: spot_interpolation
```
