# Preprocessing Audit

Audit of the preprocessing workflows of the four modalities (MSI, ST, microscopy, Raman) after the
refactor `b9e3412` to `b3e4d81`, dated 2026-11-03. It covers refactor equivalence, defects,
scientific gaps, robustness and performance. File references are `path:line` at `b3e4d81`.

Verification labels: **R** reproduced by a controlled run, **A** reproduced by a reviewer harness,
**C** confirmed by reading the code.

## 1. Refactor equivalence

### 1.1 Method

- Test suite: 149 tests pass on `b3e4d81` (pytest is not installed in the `FOCUS` environment; it
  was run from a scratch install).
- Old against new runs: `b9e3412` extracted with `git archive` and run beside `b3e4d81` on the same
  inputs. A comparer checked `X`, every layer, `obs`, `var`, `obsm` and the full `uns`, fingerprints
  included.

| Modality | Inputs | Settings | Result |
|---|---|---|---|
| ST | 2 samples cut from `MEL.h5ad` (125k spots, 19k genes; the second with a different panel and noise) | every filter, mitochondrial removal, normalization and log1p; then `sub_cellular` with defaults | 6 of 6 files identical |
| MSI | real positive and negative subsets (80 x 80 px, 4,137 spectra each, crossing the tissue edge) | TIC with background detection at 10 ppm; then CLR at 5 ppm, no background, `sub_cellular` | 4 of 4 files identical |
| Microscopy | synthetic RGB OME-TIFF with a pixel size; 16-bit 3-channel IF | defaults; black background | every pyramid series, the OME-XML and all FOCUS annotations identical |
| Raman | no `.lif` file available | synthetic LIF harness and the real `FOCUS_ASHLAR` environment | every stage identical |

- Five independent function-by-function syntactic comparisons found only renames and type
  annotations. Defaults, enums, paths, fingerprint payloads and cache-skip logic match.
- Caches: `b3e4d81` reuses outputs written by `b9e3412`, so existing projects are not sent back to
  alignment.

### 1.2 Changes introduced by the refactor

| Severity | Issue |
|---|---|
| Critical | Git tracks `src/focus/GUI/` (59 files) while every import uses `focus.gui`. macOS hides the mismatch (`core.ignorecase=true`). A clean `git archive HEAD` raises `ModuleNotFoundError: No module named 'focus.gui'` at `src/focus/alignment/coordinator.py:26`, which breaks Linux CI, Docker, the wheel and remote hosts. Fix: `git mv src/focus/GUI src/focus/gui_tmp && git mv src/focus/gui_tmp src/focus/gui`. |
| Low | The warning that `min_intensity_threshold` is ignored was dropped (`b9e3412` `lipidomics.py:3445`); the setting is now ignored silently (`src/focus/preprocessing/msi/adapter.py:73`). |
| Low | Stale names: the MSI logger is still `focus.preprocessing.lipidomics` (`src/focus/preprocessing/msi/settings.py:9`); the docs still reference `utils.*`, `RamanImage` class constants and "orchestrator". |
| Info | Old import paths are removed without aliases (`focus.constants`, `BaseSample`/`BaseDataset`, the three-field `ModalityHandler`). External scripts and notebooks that import them fail. |

## 2. Defects predating the refactor

### 2.1 Critical and high

| # | Modality | Defect | Check |
|---|---|---|---|
| 1 | Microscopy | Default colour enhancement can zero colour channels. The percentile estimate subsamples the flattened interleaved `(H, W, C)` values with stride `ceil(n / 9e6)`; a stride that is a multiple of `C` samples one channel only. A synthetic 3600 x 4200 H&E with default settings turns tissue `(160, 90, 170)` into `(18, 0, 48)`, green 0 over all tissue. About one image size in three is affected; present since `9f5e07f`. `src/focus/preprocessing/microscopy/color.py:38-41` | R |
| 2 | ST | The cross-sample gene filter, the union gene panel and the merged normalization are bypassed on two of the three paths that build the final MuData. With annotations, the anchor is an inner-join concatenation of per-sample files (`src/focus/pipeline/annotations.py:95`), which also drops `uns['spot_size']`. As a registration target, the per-sample files are registered and merged with an inner join (`src/focus/registration/merging.py:63`). `min_spots_per_gene` is ignored and every gene absent from one sample is dropped. | C, A |
| 3 | MSI | imzML files without `3DPosition*` stage positions get transposed physical coordinates: `pixel_coordinates` is `(y, x)` and is multiplied by `raster_size` `(s_x, s_y)` (`src/focus/preprocessing/msi/imzml.py:220-227`). The axes swap and each axis takes the other axis pixel size. | C, A |
| 4 | Microscopy | 3-channel fluorescence is segmented as brightfield. The dark-background probe runs only for 1- and 2-channel input (`src/focus/preprocessing/microscopy/tissue.py:103-113`) and works on 8-bit values, so low-range 16-bit fluorescence ties at 0. The mask covers the whole image; tissue touching the border is erased. | R |
| 5 | Microscopy | `min_object_coverage=0.01` is relative to the whole image, so small pieces (TMA cores, biopsies) are discarded; when every object is discarded and cropping is off, the output is all background (`src/focus/preprocessing/microscopy/tissue.py:160-179`). | A |
| 6 | Microscopy | The padding guard matches exact black only. Near-black scanner padding (values 1 to 5 after JPEG compression) becomes the brightest class after inversion, so the padding is segmented as tissue and the crop lands on it (`src/focus/preprocessing/microscopy/tissue.py:115-118`). | A |
| 7 | MSI | Binary compression and spectrum representation are not checked. A zlib-compressed IBD is decoded as raw floats and profile spectra are processed as peak lists, without an error (`src/focus/preprocessing/msi/payload.py:145`). | C |
| 8 | Raman | Two spectral windows that do not overlap, with the second below the first in the concatenation order, delete the whole first scan: `closest_idx` resolves to 0 (`src/focus/preprocessing/raman/reader.py:122-152`). | C, A |
| 9 | Raman | ASHLAR receives the cycle 1 alignment channel for every cycle. A later cycle with fewer channels fails with `Invalid C index: 5/3`; otherwise later cycles are aligned on an unrelated band (`src/focus/preprocessing/raman/stitching.py:181-186`, `tools/ASHLAR/main.py:84-88`). | A |

### 2.2 Medium

- Raman: non-square tiles raise `IndexError`; the coordinate grid uses `xy` indexing against a
  `(C, X, Y)` reshape (`src/focus/preprocessing/raman/spectra.py:43-64`). R. A vectorized restore
  (`out.reshape(C, -1)[:, keep] = processed.T`) removes the loop and the defect.
- Raman: tile sizes that are not multiples of 16 fail in `tifffile` with `tile=`
  (`src/focus/preprocessing/raman/stitching.py:243-248`). A.
- Raman: a spectra slice shorter than the Savitzky-Golay window (7) raises `ValueError`. A.
- Raman: the LIF image index differs from the depth-first order of `readlif` when "Merged" child
  images exist, so a wrong image can be read (`src/focus/preprocessing/raman/lif_metadata.py:21-29`).
  A, synthetic XML only.
- Raman: one failed BaSiC channel does not cancel the other channels, and stderr goes to
  `/dev/null`, so the report is `exit status 1` only
  (`src/focus/preprocessing/raman/correction.py:42-62`). A.
- Microscopy: channels after the third are dropped without a warning
  (`src/focus/preprocessing/microscopy/reader.py:238-239`); every 3-channel image, IF included, is
  written with RGB photometric interpretation and loses its channel names
  (`src/focus/preprocessing/microscopy/pyramid.py:172`). R.
- Microscopy: 4-D TIFF stacks (Z or T) fail; CZI keeps `C=0` and `Z=0` only
  (`src/focus/preprocessing/microscopy/reader.py:83-89`); the installed `czifile` 2019.7.2.3 fails on
  numpy 2.4 for LZW subblocks; 300 and 600 dpi placeholder resolutions are accepted as 84.7 and
  42.3 µm per pixel (`src/focus/preprocessing/microscopy/metadata.py:28`) although the docs state
  they are rejected. A.
- MSI: imzML parsing depends on the literal parameter-group ids `mzArray` and `intensities`, fails
  on integer intensity arrays and on a pixel size written as `"20.0"`, stores the pixel size as
  `int16` (no fractional sizes), and replaces a missing pixel size with 1 µm silently
  (`src/focus/preprocessing/msi/imzml.py:155-177`). C.
- MSI: when background detection finds no foreground, the log reports "all foreground" but the
  sample contributes no spectra to reference selection or the backbone
  (`src/focus/preprocessing/msi/payload.py:116-121`). A.
- MSI: an m/z of 0 raises `ZeroDivisionError` in binning
  (`src/focus/preprocessing/msi/binning.py:36`). A.
- ST: an input `layers['raw']` replaces the merged counts
  (`src/focus/preprocessing/transcriptomics/dataset_operations.py:78`). A.
- ST: a missing `spot_size` becomes `[1, 1]` without a warning
  (`src/focus/preprocessing/transcriptomics/sample.py:93-95`), so the registration warning for a
  missing `spot_size` never fires. C.
- Shared clustering: the valley-ratio histogram range is the data min and max, so one outlier hides
  a real valley (ratio 0.05 becomes 0.95) and forces a merge
  (`src/focus/spatial/clusters.py:46-55`). A.

### 2.3 Low

- MSI validation rejects `mass_tolerance=10.0` (declared a float by the GUI) and
  `frequency_threshold` of 0 or 1 (`src/focus/preprocessing/msi/validation.py:35-44`).
- MSI: a resumed run differs from a fresh run by about 1e-3 ppm in the recalibration values.
- ST merged QC: after mitochondrial removal the merged `pct_counts_mt` is 0 everywhere; zero-count
  spots get NaN; log1p without normalization upcasts to float64.
- ST input selection takes the first `.h5ad` in sorted order, so `square_002um` precedes
  `square_008um` in Visium HD exports.
- Non-finite coordinates fail with an opaque SciPy error (`src/focus/spatial/smoothing.py:65`,
  `src/focus/spatial/units.py:44`).
- Raman: an unreachable `returncode` check after `check=True`, the conda environment matched by
  substring, stale ASHLAR inputs picked up by the input glob.

## 3. Scientific gaps and outdated methods

### 3.1 Cross-cutting

- Microscopy defaults `color_enhancement`, `remove_background` and `crop_to_tissue` to `True`,
  against the opt-in convention of the other modalities.
- The enhanced and background-filled pixels are the input of Prov-GigaPath, which was pretrained on
  native H&E tiles[^xu2024]. Storing unenhanced pixels and applying enhancement for display only
  keeps the encoder input in its training distribution.
- `docs/scientific/microscopy_methods.md` and `docs/scientific/raman_methods.md` cite no source.
  BANKSY[^singhal2024], scanpy[^wolf2018] and Otsu[^otsu1979] are uncited in the ST and MSI pages.

### 3.2 ST

- CP10k and log1p are adequate as opt-in steps. Median-depth scaling (`target_sum=None`) suits
  low-depth bins. Library size carries biological signal in spatial data[^bhuva2024]; analytic
  Pearson residuals[^lause2021] are a further opt-in candidate.
- Xenium control features (`NegControl*`, `BLANK_*`, `UnassignedCodeword_*`) are not excluded and
  inflate QC totals; `var['feature_types']` identifies them.
- No reader exists for 10x HDF5, Space Ranger outputs, Visium HD `binned_outputs` or Xenium bundles;
  `spot_size` could be derived from the scale factors.

### 3.3 Spatial clustering (alignment display only)

- Sharp domain boundaries produce clusters one unit wide: two true domains yield 4 to 6 clusters.
  The valley merge does not remove them (ratio 0.000 on the combined embedding, 1.000 on the
  unit's own principal components) and `label_coherence` (0.93 to 0.97) does not register them. A
  majority or Potts smoothing pass on the labels removes them.
- The merge docstring describes a discriminant projection; the code projects on the
  centroid-difference axis. Hennig[^hennig2010] uses discriminant coordinates; a shrinkage LDA axis
  is inexpensive.
- The BANKSY adaptation omits the azimuthal Gabor filter term and Gaussian neighbour weights and
  computes PCA before smoothing[^singhal2024]; the docs should label it a FOCUS adaptation.
- The presets were calibrated by maximizing `label_coherence`, a self-referential criterion. ARI
  against an annotated benchmark such as DLPFC[^maynard2021] is an external check.

### 3.4 MSI

- Core methods are sound and cited: MAD noise, XCMS-style grouping[^smith2006], per-pixel
  recalibration, median normalization[^fonville2012], TIC[^deininger2011].
- Stale documentation: the microgrid section describes Otsu, the code uses a background-ion model
  with robust z-scores and hysteresis; the "most intense peak in the window" rule is attributed to
  MALDIquant[^gibb2012], which matches the closest peak.
- Background detection runs before recalibration, so drifting calibrants can be classified as
  cell ions.
- No absolute mass anchoring, for example to PC 34:1 [M+H]+ at m/z 760.5851.
- Annotation is a plain ppm window, without isotope or adduct logic and without FDR control
  (METASPACE[^palmer2017]).
- The noise gate has no location term; median + k MAD is the convention.
- CLR is computed over nonzero values only, which is not the Aitchison CLR[^aitchison1982].

### 3.5 Raman

- The wavenumber formula and the pump-wavelength metadata indicate coherent Raman (SRS or CARS on a
  Leica CRS system). The RamanSPy chain (Whitaker-Hayes, Savitzky-Golay, IASLS, min-max)[^georgiev2024]
  targets spontaneous Raman on CCD detectors.
  - CARS needs Kramers-Kronig removal of the non-resonant background[^camp2016].
  - SRS needs per-wavelength laser power normalization.
  - Cosmic-ray despiking does not apply to PMT or lock-in detection.
- Per-pixel min-max normalization followed by `uint8` storage removes abundance and stretches noise.
  L2, area or SNV normalization stored as `float32` or `uint16` preserves it.
- Wavenumbers are neither calibrated nor stored; features are named `Channel_N`.

### 3.6 Microscopy

- Luma Otsu tissue detection is dated. CLAM[^lu2021] and HistoQC[^janowczyk2019] threshold
  saturation with area limits in µm², which also addresses defects 5 and 6.
- `INTER_AREA` pyramid downsampling is correct (anti-aliased).

## 4. Robustness and performance

- Microscopy holds the full slide in memory, about 39 bytes per source pixel (230 to 390 GB for a
  40x whole-slide image). Detection on a stored low-resolution level, reading only the crop box
  through `aszarr`, and tiled output (currently strips, `rowsperstrip=11`, with a duplicate
  `ImageDescription` tag) remove the limit.
- MSI builds dense n x F matrices before CSR conversion (peak above 15 GB at 183k x 6k); the merge
  reloads the merged file to rewrite three `uns` keys (`src/focus/preprocessing/msi/merging.py:51`).
  Emitting CSR from the numba kernel removes the dense stage.
- Raman keeps every sample cube in memory with about five copies per sample, starts one
  `conda run` and one JAX import per channel, and caches on file existence only.
- ST merge peaks at 3 to 4 times the matrix size; every run re-hashes the whole input (gigabytes for
  HD data). Memoizing the hash on size and mtime removes the cost.
- The alignment cache key includes library versions and the display-only `resolution` preset
  (`src/focus/alignment/cache.py:95-110`), so a scanpy patch release or a preset change reopens
  manual alignment. Keying alignment on geometry (obs names, coordinates, `spot_size`) and keeping
  the full fingerprint for registration avoids it.
- Writes are not atomic although `write_h5ad_atomic` exists (`src/focus/io/anndata.py:86`).
- Untested areas: tissue detection, colour enhancement, CZI and ND2 loaders, non-square Raman tiles,
  multi-cycle ASHLAR, the ST annotation and registration merges, real ST and MSI fingerprint hashes.

## 5. Fix status

| Item | Status |
|---|---|
| 1.2 GUI directory case | Fixed (`0a92ee7`) |
| 2.1 defects 1 to 9 | Fixed, see section 6 |
| ST items of 2.2, 2.3, 3.2 and 4 | Fixed, see section 7 |
| Microscopy items of 2.2, 3.1, 3.6 and 4 | Fixed, see section 8 |
| MSI items of 1.2, 2.2, 2.3, 3.4 and 4 | Fixed, see section 9 |
| Shared clustering items of 2.2 and 3.3 | Fixed, see section 10 |
| Other items of 2.2, 2.3, 3, 4 | Open |

Not verified: Raman on real `.lif` files, the real LAS X LIF tree layout, and the physically correct
Y-flip convention for Raman stitching.

## 6. Fixes to the critical and high defects

The test suite passes with 172 tests (149 existing, 23 added for these fixes).

| # | Fix | Cache effect | Verification |
|---|---|---|---|
| 1 | The percentile subsample stride is raised to the next integer coprime with the channel count (`color.py`). Strides already coprime are unchanged. | `METHOD_VERSION` `microscopy-2`: every image is rebuilt. An image whose pixels do not change keeps its pixel digest, so its alignment stays valid. | Strides 6 and 3 match the exact percentiles (error below 5e-5). The synthetic 3600 x 4200 H&E tissue changes from `(12, 0, 45)` to `(131, 4, 148)`. A 2000 x 2400 crop gives pixels identical to `b3e4d81`. |
| 2 | Spot registration reads each sample's rows of the merged preprocessed target (`registration/targets.py`, both spot engines). The merged annotated file is the merged preprocessed reference with the labels added to `obs`, written without loading the matrices (`pipeline/annotations.py`). Both fall back to the per-sample files when no merged file exists. | The target signature becomes the merged preprocessing fingerprint, so every spot-target registration is recomputed once. Annotations are rebuilt on every run. | On the two-sample MEL output, the per-sample panels of 19,057 and 15,245 genes are replaced by the merged 11,591-gene panel, with identical spots and coordinates. Tests cover the union panel, `uns['spot_size']` and the label placement. |
| 3 | The pixel grid is reordered to `(x, y)` before scaling by the raster size (`msi/imzml.py`). | Only inputs without stage positions change: their fingerprint gains a `"coordinates": "pixel_grid_xy"` marker (`msi/provenance.py`, same single read as the imzML digest). Datasets with stage positions keep their key. | Grid coordinates now match stage coordinates. The real positive and negative subsets give byte-identical outputs, fingerprints included. |
| 4 | The polarity probe runs for every channel count on the float luma. The border must also be below a third of the 99th-percentile luma. Dark-background images are scaled to their 99.9th percentile before 8-bit conversion (`tissue.py`). | `microscopy-2` | 3-channel IF (full range and 12-bit in a 16-bit container, raw and enhanced) is segmented. Brightfield masks are unchanged. The synthetic IF sample is now cropped to tissue. |
| 5 | The area threshold is capped at a tenth of the largest contour, so the largest object is always kept. | `microscopy-2` | TMA cores of 0.07% of the slide are kept. Debris below 1% next to a large section is still removed. |
| 6 | Near-black regions (all channels at most 32) connected to the image border are treated as padding; enclosed near-black pigment is kept. | `microscopy-2` | Padding with 8-bit noise of 1 to 5, raw and enhanced, stays background. Enclosed pigment and normal sections give masks identical to `b3e4d81`. |
| 7 | A declared compression other than `no compression` raises `ValueError` (`msi/imzml.py`). The spectrum representation is decided from the stored spectra (`msi/representation.py`): a resolved profile is centroided once per run (`msi/centroiding.py`), a declared profile too coarsely sampled to pick and a mixed file raise `ValueError`, a mislabeled header only warns. | None for centroid files: valid inputs are unchanged. Profile files, refused before, are read. | The real test data (centroid, no compression) passes. Tests cover zlib, every header and content combination, picking accuracy and the end-to-end equivalence of a profile and a centroid dataset. |
| 8 | Overlap trimming requires the wavenumber at the break to lie within the range of the preceding channels (`raman/reader.py`). | Raman caches on file existence: affected outputs need `force_recomputing`. | Disjoint windows in both orders are kept; a true overlap is still trimmed. |
| 9 | One alignment channel per cycle, indexed within the cycle, passed comma-separated. `tools/ASHLAR/main.py` sorts cycles by number and uses each cycle's own channel. | Raman caches on file existence: affected outputs need `force_recomputing`. | The real `FOCUS_ASHLAR` environment stitches the 10-channel plus 3-channel case into a `(13, 112, 112)` mosaic, where it previously failed with `Invalid C index: 5/3`. |

## 7. ST preprocessing redesign

The ST pipeline (input to merged AnnData) was rebuilt around two technology settings, `capture`
(`sequencing`, `imaging`) and `unit` (`spot`, `bin`, `cell`), which calibrate quality control, the
gene rule, the default normalization and the clustering preset. The numeric settings were removed
and fail validation with the name of their replacement. Method version `st-5`: every ST output is
rebuilt once and every ST alignment pair is re-aligned once. See `docs/scientific/st_methods.md`.

| Audit item | Resolution |
|---|---|
| Input `layers['raw']` replaces the merged counts (2.2) | Counts are resolved at load from `.X`, `layers['counts']`, `layers['raw']` or `.raw` (first integer matrix); every other input matrix is dropped. Per-sample files hold raw counts only. |
| Missing `spot_size` becomes `[1, 1]` (2.2) | `uns['spot_size']` is required; a missing value raises `ValueError`. |
| Merged `pct_counts_mt` is 0 after removal; NaN for zero-count spots; log1p upcasts to float64 (2.3) | Merged QC keeps the per-sample mitochondrial and control metrics; zero-count spots are removed per sample; normalized `.X` is float32. |
| First sorted `.h5ad` is used (2.3) | Several `.h5ad` files in a sample folder raise `ValueError`. |
| CP10k as the only normalization; no control-probe removal (3.2) | Catalog `auto`, `none`, `log_normalize` (median depth), `cp10k_log`, `size_factors`, `area_log_normalize`, applied once to the merged dataset with one global scale. Control features are recorded in `obs['pct_counts_control']` and removed. |
| QC by user thresholds | Structural cleanup (out of tissue, zero counts) plus spatially aware local outliers for spots (SpotSweeper), 5-MAD global outliers for cells, none for bins. |
| Input re-hashed on every run; non-atomic writes; merge memory (4) | The input SHA-256 is reused while the input stat is unchanged; per-sample and merged writes are atomic; the gene rule reads `var['n_cells_by_counts']` and subsets the merged matrix in place. |
| `spot_interpolation` of a normalized ST target | Raw counts are shared by footprint overlap and the merged registered file is normalized once with the target's method. |

Verified on two MEL Visium HD 16 µm samples (`sequencing`, `bin`): 12,905 of 19,070 union genes kept,
merged `.X` float32 `log_normalize` at a median library size of 485, `pct_counts_mt` nonzero, no NaN
in `obs`, merged rows equal to the per-sample rows, and a second run fully cached without re-hashing.
Readers for 10x HDF5, Space Ranger, Visium HD `binned_outputs` and Xenium bundles remain out of
scope: the input contract is an AnnData with raw counts, `obsm['spatial']` and `uns['spot_size']`.

## 8. Microscopy preprocessing redesign

The microscopy pipeline (source file to per-sample OME-TIFF) was rebuilt around two acquisition
settings, `image_type` (`auto`, `brightfield`, `fluorescence`) and `z_projection` (`auto`, `max`,
`best_focus`, `middle`), and one metadata fallback, `pixel_size`. Stored pixels are the source pixels;
contrast is display metadata; tissue is detected and stored as an OME ROI; crop and background fill are
opt-in. Method version `microscopy-3`: every microscopy output is rebuilt once, and alignments made on
`microscopy-2` outputs written with the old defaults (enhanced pixels) are redone once. See
`docs/scientific/microscopy_methods.md`.

| Audit item | Resolution |
|---|---|
| Channels after the third dropped; 3-channel IF written as RGB without channel names (2.2) | Every channel kept with its OME channel metadata; RGB only for photometric RGB, YCbCr, BGR or ND2 RGB samples. |
| 4-D stacks fail; CZI keeps `C=0`, `Z=0`; `czifile` fails on numpy 2.4 (2.2) | Axes from the reader metadata; `z_projection` preset; one series rule for every format; CZI read with pylibCZIrw. |
| 300 and 600 dpi placeholders accepted (2.2) | Inch resolutions at screen or print defaults are rejected; `pixel_size` is the fallback. |
| Enhancement, fill and crop on by default; enhanced pixels fed to Prov-GigaPath (3.1) | Lossless defaults; per-channel display windows in `focus_display`; fill and crop opt-in, the fill value set by the image type. |
| No citations in the microscopy methods page (3.1) | Every step attributed; FOCUS adaptations and design choices labelled. |
| Luma Otsu detection with pixel-relative areas (3.6) | Saturation and Otsu for brightfield (CLAM, HistoQC), per-channel scaled channel maximum and triangle threshold for fluorescence, kernels and areas in µm on an 8 µm/px canvas. |
| Full slide in memory, about 39 bytes per source pixel; strips with a duplicate description (4) | Lazy reading, canvas detection on stored levels, zarr staging, tiled SubIFD pyramid. A 24,000 x 24,000 RGB slide peaks at 0.73 GB (about 22 GB before). |
| Output not recognised as a pyramid or as OME-TIFF by Bio-Formats | One series with SubIFD levels; UTF-8 OME-XML (Bio-Formats read the former `&#181;m` unit as invalid and fell back to plain TIFF). Verified with Bio-Formats 8.4 (QuPath 0.7): OME-TIFF reader, all channels, every level. |
| Image-type polarity guessed for every image | Configuration, then acquisition metadata, then layout, then polarity with a warning. |

Also changed downstream:

- The alignment view renders the stored display level with the saved windows and colours and toggles
  channels (`?channels=` on the payload route). Before, 1- and 2-channel images showed their last
  channel and every image used a global min-max.
- Image-to-image alignment crops by the series axes (RGB was cropped on the wrong axes) and keeps axes,
  pixel size and channel names.
- `feature_extraction` rejects fluorescence images (configuration and registration time); its
  background skip follows the recorded fill.
- The standalone grid feature cache includes the image pixel digest; changed pixels are re-encoded.
- Annotation transfer adds the recorded crop offset of an image annotation modality to the aligned
  coordinates, so GeoJSON drawn on the source acquisition matches a cropped image
  (`src/focus/pipeline/annotations.py`, `AnnotationStage.source_offset`).

Out of scope, still open:

- No registration type for fluorescence images (per-channel intensity in the anchor footprint).
- Pen-mark detection; flat-field correction of stitched mosaics.

## 9. MSI preprocessing redesign

The MSI pipeline was rebuilt around one experiment setting, `sample_type` (`tissue`, `tissue_noisy`,
`cell_culture`, `microgrid`), and one numeric setting, `mass_tolerance`. The steps run in a new order:
calibrants from every spectrum, recalibration, foreground on recalibrated m/z, then per-sample
features selected by frequency, annotation and S/N against the background, merged into the backbone
and binned by Gaussian responsibilities. `detect_background`, `frequency_threshold` and
`min_intensity_threshold`, and the normalizations `log`, `clr` and `tic_mean_scaled`, fail validation
with the reason. Method version `msi-13`: every MSI output is rebuilt once and every MSI alignment pair
is re-aligned once. See `docs/scientific/msi_methods.md`.

| Audit item | Resolution |
|---|---|
| `min_intensity_threshold` ignored silently (1.2) | Rejected by validation; replaced by the S/N filter. |
| Logger `focus.preprocessing.lipidomics` (1.2) | `focus.preprocessing.msi`. |
| imzML: literal group ids, integer intensities, `"20.0"` pixel size, `int16` size, silent 1 µm (2.2) | Arrays by `m/z array` / `intensity array` terms; integer intensities read as float32; decimal float32 pixel size; missing size inferred from stage positions, otherwise `ValueError`. |
| "All foreground" logged but no spectra used (2.2) | Every spectrum with peaks is foreground, with a warning, and feeds the features. |
| m/z 0 raises `ZeroDivisionError` (2.2) | Peaks at m/z 0 or below are dropped at read. |
| `mass_tolerance=10.0` rejected (2.3) | Int or float accepted. |
| Resumed run differs by about 1e-3 ppm (2.3) | Cause: a resume aligned the queued samples against the cached samples' 10 references, a fresh run against their pools of up to 200 ions, and in the other pair orientation. Pools and shifts are stored (`uns['recalibration_pool']`), held fixed in dataset order, and the backbone is binned from its stored float32 values: a resumed run equals a fresh one bit for bit (test). |
| Background detection before recalibration (3.4) | Recalibration first; calibrants from every spectrum (matrix ions are eligible, which a microgrid needs). |
| Detection gated on the lipid DB | Always runs, without the database. |
| Stale docs: microgrid Otsu, "most intense peak" credited to MALDIquant (3.4) | Methods page rewritten; the most-intense rule is a labelled FOCUS adaptation. |
| Noise gate without a location term (3.4) | Median + k x 1.4826 MAD. |
| CLR over nonzero values only (3.4) | Removed; normalizations divide by one divisor at a fixed scale (`tic`, `median`, `rms`, `internal_standard`). |
| Annotation without FDR control (3.4) | Decoy-shift estimate of chance annotations, with a warning above half. Isotope and adduct logic remain out of scope. |
| Dense n x F matrices; merge reloads the merged file (4) | The numba kernel writes CSR; the merged `uns` entries are written in place; writes are atomic. |

Foreground detection was rebuilt. The old score (entropy, peak count, log TIC) rises on a background of
noise centroids. The new score counts the recurrent ions a spectrum holds above its noise. A background
is separated only when BIC prefers two components, Ashman's D is at least 2 and the lower component
holds at most half the ions of the higher one. The last two rules were added after the real data below:
BIC split two tissue regions of about 350 and 390 ions per spectrum (D 0.85 on a 100 x 100 window).

Gaussian binning: an overlap-capped kernel that also attenuates isolated features was implemented
first and rejected on the real data. Its centroids sit on a 5 ppm sampling grid, and the per-line
recalibration offsets moved them against the Gaussian. Against nearest-feature binning, intensities
dropped 3 % to 19 %, scan lines differed by up to 20 % and the PE 38:4 image roughness rose from 0.23
to 0.52. With responsibilities, isolated features equal nearest-feature binning exactly (ratio 1.000
for PC 34:1, PC 32:0, PI 38:4, PE 38:4) and only features closer than the tolerance share peaks.

Verified on the dual-mode tissue acquisition in `test_data/MSI` (Bruker TOF, SCiLS export, 182,865
spectra per mode, 10 x 20 µm, `tissue`, `tic`, 10 ppm), against `5177d8a` (`msi-12`) on the
same machine:

| | `msi-12` | `msi-13` |
|---|---|---|
| Wall time | 305 s | 253 s |
| Peak RSS | 13.2 GB | 11.2 GB |
| Features (pos / neg) | 1,730 / 4,028 | 1,729 / 4,033 |
| Non-zero entries | 191.86 M | 191.94 M |
| Per-sample file | 1.34 GB | 1.34 GB |

The acquisition covers the section only (no off-tissue spectra). Every spectrum is foreground in both
modes (D 0.17 and 0.49), and the S/N filter is skipped for lack of a background, as designed. The
background, noisy-background, culture and microgrid cases are covered by synthetic tests
(`tests/msi/test_foreground.py`, `test_intensity_filter.py`, `test_process_dataset.py`). The suite
passes with 241 tests.

Out of scope, still open:

- Isotope, adduct and FDR-controlled annotation.
- Peak memory is dominated by the spectra held per sample (about 4.7 GB of IBD here) and their
  recalibrated copies; streaming the binning per block of spectra would bound it.

## 10. Alignment clustering redesign

ST and MSI labels for the alignment GUI now come from one routine, `focus.spatial.clustering.cluster_spots`,
configured by the modality presets, with an edge-preserving neighbourhood embedding. Method version `clusters-2`, with
ST `st-6` and MSI `msi-14`: every ST and MSI output is rebuilt once, and every ST and MSI
alignment pair is aligned again once (the alignment key is still the preprocessing fingerprint).
See `docs/scientific/spatial_clustering.md`.

| Item | Resolution |
|---|---|
| Two parallel pipelines (ST `native_cluster_labels`, MSI `compute_cluster_labels`) | One routine; the modality supplies the spots that take part, the unit eligibility and the representation. MSI gains eligibility, provenance and colours like ST. |
| Labels computed on raw counts with an internal normalization | Labels follow the configured normalization (ST: applied in memory per sample; MSI: the normalized spectra). `none` falls back to the ST technology's `auto` method or the MSI nonzero median. The ST normalization enters the sample fingerprint. |
| Presets ignored by the clustering | Modes: `domains` (ST, MSI tissue), ordered `gradient` levels (MSI `cell_culture`), `foreground` without clustering (MSI `microgrid`). MSI background spectra are `unassigned`. MSI `resolution: sub_cellular` aggregates pixels until the median block detects 200 features. |
| Neighbourhood embedding distorts domains; clusters one unit wide at sharp boundaries (3.3) | The neighbourhood mean is weighted by expression similarity (bilateral), so it does not average across sharp boundaries; Leiden overclusters into about 25 clusters (resolution searched, at least 300 units per cluster) and the merge joins the splits. No boundary cluster in the synthetic calibration. Removing the neighbourhood altogether was tried and rejected: MEL2 Visium HD merged to 2 clusters and the MEL2 MALDI-TOF labels were speckled by detection dropout. |
| Valley-ratio histogram on the min-max range (2.2) | Range from the 0.5 % to 99.5 % quantiles; an outlier no longer hides a valley (test). |
| Merge axis between centroids, docstring says discriminant (3.3) | Fisher discriminant axis with the pooled within-cluster covariance, Ledoit-Wolf shrunk. |
| Union scores with a veto | Groups are scored by the contact-weighted mean of their input pair ratios. Union scores merged the Visium HD sample to one cluster; the veto kept 4 to 22 fragments of overclustered synthetic domains. |

Verified on the MEL and MEL2 Visium HD 16 µm samples (`sequencing`, `bin`: 14 and 8 clusters,
against 7 and 5) and on the MEL and MEL2 MALDI-TOF acquisitions in `test_data` (17 and 21 clusters
against 13 and 15, neighbour coherence 0.91 and 0.89). The suite passes with 255 tests.

Out of scope, still open:

- A small domain with weak markers beside a large one is merged into it (no density valley between
  them).
- A smooth expression field or gradient inside a domain shows as a few coherent bands.
- Alignment keyed on geometry instead of the preprocessing fingerprint (section 4).

[^xu2024]: Xu H. et al. A whole-slide foundation model for digital pathology from real-world data. *Nature* 630, 181-188 (2024). [doi:10.1038/s41586-024-07441-w](https://doi.org/10.1038/s41586-024-07441-w)
[^singhal2024]: Singhal V. et al. BANKSY unifies cell typing and tissue domain segmentation for scalable spatial omics data analysis. *Nature Genetics* 56, 431-441 (2024). [doi:10.1038/s41588-024-01664-3](https://doi.org/10.1038/s41588-024-01664-3)
[^wolf2018]: Wolf F. A., Angerer P., Theis F. J. SCANPY: large-scale single-cell gene expression data analysis. *Genome Biology* 19, 15 (2018). [doi:10.1186/s13059-017-1382-0](https://doi.org/10.1186/s13059-017-1382-0)
[^otsu1979]: Otsu N. A threshold selection method from gray-level histograms. *IEEE Transactions on Systems, Man, and Cybernetics* 9, 62-66 (1979). [doi:10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076)
[^bhuva2024]: Bhuva D. D. et al. Library size confounds biology in spatial transcriptomics data. *Genome Biology* 25, 99 (2024). [doi:10.1186/s13059-024-03241-7](https://doi.org/10.1186/s13059-024-03241-7)
[^lause2021]: Lause J., Berens P., Kobak D. Analytic Pearson residuals for normalization of single-cell RNA-seq UMI data. *Genome Biology* 22, 258 (2021). [doi:10.1186/s13059-021-02451-7](https://doi.org/10.1186/s13059-021-02451-7)
[^hennig2010]: Hennig C. Methods for merging Gaussian mixture components. *Advances in Data Analysis and Classification* 4, 3-34 (2010). [doi:10.1007/s11634-010-0058-3](https://doi.org/10.1007/s11634-010-0058-3)
[^maynard2021]: Maynard K. R. et al. Transcriptome-scale spatial gene expression in the human dorsolateral prefrontal cortex. *Nature Neuroscience* 24, 425-436 (2021). [doi:10.1038/s41593-020-00787-0](https://doi.org/10.1038/s41593-020-00787-0)
[^smith2006]: Smith C. A. et al. XCMS: processing mass spectrometry data for metabolite profiling using nonlinear peak alignment, matching, and identification. *Analytical Chemistry* 78, 779-787 (2006). [doi:10.1021/ac051437y](https://doi.org/10.1021/ac051437y)
[^fonville2012]: Fonville J. M. et al. Robust data processing and normalization strategy for MALDI mass spectrometric imaging. *Analytical Chemistry* 84, 1310-1319 (2012). [doi:10.1021/ac201767g](https://doi.org/10.1021/ac201767g)
[^deininger2011]: Deininger S.-O. et al. Normalization in MALDI-TOF imaging datasets of proteins: practical considerations. *Analytical and Bioanalytical Chemistry* 401, 167-181 (2011). [doi:10.1007/s00216-011-4929-z](https://doi.org/10.1007/s00216-011-4929-z)
[^gibb2012]: Gibb S., Strimmer K. MALDIquant: a versatile R package for the analysis of mass spectrometry data. *Bioinformatics* 28, 2270-2271 (2012). [doi:10.1093/bioinformatics/bts447](https://doi.org/10.1093/bioinformatics/bts447)
[^palmer2017]: Palmer A. et al. FDR-controlled metabolite annotation for high-resolution imaging mass spectrometry. *Nature Methods* 14, 57-60 (2017). [doi:10.1038/nmeth.4072](https://doi.org/10.1038/nmeth.4072)
[^aitchison1982]: Aitchison J. The statistical analysis of compositional data. *Journal of the Royal Statistical Society B* 44, 139-177 (1982). [doi:10.1111/j.2517-6161.1982.tb01195.x](https://doi.org/10.1111/j.2517-6161.1982.tb01195.x)
[^georgiev2024]: Georgiev D. et al. RamanSPy: an open-source Python package for integrative Raman spectroscopy data analysis. *Analytical Chemistry* 96, 8492-8500 (2024). [doi:10.1021/acs.analchem.4c00383](https://doi.org/10.1021/acs.analchem.4c00383)
[^camp2016]: Camp C. H., Lee Y. J., Cicerone M. T. Quantitative, comparable coherent anti-Stokes Raman scattering (CARS) spectroscopy: correcting errors in phase retrieval. *Journal of Raman Spectroscopy* 47, 408-415 (2016). [doi:10.1002/jrs.4824](https://doi.org/10.1002/jrs.4824)
[^lu2021]: Lu M. Y. et al. Data-efficient and weakly supervised computational pathology on whole-slide images. *Nature Biomedical Engineering* 5, 555-570 (2021). [doi:10.1038/s41551-020-00682-w](https://doi.org/10.1038/s41551-020-00682-w)
[^janowczyk2019]: Janowczyk A. et al. HistoQC: an open-source quality control tool for digital pathology slides. *JCO Clinical Cancer Informatics* 3, 1-7 (2019). [doi:10.1200/CCI.18.00157](https://doi.org/10.1200/CCI.18.00157)
