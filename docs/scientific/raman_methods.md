# Raman Preprocessing Methods

Implementation reference for `focus/preprocessing/raman/` (method version `raman-3`). The input is one
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

Every tile of an acquisition is imaged through the same scan field, so the shading profile \(P\) is
shared. The illumination multiplies the optical signal, not the detector's own offset: a sample of a
multi-channel SRS acquisition is \(d + P(x, y)\,s\), with \(s\) the signal and \(d\) a constant
offset, the same in every channel, that the scan field does not shade; a channel close to the offset
therefore looks less shaded than a bright one.

`flatfield.detector_offset` estimates \(d\) from the channels themselves:

1. per tile and channel, the means of the unsaturated samples over blocks of about 9 µm
   (`channel_blocks`);
2. per channel, the mean of these block maps over the tissue tiles (tiles whose mean is at least the
   25th percentile of the tile means), smoothed with \(\sigma = 36\) µm and centred;
3. the leading spatial component of the centred maps (SVD across channels): with one profile above
   one offset, its loading in channel \(c\) is \(k(\bar s_c - d)\), linear in the channel mean
   \(\bar s_c\), so a line fitted to the loadings against the channel means gives
   \(d = -\mathrm{intercept}/\mathrm{slope}\).

The offset is accepted when the acquisition has at least 3 channels and 20 tiles, the line fits with
\(R^2 \ge 0.95\) across channels, the RMS of the modelled shading is at least 5 times its split-half
noise (half the difference of the maps of alternate tissue tiles), and \(d\) lies between 0 and
the dimmest channel mean; otherwise \(d = 0\), which single-channel acquisitions always get.

`flatfield.estimate` then computes the profile retrospectively[^tomazevic2002] above the offset:

1. per tile, the mean over channels of the unsaturated samples (`channel_mean`; a pixel saturated in
   every channel keeps the saturation value), minus \(d\);
2. the per-pixel mean over the tissue tiles;
3. Gaussian smoothing with \(\sigma = 36\) µm, normalised to mean 1, floored at 0.05.

Every channel is corrected as \((\mathrm{raw} - d)/P\), and so is everything derived from the
channels: the tile registration (§3), the placement views (§4), the overlap check below, the
denoising fit and the written pixels (§5). FOCUS design choice, with its calibration: one profile per
acquisition, shared by all its channels above one offset, and no per-channel profile. On the
20-channel P762 CH scan R4 (4), one profile with \(d = 6.92\) counts explains every channel (\(R^2\)
0.999 across channels; residual 0.13 counts against 0.30 counts of split-half noise). Above the
offset the shading spans ±36 % (1st to 99th percentile 0.73 apart), where the estimate on raw
counts saw ±18 % (0.36). Acquisitions with fewer than 10 tiles (Mark & Find positions) borrow the
profile of the acquisition with the nearest mean wavenumber in the same file, applied with
\(d = 0\); without one they are left uncorrected and reported.

`flatfield.overlap_check` measures the shading independently of the model: in each side-by-side
overlap of registered tiles (§3) the same tissue is imaged near the right (or bottom) edge of one
tile and near the left (or top) edge of the next, so the ratio of the two strip means (channel mean
above the offset) is the edge-to-edge illumination ratio. Strips 8 px inside the overlap and 100 px
away from the tile ends are used; strips darker than half the 25th-percentile tile mean are
background and ignored. The median measured and modelled ratios are reported and stored with the
image.

FOCUS design choice, with its calibration: BaSiC[^peng2017], used before `raman-2`, was compared
with the smoothed mean (then estimated on raw counts) against the overlap ratios of the five P762 acquisitions. BaSiC (reference
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
   corrected channel-mean tiles (above the offset, §2.2), both whitened with a Laplacian of Gaussian (\(\sigma = 1\) px)
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

A placement is a similarity transform (rotation, scale and shift) from an image's level-0 pixels to
image 0's level-0 pixels. Each mosaic's origin in stage µm is the mean over its tiles of stage
position minus solved position times the pixel size. Stage coordinates are not trusted alone across
acquisitions: the slide can be lifted and put back between scans, which moves and turns it. In P762
the acquisitions taken 9 hours before the reference are 455 µm from their stage positions relative to
it and turned by about 0.64°; under the shift-only placement used before `raman-3` their spots were
misplaced by a median of 56 µm, up to about 115 µm.

The views are the flat-field corrected channel-mean mosaics (§2.2) averaged over blocks of about
9 µm (search view), 4.5 µm (coarse view) and 1.2 µm (fine view); whitened views are filtered with a
Laplacian of Gaussian (\(\sigma = 1.5\) view px). `placement.place` works in two stages:

1. **Candidates**, each a full transform:
    - a rotation search over the full circle: at every angle, in 5° steps on the search view, the
      image is resampled at that rotation and at the ratio of the pixel sizes, its shift comes from
      phase correlation[^kuglin1975] of the whitened views zero-padded to the sum of their sizes (so
      no shift wraps), and the angle is scored by the normalised cross-correlation (NCC) of the
      whitened views over the pixels both measure; the best angle is searched again within ±5° in
      1° steps, then within ±1° in 0.25° steps, on the coarse view;
    - the stage offset to the reference;
    - the stage offset to every image already placed, composed with that image's placement
      (acquisitions of one sitting share a stage frame, so they share its rotation).

    Each candidate is scored by the NCC of the unwhitened coarse views against every image already
    placed; the best one is kept, with the image it matched. FOCUS design choice: tissue morphology
    is shared by all spectral windows while their contrast differs, so a fingerprint-window
    acquisition is scored against whichever placed image it resembles most.
2. **Refinement** against the matched image, on the whitened fine views, the image resampled through
   the candidate:
    - blocks of 96 px on a grid; blocks with an unmeasured pixel are skipped, the more textured half
      (standard deviation at least the median) is kept, at most 600 of them drawn at random;
    - each block is matched by NCC within ±24 px of its predicted position; it is kept when its peak
      beats the 99th percentile of the peaks of 200 mismatched block pairs (blocks more than three
      block sides apart; ASHLAR's permutation test[^muhlich2022]) and does not lie on the edge of
      the window;
    - a similarity transform is fitted to the kept block centres by RANSAC[^fischler1981]
      (`skimage` `SimilarityTransform`, inlier distance 2 fine px), then by least squares on the
      inliers;
    - a second pass starts from that transform with a ±6 px window.

    A pass with fewer than 8 usable, kept or inlier blocks stops and keeps the previous transform;
    when the first pass stops, the candidate is not refined.

The activity log and `focus_placement_method` give the method, the rotation, the scale relative to
the ratio of the recorded pixel sizes, the distance from the stage position (at the image centre),
the inlier blocks and their median residual, and the tissue correlation. Warnings are reported for
a placement more than 20 µm from the stage position, an unrefined placement, a median block residual
above 5 µm (the tissue may be deformed) and a scale more than 1 % from the ratio of the pixel sizes
(the pixel size metadata may be wrong). On P762, R4 (2), R4 (4) and R4 (8) are turned by −0.65°,
−0.63° and −0.65° relative to the reference and R8 (2) by +0.00°, at 0.9996 to 1.0002 times the
ratio of the pixel sizes, from 366 to 596 inlier blocks with a median residual of 0.2 to 1.3 µm.

The placement is stored as `focus_placement` `a,b,tx,c,d,ty`: image pixel \((\mathrm{col}, \mathrm{row})\)
lies at image-0 pixel \((a\,\mathrm{col} + b\,\mathrm{row} + t_x,\ c\,\mathrm{col} + d\,\mathrm{row} + t_y)\).
Readers also accept the four values `x0,y0,sx,sy` of files written before `raman-3`, read as
\(a = s_x\), \(d = s_y\), \(b = c = 0\), \(t = (x_0, y_0)\).

---

## 5. Spectral processing

`images.build` stages the assembled native samples of every channel (uint8 or uint16, 0 where no
tile measured) with one flat-field mosaic beside them, then processes blocks of 1024 x 1024 px:

1. **Flat-field correction:** native value minus the detector offset \(d\), divided by the
   flat-field mosaic (§2.2; NaN where not covered).
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
   directions that are noise at that scale and keeps the spatial resolution; it is linear, so it
   keeps the stored values linear.
3. **Quantisation:** values are rounded to multiples of 1/4 detector count, so the float32 tiles
   compress well (about 0.75 byte per sample). FOCUS design choice: 4 times finer than the converter
   step; on P762 the rounding adds less than 0.5 % to the variance of the denoised measurement
   noise.

Single-channel acquisitions are only flat-field corrected (with \(d = 0\)) and quantised. SRS
intensity is linear in concentration[^freudiger2008][^fu2012], and the stored values keep that
property within an acquisition whatever the settings. The saturated fraction of every channel is
stored; above 1 % a warning is reported. Saturated samples keep their (clipped) values.

### 5.1 Offset and normalisation

`remove_offset` and `normalization` are not applied to pixels. Each multi-channel image records them
as `focus_spot_offset` (`min` with `remove_offset`, else `none`) and `focus_spot_normalization`
(`area` or `none`); single-channel images record `none` for both. Registration applies them to
spectra averaged over spots and over spot-sized blocks (`spatial.spectral.spot_spectra`, §7):

- **offset** `min`: each averaged spectrum's minimum over the image's channels is subtracted. The
  non-resonant SRS background (cross-phase modulation, two-photon absorption, thermal effects) is
  nearly flat over a window[^berto2014]; the minimum is a FOCUS approximation of it;
- **normalisation** `area`: each averaged spectrum is divided by its mean over the image's channels;
  a non-positive mean gives NaN.

FOCUS design choice, with its calibration: on a single noisy pixel the minimum and the mean over the
window are biased by an amount that follows the local signal-to-noise ratio, and so the
illumination. On P762 the per-pixel offset removal and normalisation of earlier versions printed a
within-tile pattern of 0.4 to 1.5 times the tissue standard deviation into every multi-channel scan,
reproducible between split halves of the tiles (r 0.8 to 0.97). In the 1492 to 1800 cm⁻¹ window,
the share of variance that the CH scan predicts at the same 4.5 µm bins was 30 % for the linear
data, 15 % for the per-pixel normalisation and about 2 % for the normalised shape computed without
that bias: the per-pixel normalised signal was largely the bias. On spectra averaged over many
pixels the noise, and with it the bias, is small.

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
choice: SRS glass has its own non-resonant level (7 to 10 detector counts against 20 to 80 for
tissue in the raw P762 CH3 scan), so the two populations suit Otsu's threshold, whereas the triangle threshold of
the microscopy fluorescence path, made for a near-zero background, classed 99.6 % of the measured
area as tissue; display windows are the 0.35 and 99.65 percentiles over tissue. The channel
nearest 2850 cm⁻¹ (within 30 cm⁻¹) is green and the one nearest 2930 cm⁻¹ magenta, both shown;
without either the middle channel is shown.

The file-level `focus` MapAnnotation holds the fingerprint (method version, input SHA-256, sample,
modality, settings, every constant of `settings.calibration()`, library versions), the input
signature, the level-0 pixel digest of all images in page order, the skipped elements with their
reasons, the tissue fraction and `focus_display`. A regenerated output with the same digest records
the file it replaces, so alignments made on it stay valid. The digest covers the pixels only, not
the annotations: registration adds every image's placement and spot processing to its own cache keys
(§7).

---

## 7. Registration

`raman_pixel_interpolation` reads every image (`pixel_readout.raman_targets`): for image `k` with
placement \(A = [L \mid t]\) (§4.2; a legacy `x0,y0,sx,sy` is read as \(L = \mathrm{diag}(s_x,
s_y)\), \(t = (x_0, y_0)\)) the anchor spots, aligned to image-0 pixels, are mapped to
\(L^{-1}((x, y) - t)\) and the footprint geometry is fitted again from these coordinates. Only the
pixel box the footprints can reach is read (through tifffile's zarr view). Each spot receives the
Gaussian-weighted average of the measured (finite) pixels of the image inside its footprint, then
the image's offset removal and normalisation (§5.1); a spot with none gets NaN for that image's
channels. Files written before `raman-3` record no spot processing (their pixels were processed) and
are read as stored. Features are concatenated over the images; see
[Registration](registration_methods.md#4-raman-pixel-interpolation-raman_pixel_interpolation).

`pixel_composition` reads the images the same way. Its endmembers, shared by the acquisitions with
the same wavenumbers, are the archetypes of spot-sized block spectra inside the footprints with the
same offset removal and normalisation; their number is the Gavish-Donoho rank of these blocks (the
rank rule of §5) plus one. Each spot gets the fully constrained abundances of its mean spectrum after that
processing, and keeps its linear mean spectrum in `obsm['channel_mean']`. See
[Registration](registration_methods.md#5-pixel-composition-pixel_composition).

Both registrations add the placement and the spot processing of every image to their cache keys,
since the pixel digest (§6) does not cover them.

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
[^fischler1981]: Fischler M. A., Bolles R. C. Random sample consensus: a paradigm for model fitting with applications to image analysis and automated cartography. *Communications of the ACM* 24, 381-395 (1981). [doi:10.1145/358669.358692](https://doi.org/10.1145/358669.358692)
[^guizar2008]: Guizar-Sicairos M., Thurman S. T., Fienup J. R. Efficient subpixel image registration algorithms. *Optics Letters* 33, 156-158 (2008). [doi:10.1364/OL.33.000156](https://doi.org/10.1364/OL.33.000156)
[^otsu1979]: Otsu N. A threshold selection method from gray-level histograms. *IEEE Transactions on Systems, Man, and Cybernetics* 9, 62-66 (1979). [doi:10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076)
[^gavish2014]: Gavish M., Donoho D. L. The optimal hard threshold for singular values is 4/√3. *IEEE Transactions on Information Theory* 60, 5040-5053 (2014). [doi:10.1109/TIT.2014.2323359](https://doi.org/10.1109/TIT.2014.2323359)
