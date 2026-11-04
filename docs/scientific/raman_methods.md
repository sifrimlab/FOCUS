# Raman Preprocessing Methods

Implementation reference for `focus/preprocessing/raman/` (method version `raman-2`). The input is one
Leica LAS X session file (`.lif`) of hyperspectral stimulated Raman scattering (SRS)
acquisitions[^freudiger2008]; the output is one OME-TIFF holding one OME image per acquisition.
`RamanImage.process()` reuses an existing output whose fingerprint matches and otherwise runs
`workflow.process_lif`: inventory (§1), illumination and tile registration (§2, §3), placement of the
acquisitions (§4), spectral processing (§5) and writing (§6).

---

## 1. Inventory

### 1.1 Elements

`inventory.read_elements` walks the LIF XML header in readlif's order (depth first through
`Children/Element`, descending only into elements that hold `Data`), so element `i` is readlif image
`i`. Per image element:

| Field | XML source |
|---|---|
| width, height, pixel size | `DimensionDescription` `DimID` 1 (X, columns) and 2 (Y, rows): `NumberOfElements`, `Length / NumberOfElements`, unit `m` or `um` |
| channels | `DimID` 9 `NumberOfElements` (1 when absent) |
| stored tiles | `DimID` 10 `NumberOfElements` (1 when absent) |
| byte strides | `BytesInc` of `DimID` 10, 9, 2, 1 |
| bit depth | `ChannelDescription@Resolution` |
| planned tiles, stage positions | `Attachment[@Name="TileScanInfo"]/Tile`: count, `PosX`, `PosY` (scaled by the X dimension unit to µm), presence of `PosZ` |
| pump wavelengths | `LambdaExcitation` begin and end (`...Double` attributes first), `linspace` over the channels; without `DimID` 9 the `Wavelength` of the CRS laser |
| Stokes wavelength | `PumpWavelength` of the `LaserArray/Laser` that carries one (the fixed 1032 nm beam) |
| detector gain | `Gain` of the active `DetectorList/Detector` |
| line average, laser power | `ATLConfocalSettingDefinition@LineAverage`, laser `PumpOutputPower` |
| start time | first entry of `TimeStampList` (Windows FILETIME) |
| memory | `Memory@Size` |

The Raman shift of channel `i` is

\[
\tilde\nu_i = \frac{10^7}{\lambda_{\mathrm{pump},i}} - \frac{10^7}{\lambda_{\mathrm{Stokes}}}\quad(\mathrm{cm}^{-1}).
\]

### 1.2 Selection

`inventory.select` decides on every element, in this order:

1. no CRS laser or no finite pump wavelength: not an SRS acquisition;
2. no `TileScanInfo` tile: single frame without a stage position;
3. one stored tile, one planned tile, no `PosZ`: LAS X merged mosaic or snapshot (merged mosaics
   carry one stage tile without a Z position; acquired tiles carry `PosZ`);
4. stored tiles different from planned tiles, or `Memory@Size` smaller than
   tiles x channels x rows x columns x bytes: incomplete scan;
5. no positive pixel size;
6. one tile outside a job folder: snapshot.

The remaining tile scans are acquisitions. Single-tile elements in one job folder sharing size,
pixel size, channels, pump wavelengths, gain, line average and bit depth form one acquisition (Mark &
Find positions). Acquisitions are then visited from the latest to the earliest start time: one that
measures the same spectral window (first and last pump wavelength within 0.05 nm, same channel count)
over a stage footprint overlapping a later kept acquisition is superseded by it. FOCUS design choice:
a session in which a window was re-acquired (for example with a higher detector gain, or over a
larger region) keeps the re-acquisition only.

---

## 2. Reading and illumination

### 2.1 Tile stacks

`reader.open_stack` maps each element's memory block (offset from readlif) with `numpy.memmap` and
a strided view of shape `(tiles, channels, rows, columns)` built from the `BytesInc` strides; samples
are read in their native type (8 or 16 bit) without rescaling. Channels are reordered by increasing
wavenumber. The saturation value is \(2^{\mathrm{bits}}-1\).

### 2.2 Flat-field

Every tile of an acquisition is imaged through the same scan field, so the shading profile is
shared. `flatfield.estimate` computes it retrospectively[^tomazevic2002]:

1. per tile, the mean over channels of the unsaturated samples (`channel_mean`; a pixel saturated in
   every channel keeps the saturation value);
2. the per-pixel mean over the tissue tiles: tiles whose mean is at least the 25th percentile of the
   tile means;
3. Gaussian smoothing with \(\sigma = 36\) µm, normalised to mean 1, floored at 0.05.

One profile is estimated per acquisition and applied to all its channels. FOCUS design choice: the
shading of the scan optics changes little over the 20 to 40 nm pump tuning range of one window,
while per-channel estimates from 8-bit low-count channels are dominated by noise. Acquisitions with
fewer than 10 tiles (Mark & Find positions) borrow the profile of the acquisition with the nearest
mean wavenumber in the same file; without one they are left uncorrected and reported.

`flatfield.overlap_check` measures the shading independently of the model: in each side-by-side
overlap of registered tiles (§3) the same tissue is imaged near the right (or bottom) edge of one
tile and near the left (or top) edge of the next, so the ratio of the two strip means is the
edge-to-edge illumination ratio. Strips 8 px inside the overlap and 100 px away from the tile ends
are used; strips darker than half the 25th-percentile tile mean are background and ignored. The
median measured and modelled ratios are reported and stored with the image.

FOCUS design choice, with its calibration: BaSiC[^peng2017], used before `raman-2`, was compared
with this estimator against the overlap ratios of the five P762 acquisitions. BaSiC (reference
smoothness) underestimated the right/left ratio of the single-channel scans (1.20 and 1.10 modelled,
1.35 and 1.27 measured) and its flat-fields carried texture that correlated between acquisitions
sharing a tile grid (r = 0.46 to 0.48) and not between acquisitions on different grids, that is,
specimen; raising its smoothness removed the texture and the shading with it. The smoothed mean
matched the measured ratios within 0.04 for every acquisition.

---

## 3. Tile registration and assembly

`stitching.register` follows ASHLAR's edge alignment[^muhlich2022]:

1. stage positions in pixels: \((\mathrm{PosX}, \mathrm{PosY})\) minus their minimum, divided by
   the pixel size; stage X runs along the columns and stage Y along the rows, with no flip (checked
   on P762 against the mosaic LAS X merged itself: correlation 0.79 unflipped, 0.02 or below for
   every flip);
2. neighbour pairs: tiles whose predicted overlap is at least 16 px in both directions and that lie
   side by side (corner contacts are ignored);
3. per pair, phase correlation[^kuglin1975] of the predicted overlap of the two flat-field
   corrected channel-mean tiles, both whitened with a Laplacian of Gaussian (\(\sigma = 1\) px)
   (`skimage.registration.phase_cross_correlation`[^guizar2008]); a correction larger than
   15 µm (ASHLAR's default maximum shift) or than half the overlap is rejected;
4. acceptance: the normalised cross-correlation of the aligned overlap must exceed the 99th
   percentile reached by 200 random pairs of tiles that do not overlap, cut with real overlap
   geometries (ASHLAR's permutation test);
5. positions: weighted least squares over the accepted pairwise offsets (weight = correlation),
   with a stage prior of weight \(10^{-3}\) times the median edge weight, so tiles without an
   accepted edge keep their stage placement; edges with a residual above 3 px are dropped and the
   system solved again (up to three times); positions are rounded to whole pixels.

On P762 81 to 170 overlaps per acquisition are accepted with a median residual of 0.3 to 0.4 px; the
solved positions differ from the stage by a median of 26 to 36 px across the mosaic (a stage to
scanner scale difference of about 0.6 %), and the assembled CH-window mosaic matches the LAS X
merge with a correlation of 0.956 (0.800 with stage positions).

Assembly (`stitching.ownership`, `assemble_channel`) never blends: every mosaic pixel takes its value
from the tile whose centre is nearest (ties to the lower tile index), so each stored sample is one
measurement and the masks partition the covered area. Pixels no tile covers are NaN.

---

## 4. Placement of the acquisitions

### 4.1 Reference image

Image 0 is the acquisition with the largest footprint; footprints within 2 % of the largest are
tied and the tie goes to an acquisition with CH-region channels (2800 to 3100 cm⁻¹), then to the
most channels. FOCUS design choice: the CH region has the strongest SRS signal and the lipid and
protein contrast used for alignment.

### 4.2 Placement

Each mosaic's origin in stage µm is the mean over its tiles of stage position minus solved position
times the pixel size. Stage coordinates are not trusted alone across acquisitions: in P762 the
acquisitions taken 9 hours before the reference are 455 µm from their stage positions relative to it
(the slide or the stage frame moved between scans). `placement.place` therefore scores candidates:

- stage coordinates relative to the reference;
- stage coordinates relative to every image already placed (acquisitions of one session sitting
  share a stage frame);
- a phase-correlation search over every relative offset of the whitened (LoG, \(\sigma = 1.5\) px)
  channel-mean mosaics at 4.5 µm per pixel, zero-padded to the sum of their sizes so no offset
  wraps.

Each candidate is scored by the normalised cross-correlation of the unwhitened channel-mean mosaics
over their overlap at 4.5 µm per pixel, against every image already placed; the best candidate is
refined by phase correlation of the whitened mosaics at about 1.2 µm per pixel, within two coarse
pixels, against the image it matched. FOCUS design choice: tissue morphology is shared by all
spectral windows while their contrast differs, so a fingerprint-window acquisition is scored against
whichever placed image it resembles most. Placements more than 20 µm from the stage position are
reported. The placement is stored as `x0,y0,sx,sy`: image pixel \((c, r)\) lies at image-0 pixel
\((x_0 + s_x c,\ y_0 + s_y r)\), with \(s\) the ratio of the pixel sizes.

---

## 5. Spectral processing

`images.build` stages the assembled native samples of every channel (uint8 or uint16, 0 where no
tile measured) with one flat-field mosaic beside them, then processes blocks of 1024 x 1024 px:

1. **Flat-field correction:** native value divided by the flat-field mosaic (NaN where not covered).
2. **Denoising** (`denoise`, multi-channel acquisitions): the spectra are averaged over bins of
   4.5 µm (8 x 8 px on P762); bins containing a saturated sample are left out; the centred bin spectra
   (up to 300 000) are decomposed by SVD and the rank is the number of singular values above the
   optimal hard threshold for an unknown noise level, \(\omega(\beta)\cdot\mathrm{median}(\sigma_i)\)
   with \(\omega(\beta) \approx 0.56\beta^3 - 0.95\beta^2 + 1.82\beta + 1.43\)[^gavish2014], at least 1.
   Every pixel spectrum \(x\) is replaced by \(\mu + VV^\top(x-\mu)\), with \(\mu\) the mean bin
   spectrum and \(V\) the kept components. FOCUS design choice, with its calibration: on P762 the
   threshold keeps 1 component (CH window) and 0 to 1 (fingerprint windows) on single pixels, and 3
   and 2 on 4.5 µm bins; ranking single pixels would erase the CH2/CH3 shape that separates lipids
   from proteins[^lu2015], which is real at the cellular scale. The projection removes the spectral
   directions that are noise at that scale and keeps the spatial resolution.
3. **Offset** (`remove_offset`): each pixel's minimum over the window is subtracted. The
   non-resonant SRS background (cross-phase modulation, two-photon absorption, thermal effects) is
   nearly flat over a window[^berto2014]; the minimum is a FOCUS approximation of it.
4. **Normalisation** (`normalization: area`): each spectrum is divided by its mean over the
   acquisition's channels; a non-positive mean gives NaN.
5. **Quantisation:** values are rounded to multiples of 1/4 detector count (2⁻⁸ for area-normalised
   spectra), so the float32 tiles compress well. FOCUS design choice: 4 times finer than the
   converter step; on P762 the rounding adds less than 0.5 % to the variance of the denoised
   measurement noise.

Single-channel acquisitions are only flat-field corrected and quantised. SRS intensity is linear in
concentration[^freudiger2008][^fu2012]; without `normalization` the stored values keep that property
within an acquisition. The saturated fraction of every channel is stored; above 1 % a warning is
reported. Saturated samples keep their (clipped) values.

---

## 6. Output

One BigTIFF, written image by image (`ome_write.ImagesWriter`): image 0 first, then the other
acquisitions in start order. Each image is a series of one page per channel with SubIFD levels,
2 x 2 averages of the finite samples, rounded to the same grid, halving each side until the image
fits one 512 px tile; tiles are zstd compressed without a predictor (Bio-Formats 8.4 cannot read the
TIFF floating-point predictor, and horizontal differencing would carry NaN along the rows). The OME-XML
(first page, UTF-8) describes every image: channels named `<wavenumber> cm-1` with the pump
wavelength as `ExcitationWavelength` and `AcquisitionMode` `SpectralImaging`, physical pixel size,
and a `focus.image` MapAnnotation per image (listed in [Raman](../modalities/raman.md#output)). It is
written with the first image and replaced once every image is known
(`ome_write.replace_description`).

Image 0 carries the display rendering (`focus_display`) and the tissue outline: on its stored level
nearest 4.5 µm per pixel, the channel mean (unmeasured pixels set to the 1st percentile of the
measured ones) is smoothed with \(\sigma = 27\) µm and thresholded with Otsu's method[^otsu1979]
over the measured pixels, then cleaned like the microscopy tissue mask (closing, specks, holes,
debris area; see [microscopy](microscopy_methods.md)) and stored as OME ROI polygons. FOCUS design
choice: SRS glass has its own non-resonant level (7 to 10 counts against 20 to 80 for tissue in
the P762 CH3 scan), so the two populations suit Otsu's threshold, whereas the triangle threshold of
the microscopy fluorescence path, made for a near-zero background, classed 99.6 % of the measured
area as tissue; display windows are the 0.35 and 99.65 percentiles over tissue. The channel
nearest 2850 cm⁻¹ (within 30 cm⁻¹) is green and the one nearest 2930 cm⁻¹ magenta, both shown;
without either the middle channel is shown.

The file-level `focus` MapAnnotation holds the fingerprint (method version, input SHA-256, sample,
modality, settings, every constant of `settings.calibration()`, library versions), the input
signature, the level-0 pixel digest of all images in page order, the skipped elements with their
reasons, the tissue fraction and `focus_display`. A regenerated output with the same digest records
the file it replaces, so alignments made on it stay valid.

---

## 7. Registration

`raman_pixel_interpolation` reads every image (`raman_pixel.raman_targets`): for image `k` with
placement \((x_0, y_0, s_x, s_y)\) the anchor spots, aligned to image-0 pixels, are mapped to
\(((x - x_0)/s_x, (y - y_0)/s_y)\) and the footprint geometry is fitted again from these
coordinates. Only the pixel box the footprints can reach is read (through tifffile's zarr view).
Each spot receives the Gaussian-weighted average of the measured (finite) pixels of the image inside
its footprint; a spot with none gets NaN for that image's channels. Features are concatenated over
the images; see [Registration](registration_methods.md#4-raman-pixel-interpolation-raman_pixel_interpolation).

---

## References

[^freudiger2008]: Freudiger C. W. et al. Label-free biomedical imaging with high sensitivity by stimulated Raman scattering microscopy. *Science* 322, 1857-1861 (2008). [doi:10.1126/science.1165758](https://doi.org/10.1126/science.1165758)
[^fu2012]: Fu D. et al. Quantitative chemical imaging with multiplex stimulated Raman scattering microscopy. *Journal of the American Chemical Society* 134, 3623-3626 (2012). [doi:10.1021/ja210081h](https://doi.org/10.1021/ja210081h)
[^lu2015]: Lu F.-K. et al. Label-free DNA imaging in vivo with stimulated Raman scattering microscopy. *PNAS* 112, 11624-11629 (2015). [doi:10.1073/pnas.1515121112](https://doi.org/10.1073/pnas.1515121112)
[^berto2014]: Berto P., Andresen E. R., Rigneault H. Background-free stimulated Raman spectroscopy and microscopy. *Physical Review Letters* 112, 053905 (2014). [doi:10.1103/PhysRevLett.112.053905](https://doi.org/10.1103/PhysRevLett.112.053905)
[^tomazevic2002]: Tomazevic D., Likar B., Pernus F. Comparative evaluation of retrospective shading correction methods. *Journal of Microscopy* 208, 212-223 (2002). [doi:10.1046/j.1365-2818.2002.01079.x](https://doi.org/10.1046/j.1365-2818.2002.01079.x)
[^peng2017]: Peng T. et al. A BaSiC tool for background and shading correction of optical microscopy images. *Nature Communications* 8, 14836 (2017). [doi:10.1038/ncomms14836](https://doi.org/10.1038/ncomms14836)
[^muhlich2022]: Muhlich J. L. et al. Stitching and registering highly multiplexed whole-slide images of tissues and tumors using ASHLAR. *Bioinformatics* 38, 4613-4621 (2022). [doi:10.1093/bioinformatics/btac544](https://doi.org/10.1093/bioinformatics/btac544)
[^kuglin1975]: Kuglin C. D., Hines D. C. The phase correlation image alignment method. *Proceedings of the IEEE International Conference on Cybernetics and Society*, 163-165 (1975).
[^guizar2008]: Guizar-Sicairos M., Thurman S. T., Fienup J. R. Efficient subpixel image registration algorithms. *Optics Letters* 33, 156-158 (2008). [doi:10.1364/OL.33.000156](https://doi.org/10.1364/OL.33.000156)
[^otsu1979]: Otsu N. A threshold selection method from gray-level histograms. *IEEE Transactions on Systems, Man, and Cybernetics* 9, 62-66 (1979). [doi:10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076)
[^gavish2014]: Gavish M., Donoho D. L. The optimal hard threshold for singular values is 4/√3. *IEEE Transactions on Information Theory* 60, 5040-5053 (2014). [doi:10.1109/TIT.2014.2323359](https://doi.org/10.1109/TIT.2014.2323359)
