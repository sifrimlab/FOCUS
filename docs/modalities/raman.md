# Raman Spectroscopy Imaging (SRS)

## Overview

FOCUS preprocesses hyperspectral **stimulated Raman scattering (SRS)** acquisitions from Leica
Stellaris CRS microscopes, stored as Leica Image Format (`.lif`) session files. SRS measures the
Raman response of chemical bonds with a photodiode and lock-in detection; its signal is linear in the
concentration of the probed bonds and carries no fluorescence background[^freudiger2008]. A CH-region
window (2800 to 3100 cm⁻¹) separates lipids (CH₂ stretch near 2850 cm⁻¹) from proteins (CH₃ stretch
near 2930 cm⁻¹)[^lu2015], and fingerprint windows (about 1200 to 1800 cm⁻¹) add further bands.

One sample is one LAS X session file. A session usually holds several **acquisitions**: tile scans of
the section at different spectral windows, single-wavenumber scans, and Mark & Find positions (ROIs),
each with its own tile grid and channel count. FOCUS stitches every acquisition separately and writes
them all to **one OME-TIFF with one OME image per acquisition**, each in the frame of a reference
acquisition. No spectrum is resampled: a 20-channel whole-section scan and a 100-channel ROI keep
their own channels.

`RamanDataset.process_dataset()` runs four steps per sample, reported as `1/4` to `4/4`:

| Step | What it does |
|------|--------------|
| `1/4` Reading the LIF session | Lists every image in the file, selects the SRS acquisitions and reports what is skipped and why |
| `2/4` Correcting illumination and stitching | Per acquisition: flat-field estimate, checked against the tile overlaps; tile registration |
| `3/4` Placing acquisitions | Chooses the reference image and places every other acquisition in its frame |
| `4/4` Processing spectra and writing OME-TIFF | Per acquisition: assembly, spectral denoising, optional offset and normalization, pyramid |

The detailed methods, with references, are in [Raman preprocessing methods](../scientific/raman_methods.md).

!!! note "Standalone"
    Raman preprocessing runs in the FOCUS environment. The auxiliary `FOCUS_BaSiCpy` and
    `FOCUS_ASHLAR` conda environments and Java are no longer used; installations that still have
    them can remove them (`conda env remove -n FOCUS_BaSiCpy`, `conda env remove -n FOCUS_ASHLAR`).

---

## Input

Place exactly one `.lif` file per sample in `<sample_id>/<modality_name>/`:

```
dataset_root/
├── sample_A/
│   └── raman/
│       └── sample_A_session.lif
└── sample_B/
    └── raman/
        └── sample_B_session.lif
```

A sample directory without a `.lif` file, or with several, is reported and skipped.

### Which images become acquisitions

Every image element of the file is described from its XML header and receives one decision:

| Element | Decision |
|---------|----------|
| Tile scan whose stored frames equal tiles x channels | Kept |
| Single-wavenumber tile scan (no spectral axis) | Kept, one channel |
| Mark & Find positions of one job with identical settings | Kept together as one acquisition |
| Tile scan with fewer stored tiles than planned (aborted) | Skipped: incomplete scan |
| Single image without a Z stage position | Skipped: LAS X merged mosaic or snapshot |
| Frame without a stage position | Skipped |
| Single tile outside a tile-scan job | Skipped: snapshot |
| Complete scan of the same spectral window (same pump range and channel count) over an overlapping area as a later scan | Skipped: superseded by the later scan |

The activity log lists every kept acquisition (tiles, channels, wavenumber range, detector gain, the
scans it supersedes) and counts the skipped elements by reason. The output records every skipped
element and its reason.

### Wavenumbers

Each channel's Raman shift is `1e7 / λ_pump − 1e7 / λ_Stokes` (cm⁻¹), with the tunable pump wavelength
from the scan definition (`LambdaExcitation`, or the laser line of a single-wavenumber scan) and the
fixed 1032 nm beam (`PumpWavelength` of the CRS laser) as Stokes. Channels are stored by increasing
wavenumber and named after it, for example `2851.3 cm-1`.

---

## Processing parameters

Set under `processing_settings` for the modality.

| Name | Type | Default | Effect |
|------|------|---------|--------|
| `denoise` | `bool` | `true` | Projects every spectrum on the spectral components that are real at the cellular scale (4.5 µm). Multi-channel acquisitions only |
| `remove_offset` | `bool` | `false` | Subtracts each pixel's minimum over its spectral window (the near-flat non-resonant background) |
| `normalization` | `none` / `area` | `none` | `none` keeps quantitative, concentration-linear intensities. `area` divides each spectrum by its mean over the acquisition's channels |
| `force_recomputing` | `bool` | `false` | Recompute even when the output carries the current fingerprint |

Every other constant (smoothing scales, registration limits, the denoising scale, the storage grid) is
fixed and part of the output fingerprint; see [Raman preprocessing methods](../scientific/raman_methods.md).

The settings of earlier versions are rejected by the configuration validation, with the reason:
`savgol_window` and `savgol_polyorder` (spectral smoothing is replaced by `denoise`),
`bg_min_area_fraction`, `otsu_threshold_factor` and `min_object_size` (the background is no longer
removed; tissue detection is automatic), `max_workers` (processing uses the available CPUs).

!!! warning "Intensities are comparable within an acquisition only"
    The detector gain, line averaging and laser settings of each acquisition are recorded in its image
    annotation. Acquisitions taken with different gains (10 to 59 in a typical session) have
    different intensity scales, so ratios between channels of different images are not meaningful.

---

## Output

```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.ome.tiff
```

| Property | Value |
|----------|-------|
| Images | One OME image per acquisition; image 0 is the reference acquisition |
| Data type | `float32`; NaN where no tile measured |
| Values | Detector counts divided by the flat-field (or area-normalised), on a grid of 1/4 count (2⁻⁸ for area-normalised spectra) |
| Channels | One per wavenumber, increasing, named `<wavenumber> cm-1`, pump wavelength as OME `ExcitationWavelength` |
| Pyramid | Per image, levels halving each side down to one 512 px tile, stored as SubIFDs |
| Compression | zstd without a predictor (lossless on the stored grid); readable by tifffile and Bio-Formats 8 (QuPath 0.7) |
| Image 0 | Tissue outline as an OME ROI, display rendering (`focus_display`) with the lipid (2850 cm⁻¹, green) and protein (2930 cm⁻¹, magenta) channels shown when measured |

Per image, the `focus.image` MapAnnotation holds the LIF elements, wavenumbers, pump and Stokes
wavelengths, detector gain, line average, laser power, flat-field source and its overlap check, the
tile registration summary, the placement in image 0 (`focus_placement`, `x0,y0,sx,sy`: image pixel
`(c, r)` lies at image-0 pixel `(x0 + sx·c, y0 + sy·r)`), the saturated fraction per channel, the
spectral rank and the processing applied. The file-level `focus` MapAnnotation holds the fingerprint,
the input signature, the pixel digest and the skipped elements.

The output is reused while its fingerprint (method version, input file content, settings,
calibration constants, library versions) is unchanged. A regenerated output whose pixels are
identical keeps the alignments made on the previous one.

---

## Alignment and registration

The alignment GUI shows image 0 with its stored rendering. Alignment is done once per sample: the
other images follow through their placements.

`raman` is compatible with one registration type:

```yaml
registration_type: raman_pixel_interpolation
```

Every image contributes its channels: each anchor spot receives the Gaussian-weighted average of the
measured pixels of that image inside its footprint, the footprint being mapped through the alignment
and the image's placement. A spot an acquisition does not cover gets NaN for that acquisition's
channels (an ROI, for example); a spot no image covers is dropped at assembly like any uncovered spot.
The registered features are named after their wavenumbers; `var` holds `wavenumber` and
`acquisition`, and a wavenumber measured by several acquisitions carries the acquisition name. See
[Registration](../pipeline/registration.md#raman_pixel_interpolation).

Outputs written before `raman-2` (a single ASHLAR-stitched image without wavenumbers) are still
registered, with features named `Channel_0` to `Channel_<C-1>`.

---

## Config example

```yaml
modalities:
  - name: raman
    type: raman
    processing_settings:
      denoise: true
      remove_offset: false
      normalization: none
      force_recomputing: false
    registration_type: raman_pixel_interpolation
```

---

## References

[^freudiger2008]: Freudiger C. W. et al. Label-free biomedical imaging with high sensitivity by stimulated Raman scattering microscopy. *Science* 322, 1857-1861 (2008). [doi:10.1126/science.1165758](https://doi.org/10.1126/science.1165758)
[^lu2015]: Lu F.-K. et al. Label-free DNA imaging in vivo with stimulated Raman scattering microscopy. *PNAS* 112, 11624-11629 (2015). [doi:10.1073/pnas.1515121112](https://doi.org/10.1073/pnas.1515121112)
