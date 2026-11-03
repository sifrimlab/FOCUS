# Microscopy Preprocessing Methods

Implementation reference for `focus/preprocessing/microscopy/` (method version `microscopy-3`).
`MicroscopyImageDataset.process_dataset()` preprocesses every sample through `MicroscopyImage`
with the same settings. A sample that raises is reported (`Sample skipped: <error>`) and left out of
the returned `{sample_id: path}` mapping; the other samples continue.

---

## 1. Objective

The module turns one microscopy acquisition per sample (brightfield H&E, IHC or special stains;
widefield, confocal or multiplexed fluorescence) into one OME-TIFF that downstream alignment and
registration read as is. Three rules apply:

- **Lossless.** The stored level-0 pixels are the source pixels: same sample type, every channel, no
  contrast change. FOCUS design choice: contrast enhancement and background fill change the values
  that feature extraction and any quantitative reading use, so enhancement is display metadata (§6)
  and the fill is opt-in (§5).
- **Metadata preserved.** Channel names, colours, wavelengths, fluorophores, acquisition mode,
  objective, pixel size and significant bits are carried into the OME-XML of the output, following
  the OME data model[^goldberg2005][^linkert2010].
- **Fingerprinted.** The output is reused only while its fingerprint matches (§8).

Two settings describe the acquisition and select every calibrated value:

- `image_type`: `auto` (from the metadata, §3), `brightfield` or `fluorescence`;
- `z_projection`: `auto`, `max`, `best_focus` or `middle` (§4).

| Calibrated value | `brightfield` | `fluorescence` |
|---|---|---|
| `z_projection: auto` | best-focus plane | maximum-intensity projection |
| Tissue detection (§5.2) | HSV saturation, median blur, Otsu | per-channel scaling, channel maximum, triangle |
| Background fill (`remove_background`) | sample maximum (white) | 0 |
| Display rendering (§6) | identity (RGB) or windowed gray | per-channel window and colour |
| Lossless codec (§7.2) | JPEG 2000 reversible (8-bit), zstd otherwise | zstd |
| `feature_extraction` registration | allowed | rejected |

The constants live in `technology.py` and enter the preprocessing fingerprint through
`calibration()`.

---

## 2. Input contract

One supported file per sample, in `{dataset}/{sample_id}/{modality}/`. Hidden files are ignored.
No file raises `FileNotFoundError` when the sample is constructed, which aborts the modality; several
files raise `ValueError`, so a sample never uses one of two acquisitions silently.

| Format | Extensions | Reader |
|---|---|---|
| OME-TIFF, BigTIFF | `.ome.tiff`, `.ome.tif`, `.ome.btf`, `.tiff`, `.tif`, `.btf` | tifffile |
| Akoya qpTIFF | `.qptiff` | tifffile |
| Aperio SVS, Hamamatsu NDPI, Leica SCN, Ventana BIF | `.svs`, `.ndpi`, `.scn`, `.bif` | tifffile |
| Zeiss CZI | `.czi` | pylibCZIrw |
| Nikon ND2 | `.nd2` | nd2 |

Formats outside this list (for example MIRAX `.mrxs` or Olympus `.vsi`) are converted to OME-TIFF
first, with `bfconvert` from Bio-Formats[^linkert2010] or the QuPath OME-TIFF export.

### 2.1 Lazy reading

Every reader exposes the acquisition as lazy `(T, Z, Y, X, C)` arrays: tifffile `aszarr` stores
(one chunk per stored tile or strip), pylibCZIrw region reads in 2048 px blocks, and the `nd2` dask
array. Pixels are read only when a step needs them, so memory is bounded by tiles and the
detection canvas, not by the slide. When the source stores its own lower resolutions (TIFF SubIFDs or
series levels, the CZI pyramid through pylibCZIrw zoom reads), they are exposed as further levels.

### 2.2 Series and scenes

One rule for every format: auxiliary images (series whose name contains `label`, `macro`,
`thumbnail`, `overview`, `map` or `preview`) are never chosen; among the others the series with the
most pixels is the acquisition. Other acquisition-sized series (at least half the area) are reported.
CZI scenes follow the same rule; ND2 stage positions other than the first are reported and not read.

### 2.3 Axes

Axes come from the reader metadata (tifffile series axes, CZI dimensions, ND2 sizes). `C` and `S`
(samples per pixel) form the channel axis. An unlabelled axis (tifffile `I` or `Q`, for example a
plain multi-page TIFF) becomes the channel axis when there is none, else depth, else time; each such
decision is reported. An image is RGB when its samples are photometric RGB or YCbCr (TIFF), BGR24 or
BGR48 (CZI) or RGB components (ND2); an alpha channel is dropped and reported.

### 2.4 Pixel size

The pixel size of the chosen series is read from the OME `PhysicalSizeX/Y` (with unit), the Aperio
`MPP`, the ImageJ unit with the resolution tags, the TIFF resolution tags (inch or centimetre), the
CZI `Scaling` distances or the calibrated ND2 voxel size. Values outside 0.01 to 100 µm are rejected.
Inch-based resolution tags equal to a screen or print default (72, 96, 150, 200, 240, 300, 600 or
1200 dpi) are placeholders written by image editors and are rejected as well.

When no valid value exists, the `pixel_size` setting (µm per pixel) is used; the source is then
recorded as `configuration`. The metadata value always wins; when both exist and differ by more than
5%, a warning reports the difference. Without either, the run continues with a warning: tissue areas
and margins use pixel fallbacks (§5) and the encoder cannot choose its level (§9).

---

## 3. Image type

`image_type` is resolved in this order, and the evidence is recorded as `focus_image_type_evidence`:

1. **Configuration.** `brightfield` or `fluorescence` is used as given; a contradicting metadata
   statement is appended to the evidence.
2. **Acquisition metadata.** An OME, CZI or ND2 channel acquisition mode (confocal, multiphoton,
   TIRF, SPIM, structured illumination and the other fluorescence modes of the OME schema), contrast
   method or illumination type (`Fluorescence`/`Epifluorescence` against `Brightfield`/`Transmitted`);
   an emission wavelength or fluorophore; the ND2 modality flags; a whole-slide brightfield scanner
   format (SVS, NDPI, SCN, BIF).
3. **Layout.** Two or more channels that are not RGB samples are fluorescence.
4. **Polarity.** For RGB and single-channel images without metadata, the middle plane is reduced to
   the detection canvas (§5.1) and its luma compared: the background is dark when the median of the
   border frame (outer 5% of the shorter side) is below the median of the image and below a third of
   its 99th percentile. Dark is fluorescence, bright is brightfield. FOCUS design choice: a border
   probe, since the scan border of a slide is background. This last step raises a warning that asks
   for an explicit `image_type`.

---

## 4. Time and depth

`T` is reduced to `T = 0`, with a warning when the file has several time points. `Z` follows
`z_projection` (`auto`: maximum projection for fluorescence, best focus for brightfield):

- `max`: maximum-intensity projection, computed block by block on the lazy array;
- `best_focus`: the plane with the highest variance of the Laplacian (the LAP4 operator of Pertuz et
  al.[^pertuz2013]), measured on a canvas of at most 2048 x 2048 px per plane averaged over channels;
- `middle`: plane `Z // 2`.

The rule and the plane used are recorded as `focus_z_projection`.

---

## 5. Tissue

Tissue is always detected: its outline is stored as an OME ROI (§7.3) and its pixels set the display
windows (§6). Detection never changes pixels; `crop_to_tissue` and `remove_background` (both off by
default) use it.

### 5.1 Detection canvas

Detection runs on a canvas of about 8 µm per pixel, the 1.25x base magnification of
HistoQC[^janowczyk2019]. FOCUS adaptation: the canvas keeps at least 1024 px on its short side, so a
small field of view is not reduced to a few pixels, and holds at most 9 MP. Without a pixel size only
the 9 MP cap applies.

The canvas is built from the finest stored level that is not finer than needed. The remaining
reduction is an area average: a power-of-two block mean computed chunk by chunk on the lazy array
(a power of two divides the tile and chunk sizes, so no rechunk holds many tiles in memory), then
`cv2.INTER_AREA` for the fractional rest.

### 5.2 Segmentation

**Brightfield, RGB.** The canvas is scaled to 8 bits by the sample range and converted to HSV. Stain
is saturated; glass and unscanned padding are not. Near-black regions (every channel at most 32 in
8 bits) connected to the image border are set to saturation 0, since compression noise in padding has
a high saturation; enclosed near-black regions (pigment) are kept. The saturation is median-blurred
and thresholded with Otsu's method[^otsu1979]. This is the CLAM segmentation (median kernel 7 px,
closing 4 px at its segmentation level)[^lu2021]. FOCUS adaptation: the threshold is Otsu's (CLAM's
`use_otsu` option) rather than a fixed saturation, and the kernels are expressed in µm at the 8 µm/px
canvas (median 56 µm, closing 32 µm).

**Brightfield, one channel.** The inverted intensity replaces the saturation, with the same padding
rule (border-connected near-black set to white) and Otsu's threshold.

**Fluorescence.** Each channel is divided by its own 99.9th percentile and clipped to [0, 1], so a dim
stain counts as much as a bright one; the channel maximum is smoothed with a Gaussian of 16 µm and
thresholded with the triangle method[^zack1977], which suits a histogram dominated by a dark
background peak. FOCUS design choice: the per-channel scaling and the channel maximum, so tissue seen
in any channel is tissue.

**Cleanup (both types).** Morphological closing (32 µm), removal of specks of at most 50 canvas
pixels, hole filling, then the external contours are kept when their area is at least 0.05 mm² (a
250 µm core). FOCUS design choice: the threshold is capped at a tenth of the largest piece, so the
largest piece and pieces of comparable size are always kept (TMA cores, small biopsies). Without a
pixel size the threshold is 1% of the canvas area, with the same cap. When nothing is detected, the
whole image is tissue and a warning is reported.

### 5.3 Crop and fill (opt-in)

`crop_to_tissue` crops to the bounding box of the tissue mask plus a margin of 100 µm (250 px without
a pixel size), clamped to the image. Canvas pixel `i` covers level-0 pixels `[i f, (i + 1) f)`, with
`f` level-0 pixels per canvas pixel. Pixels inside the box are unchanged. The box origin is recorded
as `focus_crop_offset` (`"x,y"` in level-0 source pixels).

`remove_background` sets every pixel outside the tissue mask (nearest-neighbour mapping of the canvas
mask to level 0) to the fill value of the image type: the sample maximum (white) for brightfield, 0
for fluorescence. The value is recorded as `focus_background_fill`. FOCUS design choice: the fill
follows the image type, so a fluorescence image is never filled with white.

---

## 6. Display rendering

Contrast is stored, not applied. `focus_display` (JSON) holds, per channel, the name, a colour
(`RRGGBB`), a window `[low, high]` in native values and a default visibility:

- RGB brightfield: identity windows (the full sample range) and the RGB colours.
- Other images: 0.35% of the tissue pixels of the canvas saturate at each end, the ImageJ automatic
  contrast default[^schneider2012]; all pixels are used when the mask covers less than 1% of the
  canvas. FOCUS design choice: tissue pixels only, so a dark background does not compress the window.
- Colours come from the metadata, else from the emission wavelength (below 480 nm blue, 480 to 500
  cyan, 500 to 550 green, 550 to 590 yellow, 590 to 650 red, above magenta), else blue for a nuclear
  stain name (DAPI, Hoechst, DRAQ5), else the ImageJ composite order (red, green, blue, cyan, magenta,
  yellow, white). FOCUS design choice: the usual pseudo-colours of DAPI, FITC/AF488, Cy3/AF555, AF594
  and Cy5/AF647.
- The first four channels are visible by default; the alignment view toggles the others.

The alignment view renders the stored display level (§9) with these windows: each shown channel is
scaled to [0, 1] by its window, multiplied by its colour and added, then clipped.

---

## 7. Output

### 7.1 Pyramid

The level-0 plane (after the optional crop and fill) is staged in zarr: RGB as `(Y, X, 3)`, other
images as `(C, Y, X)` with one 512 x 512 chunk per tile and channel. Each next level halves both sides
(floor) with a 2 x 2 area average, rounded to the nearest integer for integer types, until the image
fits in one tile. The levels are written as one OME-TIFF series: one page per channel (one interleaved
page for RGB) with the lower levels as SubIFDs, 512 x 512 tiles, the layout of the OME-TIFF pyramid
specification[^besson2019]. Every page carries TIFF resolution tags (pixels per centimetre) scaled by
the measured size ratio of its level, so non-OME readers keep the scale. The file is written to a
temporary sibling and renamed into place; peak memory is bounded by tile batches (64 MiB of tiles per
compression batch) and the staging chunks.

### 7.2 Sample type and compression

The sample type is the source's (`uint8`, `uint16`, `int16`, `uint32`, `float32` and the other OME
pixel types), written unchanged. Compression is lossless and readable by tifffile and Bio-Formats 8
(QuPath 0.7):

| Image | Codec |
|---|---|
| Brightfield, 8-bit | JPEG 2000 reversible (ISO/IEC 15444-1) |
| Every other image | zstd[^collet2021], horizontal predictor (floating-point predictor for floats) |

FOCUS design choice: JPEG 2000 reversible is about 40% smaller than zlib on H&E; JPEG-XL lossless is
about 8% smaller again but Bio-Formats 8.4 cannot read it. A source compressed with lossy JPEG (for
example an SVS) is decoded once and stored lossless, so the output is larger than the source.

### 7.3 OME-XML

The OME-XML is UTF-8 in the description of the first page (Bio-Formats does not resolve XML
character references there). One `Image` describes level 0:

- `Pixels`: dimension order `XYCZT`, the sample type, `SignificantBits` when the source records it,
  `PhysicalSizeX/Y` in µm when known, `Interleaved` for RGB; RGB is one `Channel` with
  `SamplesPerPixel="3"`, other images one `Channel` per page.
- `Channel`: name, colour (§6), excitation and emission wavelengths in nm, fluorophore, illumination
  type, acquisition mode and contrast method, when the source records them and the value is an OME
  enumeration value.
- `Instrument`/`Objective`: nominal magnification and numerical aperture, linked by
  `ObjectiveSettings`.
- `ROI` `tissue`: one `Polygon` per tissue piece, in level-0 pixels of the written image, simplified
  to one canvas pixel.
- `MapAnnotation` (namespace `focus`):

| Key | Value |
|---|---|
| `focus_preprocessing_fingerprint` | the fingerprint of §8 |
| `focus_input_signature` | JSON `{"stat", "sha256"}` of the source (§8) |
| `focus_pixel_digest` | SHA-256 over the level-0 pages as written (dtype, shape and bytes of each, in page order), streamed in row bands |
| `focus_crop_offset` | `"x,y"` (§5.3) |
| `focus_method` | `microscopy-3` |
| `focus_pixel_size_source` | `ome`, `aperio`, `imagej`, `tiff_resolution`, `czi`, `nd2`, `configuration` or `none` |
| `focus_image_type`, `focus_image_type_evidence` | §3 |
| `focus_z_projection` | §4 |
| `focus_source_series` | index of the chosen series or scene (§2.2) |
| `focus_background_fill` | `none` or the fill value (§5.3) |
| `focus_display` | §6 |
| `focus_superseded_signature` | written when the replaced output has the same pixel digest (§8) |

---

## 8. Cache key and regeneration

The fingerprint is the SHA-256 of the method version, the SHA-256 of the whole source file, the sample
and modality names, the settings (`image_type`, `z_projection`, `crop_to_tissue`,
`remove_background`, `pixel_size`), the calibration constants and the installed versions of NumPy,
SciPy, scikit-image, OpenCV, tifffile, imagecodecs, zarr, dask, ome-types and the format reader
(pylibCZIrw or nd2). The source SHA-256 stored in the output is reused while the source path, size and
modification time are unchanged, so an unchanged source is not re-hashed. When the output carries the
fingerprint and `force_recomputing` is false, it is reused.

Downstream caches identify the image by its pixel digest. When a regenerated image has the same
level-0 pixels as the output it replaces, the replaced file's identity is recorded as
`focus_superseded_signature`, so alignments made on it stay valid. When the pixels differ, a warning
reports that alignments made on the previous image must be redone. Outputs of `microscopy-2` stored
enhanced pixels by default, so their alignments are redone once.

---

## 9. Downstream use

- **Alignment display.** The alignment view loads the largest stored level within 9 MP and 256 MiB of
  native samples (all channels), so wide panels load a coarser level, and renders it with §6. Files
  written before `microscopy-3` keep the previous min-max conversion.
- **Feature extraction.** Prov-GigaPath was pretrained on H&E tiles at 0.5 µm per pixel[^xu2024]; the
  registration reads the stored level nearest 0.5 µm/px (see
  [registration methods](registration_methods.md#21-encoding-scale)). An image recorded as
  `fluorescence` is rejected, at configuration time when `image_type` is set and at registration time
  from `focus_image_type`. The background skip uses the recorded `focus_background_fill`.

---

## 10. Parameters and effective defaults

| Key | Values | Default |
|---|---|---|
| `image_type` | `auto`, `brightfield`, `fluorescence` | `auto` |
| `z_projection` | `auto`, `max`, `best_focus`, `middle` | `auto` |
| `crop_to_tissue` | boolean | `false` |
| `remove_background` | boolean | `false` |
| `pixel_size` | µm per pixel, or null | null |
| `force_recomputing` | boolean | `false` |

Removed in `microscopy-3`; configuration validation rejects them with the reason:
`color_enhancement`, `gamma`, `contrast_saturation` (contrast is display-only and automatic),
`background_color` (the fill follows `image_type`), `min_object_coverage`, `clip_percentile` (tissue
detection is calibrated in µm), `crop_margin` (a fixed 100 µm).

**Not offered.**

- Stain normalization (Macenko[^macenko2009], Reinhard[^reinhard2001]). FOCUS design choice: it
  rewrites the stored pixel values, against the lossless rule; it can be applied to the output
  downstream.
- Flat-field and illumination correction (BaSiC[^peng2017]). Shading is corrected at acquisition or
  stitching, where the individual tiles exist; a stitched mosaic no longer has them.
- Lossy codecs. FOCUS design choice: lossless storage keeps the output bit-identical to the source.

---

## References

[^goldberg2005]: Goldberg I. G. et al. The Open Microscopy Environment (OME) Data Model and XML file: open tools for informatics and quantitative analysis in biological imaging. *Genome Biology* 6, R47 (2005). [doi:10.1186/gb-2005-6-5-r47](https://doi.org/10.1186/gb-2005-6-5-r47)
[^linkert2010]: Linkert M. et al. Metadata matters: access to image data in the real world. *Journal of Cell Biology* 189, 777-782 (2010). [doi:10.1083/jcb.201004104](https://doi.org/10.1083/jcb.201004104)
[^besson2019]: Besson S. et al. Bringing Open Data to Whole Slide Imaging. In *Digital Pathology (ECDP 2019)*, Lecture Notes in Computer Science 11435, 3-10 (2019). [doi:10.1007/978-3-030-23937-4_1](https://doi.org/10.1007/978-3-030-23937-4_1)
[^pertuz2013]: Pertuz S., Puig D., Garcia M. A. Analysis of focus measure operators for shape-from-focus. *Pattern Recognition* 46, 1415-1432 (2013). [doi:10.1016/j.patcog.2012.11.011](https://doi.org/10.1016/j.patcog.2012.11.011)
[^janowczyk2019]: Janowczyk A. et al. HistoQC: an open-source quality control tool for digital pathology slides. *JCO Clinical Cancer Informatics* 3, 1-7 (2019). [doi:10.1200/CCI.18.00157](https://doi.org/10.1200/CCI.18.00157)
[^otsu1979]: Otsu N. A threshold selection method from gray-level histograms. *IEEE Transactions on Systems, Man, and Cybernetics* 9, 62-66 (1979). [doi:10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076)
[^lu2021]: Lu M. Y. et al. Data-efficient and weakly supervised computational pathology on whole-slide images. *Nature Biomedical Engineering* 5, 555-570 (2021). [doi:10.1038/s41551-020-00682-w](https://doi.org/10.1038/s41551-020-00682-w)
[^zack1977]: Zack G. W., Rogers W. E., Latt S. A. Automatic measurement of sister chromatid exchange frequency. *Journal of Histochemistry and Cytochemistry* 25, 741-753 (1977). [doi:10.1177/25.7.70454](https://doi.org/10.1177/25.7.70454)
[^schneider2012]: Schneider C. A., Rasband W. S., Eliceiri K. W. NIH Image to ImageJ: 25 years of image analysis. *Nature Methods* 9, 671-675 (2012). [doi:10.1038/nmeth.2089](https://doi.org/10.1038/nmeth.2089)
[^collet2021]: Collet Y., Kucherawy M. Zstandard Compression and the 'application/zstd' Media Type. RFC 8878 (2021). [doi:10.17487/RFC8878](https://doi.org/10.17487/RFC8878)
[^xu2024]: Xu H. et al. A whole-slide foundation model for digital pathology from real-world data. *Nature* 630, 181-188 (2024). [doi:10.1038/s41586-024-07441-w](https://doi.org/10.1038/s41586-024-07441-w)
[^macenko2009]: Macenko M. et al. A method for normalizing histology slides for quantitative analysis. *2009 IEEE International Symposium on Biomedical Imaging*, 1107-1110 (2009). [doi:10.1109/ISBI.2009.5193250](https://doi.org/10.1109/ISBI.2009.5193250)
[^reinhard2001]: Reinhard E., Ashikhmin M., Gooch B., Shirley P. Color transfer between images. *IEEE Computer Graphics and Applications* 21, 34-41 (2001). [doi:10.1109/38.946629](https://doi.org/10.1109/38.946629)
[^peng2017]: Peng T. et al. A BaSiC tool for background and shading correction of optical microscopy images. *Nature Communications* 8, 14836 (2017). [doi:10.1038/ncomms14836](https://doi.org/10.1038/ncomms14836)
