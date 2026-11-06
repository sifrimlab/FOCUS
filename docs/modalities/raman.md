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
| `2/4` Correcting illumination and stitching | Per acquisition: detector offset and flat-field estimate, checked against the tile overlaps; tile registration |
| `3/4` Placing acquisitions | Chooses the reference image and places every other acquisition in its frame (rotation, scale and shift) |
| `4/4` Processing spectra and writing OME-TIFF | Per acquisition: assembly, flat-field correction, spectral denoising, pyramid; records the offset removal and normalization that registration applies to spot spectra |

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
| `remove_offset` | `bool` | `false` | Registration subtracts from each averaged spectrum (spot mean, spot-sized block) its minimum over the spectral window (the near-flat non-resonant background). Multi-channel acquisitions only |
| `normalization` | `none` / `area` | `none` | `none` keeps quantitative, concentration-linear spot spectra. `area` makes registration divide each averaged spectrum by its mean over the acquisition's channels. Multi-channel acquisitions only |
| `force_recomputing` | `bool` | `false` | Recompute even when the output carries the current fingerprint |

The stored pixels are always linear intensities: `remove_offset` and `normalization` are recorded per
image (`focus_spot_offset`, `focus_spot_normalization`) and applied by registration to spectra
averaged over many pixels. On a single noisy pixel the minimum and the mean are biased by an amount
that follows the illumination, so a per-pixel offset or normalization prints the tile pattern into
the spectral shape. Both settings are part of the fingerprint: changing one reprocesses the sample
but leaves its pixels unchanged, so the alignments stay valid and the registration is recomputed.

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
| Values | Linear intensities: detector counts above the detector offset divided by the flat-field, denoised, on a grid of 1/4 count |
| Channels | One per wavenumber, increasing, named `<wavenumber> cm-1`, pump wavelength as OME `ExcitationWavelength` |
| Pyramid | Per image, levels halving each side down to one 512 px tile, stored as SubIFDs |
| Compression | zstd without a predictor (lossless on the stored grid); readable by tifffile and Bio-Formats 8 (QuPath 0.7) |
| Image 0 | Tissue outline as an OME ROI, display rendering (`focus_display`) with the lipid (2850 cm⁻¹, green) and protein (2930 cm⁻¹, magenta) channels shown when measured |

Per image, the `focus.image` MapAnnotation holds:

| Key | Value |
|-----|-------|
| `focus_acquisition`, `focus_lif_elements`, `focus_superseded` | Acquisition name, its LIF elements, the scans it supersedes |
| `focus_wavenumbers_cm1`, `focus_pump_nm`, `focus_stokes_nm` | Raman shift and pump wavelength of every channel, Stokes wavelength |
| `focus_detector_gain`, `focus_line_average`, `focus_laser_power_w`, `focus_bit_depth` | Acquisition settings |
| `focus_flatfield`, `focus_flatfield_check` | Flat-field source, illumination range and detector offset fit; the overlap check |
| `focus_detector_offset` | Detector offset subtracted before the flat-field division, in counts (`0` when not measured, or with a borrowed flat-field) |
| `focus_tile_registration` | Tile registration summary |
| `focus_placement` | Placement in image 0, `a,b,tx,c,d,ty`: image pixel `(col, row)` lies at image-0 pixel `(a·col + b·row + tx, c·col + d·row + ty)`. Files written before `raman-3` hold `x0,y0,sx,sy` (shift and scale only), still read |
| `focus_placement_method` | How the placement was found: candidate, rotation, scale, inlier blocks, median residual, tissue correlation |
| `focus_offset_removed`, `focus_normalization` | Processing of the stored pixels: always `False` and `none` (linear) |
| `focus_spot_offset`, `focus_spot_normalization` | Processing registration applies to averaged spectra: `min` or `none`, `area` or `none` |
| `focus_quantization_step` | Storage grid (`0.25` count) |
| `focus_saturated_fraction`, `focus_spectral_rank`, `focus_spectral_rank_bin_um` | Saturated fraction per channel; denoising rank and its bin size (`not applied` without denoising) |

The file-level `focus` MapAnnotation holds the fingerprint, the input signature, the pixel digest
and the skipped elements.

The output is reused while its fingerprint (method version, input file content, settings,
calibration constants, library versions) is unchanged. A regenerated output whose pixels are
identical keeps the alignments made on the previous one.

---

## Alignment and registration

The alignment GUI shows image 0 with its stored rendering. Alignment is done once per sample: the
other images follow through their placements.

`raman` is compatible with two registration types: `raman_pixel_interpolation` (mean spectrum per
spot) and `pixel_composition` (composition of the pixels in each spot).

```yaml
registration_type: raman_pixel_interpolation
```

Every image contributes its channels: each anchor spot receives the Gaussian-weighted average of the
measured pixels of that image inside its footprint, the footprint being mapped through the alignment
and the image's placement, and the averaged spectrum then gets the image's `remove_offset` and
`normalization`. A spot an acquisition does not cover gets NaN for that acquisition's channels (an
ROI, for example); at compilation a spot with any NaN feature, or none measured, is dropped like any
uncovered spot. The registered features are named after their wavenumbers; `var` holds `wavenumber`
and `acquisition`, and a wavenumber measured by several acquisitions carries the acquisition name.
See [Registration](../pipeline/registration.md#raman_pixel_interpolation).

Outputs written by `raman-2` applied `remove_offset` and `normalization` to the pixels and record
no spot processing; they are registered as stored. Outputs written before `raman-2` (a single ASHLAR-stitched
image without wavenumbers) are still registered, with features named `Channel_0` to
`Channel_<C-1>`.

```yaml
registration_type: pixel_composition
```

A Raman pixel (about 0.5 µm) is close to one chemical state, and a 55 µm spot is a mixture of
thousands of them. `pixel_composition` finds the dataset's endmembers (the archetypes of spot-sized
block spectra inside the spots: extreme spot-scale spectra that enclose all the others, with the
image's `remove_offset` and `normalization`; their number is the Gavish-Donoho spectral rank plus
one) and gives each spot the fully constrained abundances of its mean spectrum after the same
processing: where the spot lies between the archetypal spectra. Acquisitions with the same wavenumbers share one
set of endmembers; an acquisition with fewer than 3 channels contributes channel means only. The
endmember spectra are stored in the merged file's `uns['pixel_phenotypes']`, the linear mean
spectrum of each spot in `obsm['channel_mean']`, and the dominant endmember of every pixel in a
label map for display. See [Registration](../pipeline/registration.md#pixel_composition).

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
