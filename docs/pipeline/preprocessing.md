# Preprocessing Stage

## Overview

The preprocessing stage is the first step in the FOCUS pipeline, where raw data from each modality is cleaned, normalized, and converted to standardized formats for subsequent alignment and registration.

For each modality defined in the configuration, FOCUS discovers all sample subdirectories, creates the output directory structure, and dispatches to the modality-specific processing pipeline. Preprocessing can be skipped for any modality if the output file already exists and `force_recomputing` is not set.

!!! abstract "Scientific background"
    For the algorithms, equations and implementation details behind each modality's pipeline, see
    [MSI](../scientific/msi_methods.md), [Raman](../scientific/raman_methods.md),
    [Microscopy](../scientific/microscopy_methods.md) and
    [Spatial Transcriptomics](../scientific/st_methods.md) preprocessing methods.

## Modality-Specific Preprocessing

Each modality has its own processing pipeline:

---

### 1. Microscopy Image Preprocessing

**Input Formats**: exactly one file per sample: `.ome.tiff`, `.ome.tif`, `.ome.btf`, `.qptiff`, `.tiff`, `.tif`, `.btf`, `.svs`, `.ndpi`, `.scn`, `.bif` (tifffile), `.czi` (pylibCZIrw), `.nd2` (nd2)  
**Output Format**: Lossless tiled OME-TIFF pyramid in the source sample type with every channel (JPEG 2000 reversible for 8-bit brightfield, zstd otherwise)

Two settings describe the acquisition: `image_type` (`auto`, `brightfield`, `fluorescence`) and `z_projection` (`auto`, `max`, `best_focus`, `middle`). They select every calibrated value (tissue detection, Z reduction, background fill, display rendering, codec). Cropping and background removal are opt-in. A sample that raises does not stop the run: the warning `Sample skipped: <error>` is reported and the next sample is processed. A sample folder with no supported image or with several is detected while the sample objects are constructed, before processing begins, and aborts the modality.

**Processing Steps**:

1. **Reading image**
   - Open the single supported file of the sample folder lazily; pixels are read tile by tile when a step needs them
   - Choose the acquisition series or scene (the largest that is not a label, macro, thumbnail, overview, map or preview image) and read the axes from the reader metadata
   - Report the format, size, channels, sample type and the reader's notes

2. **Resolving image type**
   - Pixel size from the file metadata, else the `pixel_size` setting; a difference over 5% between the two is reported
   - `image_type` from the configuration, else the acquisition metadata, else the channel layout, else the image polarity (with a warning that asks for an explicit value); the evidence is recorded
   - Z plane: `T = 0`; Z reduced by `z_projection` (`auto`: maximum projection for fluorescence, best focus for brightfield)

3. **Detecting tissue** (always)
   - Detection canvas of about 8 µm per pixel, built from the stored levels and an area average
   - Brightfield: HSV saturation (or inverted intensity for one channel), median blur, Otsu threshold. Fluorescence: each channel scaled to its 99.9th percentile, channel maximum, Gaussian smoothing, triangle threshold
   - Cleanup in µm: closing, speck removal, hole filling, pieces of at least 0.05 mm² (capped at a tenth of the largest piece); an empty result keeps the whole image as tissue with a warning
   - The mask is stored as an OME ROI and sets the per-channel display windows; it never changes pixels by itself

4. **Cropping to tissue** or **Removing background** (only when `crop_to_tissue` or `remove_background` is enabled)
   - `crop_to_tissue`: bounding box of the mask plus 100 µm (250 px without a pixel size), clamped to the image. The crop origin in the source image is recorded as the crop offset (`0,0` when cropping is off); `pre_aligned` coordinates, which are source-image pixels, are shifted by it during alignment
   - `remove_background`: pixels outside the mask set to the fill of the image type (maximum of the sample type for brightfield, 0 for fluorescence), recorded as `focus_background_fill`

5. **Writing OME-TIFF**
   - Stage level 0 in zarr and build each lower level with a 2 x 2 area average until the image fits in one 512 x 512 tile
   - Write one OME-TIFF series: one page per channel (one interleaved page for RGB), lower levels as SubIFDs, 512 x 512 tiles, lossless compression
   - OME-XML with channel metadata, objective, `PhysicalSizeX/Y` in µm, the tissue ROI and a FOCUS `MapAnnotation` (fingerprint, input signature, pixel digest, crop offset, image type, Z projection, background fill, display windows; see [Data Schemas](../api/data_types.md#1-preprocessing-outputs))
   - Write to a temporary file and rename it into place

The algorithms and their sources are in [Microscopy Preprocessing Methods](../scientific/microscopy_methods.md).

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `image_type` | `"auto"` | `"auto"`, `"brightfield"` or `"fluorescence"` |
| `z_projection` | `"auto"` | `"auto"`, `"max"`, `"best_focus"` or `"middle"` |
| `crop_to_tissue` | `false` | Crop to the tissue bounding box plus 100 µm |
| `remove_background` | `false` | Fill pixels outside the tissue with the fill of the image type |
| `pixel_size` | `null` | µm per pixel, used only when the file metadata has none |
| `force_recomputing` | `false` | Reprocess even if the output matches the current fingerprint |

The settings `color_enhancement`, `gamma`, `contrast_saturation`, `background_color`, `min_object_coverage`, `clip_percentile` and `crop_margin` were removed in `microscopy-3` and fail validation with the reason. The detection constants are calibrated in µm and the number of pyramid levels follows from the image size; neither is a parameter.

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.ome.tiff
```

---

### 2. MSI (Mass Spectrometry Imaging) Preprocessing

**Input Formats**: `.imzML` + `.ibd` pairs inside `pos/` and/or `neg/` subdirectories  
**Output Format**: AnnData (`.h5ad`), one per sample and one merged across all samples

The MSI pipeline operates at the **dataset level**: all samples are processed together to compute a shared reference m/z backbone, ensuring consistent feature alignment across samples.

**Processing Steps**:

1. **Initialization and Metadata Parsing** (per sample)
   - Detect ion modes from the presence of a complete `.imzML` + `.ibd` pair in `pos/`, `neg/`, or both (an ion mode subdirectory holding neither file is treated as not acquired and ignored)
   - Parse imzML XML: extract data types, raster size (µm), pixel coordinates, and physical coordinates
   - Correct rotation error in physical coordinates via linear regression on the densest pixel column
   - If double ion mode: pair each positive spectrum with the negative spectrum at the same stage position (pixel index when the files have no stage positions), remove unpaired spots, compute affine alignment between positive and negative physical coordinates, and average the two coordinate sets
   - Normalize physical coordinates to origin; shift to raster center
   - Compute raster bounding-box coordinates for each spot (in µm)

2. **Recalibration Reference Selection** (method and references: [MSI methods §4.1](../scientific/msi_methods.md#41-recalibration-reference-selection))
   - Per sample: every spectrum of that sample contributes, before any foreground is known, so matrix and background ions present on the whole slide are eligible calibrants. Every peak is counted, whatever its intensity, on a logarithmic m/z grid with bins one tenth of `mass_tolerance` wide; the raw peaks are then released, so memory stays bounded by the mass range and tolerance rather than the spectrum count
   - The sample's m/z features are the maxima of its smoothed peak density that rise above the local background: a running median of the density, or the col toward a larger neighbouring maximum, tested with a Poisson test. An ion that recurs is a feature however weak it is; noise is not, however many centroids a spectrum holds
   - Candidates come from a feature detection with a kernel half as wide as the backbone's, so ions half a tolerance apart are separate candidates
   - The calibrant matching of step 3 is replayed on the sample with each of the 1,000 most frequent candidates as a reference, in the calibrant search window (max(5 × `mass_tolerance`, 50) ppm): a match within the candidate's own feature is a hit, one elsewhere in the window took another ion
   - A candidate is isolated when at most 10% of its matches took another ion; its score is its hits per spectrum and its reference mass the mean m/z of its hits. When a sample has no isolated candidate, the log gives its feature and isolated counts
   - For each ion mode, select at least 10 references: sprayed internal standards from `internal_standard_mz` first, then the best candidate (ties broken by ascending m/z) in each of 10 equal log-m/z segments, then by score. References are more than twice the search window apart
   - When internal standards are observed, the references are moved onto the standards' mass scale. Otherwise one constant ppm shift per sample, from the calibrant candidates samples share, puts all samples on a common mass scale, so one ion stays one feature across samples; the shift is logged with the sample's references
   - Alternatively, a user-supplied `recalibration_reference` can be passed directly, which skips this step entirely

3. **Per-Spectrum m/z Recalibration** (per sample; [MSI methods §4.2](../scientific/msi_methods.md#42-per-spectrum-recalibration))
   - Scan lines follow the imzML scan type (horizontal by default)
   - Noise gate: a calibrant match must reach its spectrum's noise median plus 5 × 1.4826 × MAD, over the intensities of its peaks outside the sample's features (widened by the search window); a spectrum with fewer than 10 such peaks has no gate, so clean spectra keep every calibrant
   - Sample offset: locate the calibrants within ±10 times the search window, where more references show significantly more peaks than their background than chance agreement explains; the search then starts there (at 0 when no offset is supported)
   - Line level: match every reference within the search window, take the median ppm error per line and reference; lines without an estimate are searched again around the drift of their estimated neighbours until no further line is estimated; fit an offset (and a slope in m/z when at least 3 references spanning 25% of their m/z range are matched), smooth over 5 lines and interpolate lines without calibrants
   - Pixel level: match again within the search window centred on the line prediction; a spectrum's residual is the median of its residuals within `mass_tolerance` of their median, used when at least 3 agree; smooth over the 3 x 3 pixel neighbourhood
   - Correct every m/z as m / (1 + e(m) × 1e-6), with e(m) the spectrum's ppm error model; the same model serves foreground detection, the sample features and the intensity matrix
   - Runs with or without a lipid annotation database; the applied correction is stored in `obs["recalibration_ppm_<mode>"]`

4. **Foreground Detection** (per sample, on the recalibrated spectra; [MSI methods §5](../scientific/msi_methods.md#5-foreground-detection)). Always runs; no lipid database is needed
   - Recurrent ions: the sample's m/z features over all its spectra (as in step 2)
   - `sample_type` = `"tissue"`, `"tissue_noisy"`, `"cell_culture"`: score = log(1 + number of recurrent ions a spectrum holds above its median noise + 3 × 1.4826 MAD). Noise centroids never recur, so a noisy background scores low
     - A 1- and a 2-component Gaussian mixture are compared by BIC. A background is separated only when two components win, Ashman's D ≥ 2 and the lower component holds at most half the recurrent ions of the higher one; otherwise every spectrum is foreground
     - Cleanup: `tissue` fills holes and applies a 3 × 3 opening; `tissue_noisy` median-filters the score over 3 × 3 pixels first, applies a 5 × 5 opening and removes components below 10% of the largest; `cell_culture` applies a 3 × 3 majority vote, which keeps gaps between cells
   - `sample_type="microgrid"`: background ions are the recurrent ions present in at least 50% of the spectra, over their whole feature range (matrix, sprayed standards, slide coating)
     - Per-spot features: log(1 + intensity of the peaks outside the background ions) and its fraction of the TIC. TIC is excluded because on a microgrid it tracks matrix ions and ion suppression can lower it on cells
     - Local background correction: rasterise each feature on the pixel grid, subtract a 9 × 9 spot median filter, convert the residual to a robust z-score (median, 1.4826 × MAD)
     - Hysteresis threshold on the mean z-score: seeds at z ≥ max(4, Gaussian quantile for 0.01 expected noise seeds per slide); spots with z ≥ 2 that are 8-connected to a seed join its component. No morphological cleanup; components above 25 spots are logged and kept
   - Dual ion mode: the union of the per-mode foregrounds. When no foreground is found, every spectrum is foreground and a warning is printed
   - Stored as `obs["foreground"]` and `obs["foreground_score"]`; all spots are included in the output

5. **Per-Sample Feature Selection** (foreground spectra; [MSI methods §6](../scientific/msi_methods.md#6-per-sample-feature-selection))
   - Frequency: the m/z features of the recalibrated foreground spectra (as in step 2) present in at least 0.01 times the spectra of the sample's most present feature. No intensity is used
   - Annotation (with `lipid_annotation_db`): features matched within `mass_tolerance` ppm; the log gives the number of annotations expected by chance from decoy masses
   - S/N filter: a feature is kept when its median S/N over the foreground spectra holding it is ≥ 3, the noise being the median and 1.4826 MAD of the non-recurrent peaks of the **background** spectra around its m/z. Annotated features are always kept. Skipped (every feature kept) without at least max(100, 1%) background spectra and 50 noise peaks around a feature
   - Width: the spread of each feature's peaks (interquartile range / 1.349, ppm)

6. **Global Reference m/z Backbone** ([MSI methods §7](../scientific/msi_methods.md#7-backbone-construction))
   - Pool the kept features of all samples per ion mode and split them at their largest gaps until every group spans at most `mass_tolerance` ppm; every feature lands in one group
   - Each group is one backbone feature: presence-weighted mean m/z, and a Gaussian width from its members' widths and offsets, between `mass_tolerance`/10 and a FWHM of `mass_tolerance`
   - This single reference grid is used to align all samples

7. **Lipid Annotation** (optional, requires `lipid_annotation_db`)
   - Match each backbone m/z to the database within `mass_tolerance` ppm
   - Store semicolon-separated matching `db_name` entries in `var["lipid_annotation"]` (or `"Unannotated"`)

8. **Intensity Binning** (per sample, parallel; [MSI methods §8](../scientific/msi_methods.md#8-intensity-binning))
   - Bin all spectra (including background spots) onto the backbone
   - Each peak is split over the features within `mass_tolerance` ppm by their Gaussian weights (the feature's width), normalized to sum to 1: a feature alone in reach takes the whole peak, close features share it smoothly, and intensity is conserved
   - Produces a sparse (N_spots × N_features) float32 CSR matrix directly

9. **Intensity Normalization** (applied independently per sample and per ion mode, at a fixed scale; [MSI methods §9](../scientific/msi_methods.md#9-intensity-normalization))
   - `"tic"`: divide each spectrum by its total ion count over the number of features (each spectrum sums to the number of features)
   - `"median"`: divide each spectrum by the median of its nonzero features
   - `"rms"`: divide each spectrum by its root mean square over the features
   - `"internal_standard"`: divide each spectrum by the summed intensity of the internal standard features (`internal_standard_mz`)
   - `"none"`: keep raw binned intensities

10. **Per-Sample Cluster Labels** (used only to colour spots during alignment)
    - Mode from `sample_type`: tissue domains (`tissue`, `tissue_noisy`), eight ordered gradient levels (`cell_culture`), foreground against background without clustering (`microgrid`)
    - Only foreground spectra take part; background spectra are labelled `unassigned`
    - `resolution="sub_cellular"` sums pixels into k x k blocks of the acquisition raster until the median block detects 200 features; `"standard"` clusters native pixels (blocks only above 250,000 spectra)
    - Representation: the normalized spectra averaged over each unit, then `log1p` (`intensity_normalization="none"`: each summed spectrum divided by the median of its nonzero features)
    - Up to 2,000 most variable features → PCA → each unit's components joined to an edge-preserving neighbourhood mean (1 ring; 2 rings for `sub_cellular`) → neighbour graph → Leiden at the resolution giving about 25 clusters → merging of adjacent clusters that no density valley separates ([Alignment Clustering](../scientific/spatial_clustering.md))
    - Labels stored in `obs["cluster"]`, eligibility in `obs["clustering_eligible"]`, provenance in `uns["focus_clustering"]`; units, PCA and graphs are discarded

11. **Save Per-Sample AnnData and Merge**
    - Each sample is saved separately (gzip compression), written to a temporary file and renamed
    - All samples are concatenated on disk into a single merged h5ad (inner join on features); its per-sample `uns["spot_size"]`, `uns["recalibration_reference"]` and `uns["recalibration_pool"]` dicts are written into the file in place
    - Every file carries `uns["focus_preprocessing_fingerprint"]` (method version, settings, fixed calibration, lipid database, inputs). A changed fingerprint rebuilds the dataset and re-runs alignment and registration of the modality

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mass_tolerance` | `10` | Mass tolerance in ppm (int or float) for feature detection, backbone merge, binning, recalibration and annotation |
| `sample_type` | `"tissue"` | Experiment type, which calibrates foreground detection: `"tissue"`, `"tissue_noisy"`, `"cell_culture"` or `"microgrid"` |
| `intensity_normalization` | `"none"` | Normalization method (per ion mode, fixed scale): `"none"`, `"tic"`, `"median"`, `"rms"` or `"internal_standard"` |
| `recalibration_reference` | `null` | User-supplied reference m/z per ion mode (`{"pos": [...]}` or `"pos: 760.585, 798.541; neg: 885.550"`); selected from the data if null |
| `internal_standard_mz` | `null` | m/z of the sprayed internal standards per ion mode (same forms); always recalibration references, required by `"internal_standard"` normalization |
| `resolution` | `"standard"` | Clustering preset for the alignment colours: `"standard"` (multicellular pixels) or `"sub_cellular"` (weak high-resolution pixels, wider spatial neighbourhood) |
| `lipid_annotation_db` | `null` | Path to lipid annotation database (CSV or JSON with `db_name`, `ionized_mass`, `ion_mode` columns) |
| `force_recomputing` | `false` | Reprocess even if output already exists |

The defaults above are applied both by the pipeline's settings extractor (`_extract_msi_settings`) and by the `MsiDataset.process_dataset()` signature, so config runs and direct Python calls agree. The settings `detect_background`, `frequency_threshold` and `min_intensity_threshold`, and the normalizations `"log"`, `"clr"` and `"tic_mean_scaled"`, were removed in `msi-13` and fail validation with the reason.

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.h5ad

<dataset_path>/merged/preprocessing/
└── <modality_name>_merged_processed.h5ad
```

**AnnData Structure** (per-sample):

| Slot | Description |
|------|-------------|
| `.X` | Normalized interpolated intensities (sparse CSR, spots × m/z features) |
| `.layers["raw"]` | Raw interpolated intensities before normalization (sparse CSR) |
| `.obs["sample_id"]` | Categorical sample identifier |
| `.obs["foreground"]` | Categorical boolean: tissue or cell (True) vs background (False). Always present; all True when no background was separated |
| `.obs["cluster"]` | Categorical per-sample cluster labels (alignment colouring) |
| `.obsm["spatial"]` | Physical spot center coordinates in µm, shape (N, 2), float32 |
| `.obsm["raster_coordinates"]` | Raster bounding boxes in µm, shape (N, 2, 2): [[x1,y1],[x2,y2]] |
| `.var["mz"]` | Consensus reference m/z values (float32) |
| `.var["mz_mode"]` | Ion mode for each m/z: `"pos"` or `"neg"` |
| `.var["lipid_annotation"]` | Lipid annotation string (if DB provided, else `"Unannotated"`) |
| `.uns["spot_size"]` | Raster pixel size [width, height] in µm (per-sample: list; merged: dict keyed by sample_id) |

---

### 3. Raman Spectroscopy Imaging Preprocessing

**Input Formats**: `.lif` (Leica Image File format)  
**Output Format**: Multi-channel OME-TIFF pyramid (hyperspectral, `uint8`, Adobe-Deflate compressed), written by ASHLAR

The pipeline has five labeled steps, reported as `1/5` … `5/5`. All steps are always executed; none can be individually disabled via configuration. Only the per-step parameters are tunable. Steps 2 to 4 cache their result as a `.npy` file, and the three caches are deleted once the sample's final OME-TIFF has been produced.

A sample that raises does not stop the run: the exception is caught, `Error processing sample <sample_id>: <error>` is printed to the console, and the next sample is processed. The failed sample has no output file and is absent from the returned mapping.

**Processing Steps**:

1. **LIF File Loading and Metadata Parsing**
   - Load the first `.lif` file found in the input directory
   - Parse the LIF XML: the two spatial axis sizes and their pixel sizes (µm), the number of spectral steps, the tile count, the tile stage coordinates (µm), the excitation wavelength range, and the laser pump wavelength
   - Only tile scans (tile count ≥ 2) are processed; single-field images and automatically stitched images are skipped
   - Read tile pixel data into a float32 array of shape (T, C, Y, X), where T = tiles and C = spectral channels
   - Compute the wavenumber axis over the excitation wavelength range: `ν̃ = (1/λ − 1/λ_stokes) × 10⁷`, where `λ_stokes` is the laser pump wavelength read from the LIF hardware settings and the factor `10⁷` converts nm⁻¹ to cm⁻¹
   - If several tile-scan elements are present: concatenate them along the channel axis, record each one's channel range as a *spectra slice*, and, when the concatenated wavenumber axis is not monotonic, drop the one overlapping region, from the first monotonicity break back to the closest-matching wavenumber in the preceding block
   - Rescale intensities once from the global maximum: ÷255 when it lies in (1, 255], ÷65535 when it lies in (255, 65535], `ValueError` above that, unchanged at or below 1

2. **BaSiC Illumination Correction**
   - Requires the `FOCUS_BaSiCpy` conda environment; its absence, or a missing `conda` on `PATH`, raises `RuntimeError`
   - Each spectral channel is written to a temporary `.npy`, corrected by `tools/BaSiCpy/main.py` in a subprocess (`JAX_PLATFORM_NAME=cpu`), and read back
   - Channels are dispatched in parallel through a thread pool (`max_workers` threads)
   - The assembled stack is min-max normalized globally to [0, 1], using one minimum and maximum over all tiles and channels
   - Result cached to `basic_corrected_tiles.npy`

3. **Background Removal**
   - Quick-stitch the BaSiC-corrected tiles into a preview mosaic using distance-transform weights
   - Reduce the hyperspectral mosaic to one grayscale image via PCA (1 component) over the non-zero pixels, contrast-stretched between its 2nd and 98th percentiles
   - Apply CLAHE (`clipLimit=2.0`, `tileGridSize=(8, 8)`)
   - Compute the Otsu threshold on a copy clipped at the 95th percentile, multiply it by `otsu_threshold_factor` (default: 0.7; below 1.0 it lowers the threshold and keeps more pixels), and binarize the unclipped mosaic with it
   - Remove connected components of `min_object_size` pixels or fewer (default: 500)
   - Fill holes in the binary mask
   - Filter by contour area: keep external contours covering ≥ `bg_min_area_fraction` of the mosaic area (default: 5%) and fill them
   - Back-project the mosaic mask onto individual tiles and multiply it into the BaSiC-corrected tiles, so background pixels become 0 in every channel
   - Result cached to `segmented_tiles.npy`; step 4 operates on these masked tiles

4. **Spectral Cleaning** (one work unit per tile and spectra slice, parallel)
   - Drop spectra whose consecutive-difference series has a MAD of exactly 0. These are flat spectra, including the background pixels zeroed in step 3, and they would divide by zero in the despiking z-score. Their pixels are written back as zeros
   - Run the remaining spectra through the fixed RamanSPy pipeline:
     1. **Despiking**: Whitaker-Hayes cosmic ray removal
     2. **Denoising**: Savitzky-Golay filter (default: window=7, polyorder=3)
     3. **Baseline correction**: IASLS algorithm
     4. **Normalization**: MinMax, per spectrum, to [0, 1]
   - Work units are dispatched with `joblib` (`max_workers` workers)
   - Results cached to disk (`raman_corrected_tiles.npy`)

5. **ASHLAR Stitching**
   - Requires the `FOCUS_ASHLAR` conda environment
   - Mirror the tile y coordinates within their own range (Leica → OME-TIFF convention), then scale the tiles by 255 and cast to `uint8`
   - Select the alignment channel from the first spectra slice: the channel whose largest per-tile mean intensity is highest
   - Write one OME-TIFF input file per spectra slice (one ASHLAR cycle), with per-tile positions and pixel size in µm
   - Run `tools/ASHLAR/main.py` in a subprocess: `EdgeAligner` on the first cycle, a `LayerAligner` per further cycle, then a pyramid write with enough 2× levels for the smallest one to hold at most 9,000,000 pixels
   - Rename ASHLAR's output to `<modality_name>_<sample_id>_processed.ome.tiff`

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | `8` | Threads for BaSiC channels (step 2) and `joblib` workers for spectral cleaning (step 4) |
| `savgol_window` | `7` | Savitzky-Golay filter window length for denoising |
| `savgol_polyorder` | `3` | Savitzky-Golay filter polynomial order; must be smaller than `savgol_window` |
| `bg_min_area_fraction` | `0.05` | Minimum contour area as fraction of mosaic area for background removal |
| `otsu_threshold_factor` | `0.7` | Multiplicative factor applied to the Otsu threshold before background segmentation |
| `min_object_size` | `500` | Connected components of this many pixels or fewer are removed from the tissue mask |
| `force_recomputing` | `false` | Reprocess even if the output or an intermediate cache already exists |

The defaults are the `RamanImage` class constants, used both by the config settings extractor (`_extract_raman_settings`) and by the `RamanDataset.process_dataset()` signature.

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.ome.tiff
```

The OME-XML that ASHLAR writes carries the physical pixel size in µm but no channel names and no wavenumber values, so the spectral axis is not recoverable from the output file. Downstream stages address the mosaic in pixel coordinates.

---

### 4. Spatial Transcriptomics Preprocessing

**Input Format**: one AnnData (`.h5ad`) per sample directory (several raise `ValueError`)  
**Output Format**: AnnData (`.h5ad`), one per sample and one merged

Supports any spatial transcriptomics technology whose AnnData holds raw counts (in `.X`, `.layers['counts']`, `.layers['raw']` or `.raw`), `.obsm["spatial"]` and `.uns["spot_size"]` in µm. `capture` and `unit` describe the technology and calibrate quality control, the gene rule, the default normalization and the clustering preset. See [Spatial Transcriptomics](../modalities/transcriptomics.md) and [ST methods](../scientific/st_methods.md).

**Per-Sample Processing Steps** (raw counts throughout):

1. **Loading and Validation**: finite `.obsm["spatial"]` (float32), required `.uns["spot_size"]` (float32 `[x, y]`), raw counts resolved into a CSR `.X` with every other input matrix dropped, unique gene names
2. **Control Features**: probes, codewords and non-`Gene Expression` features counted into `obs["pct_counts_control"]`, then removed
3. **Mitochondrial Flag**: `.var["mt"]` by a case-insensitive `MT-`/`MT.` name prefix
4. **Quality Control**: out-of-tissue (`in_tissue == 0`) and zero-count spots always removed; with `quality_control=true`, local outliers for `unit="spot"` (36 nearest spots, robust z beyond 3) and global 5-MAD low-tail outliers for `unit="cell"`
5. **QC Metrics**: `scanpy.pp.calculate_qc_metrics(qc_vars=["mt"], percent_top=None)` with mitochondrial genes present
6. **Area**: `obs["focus_area"]` from `spot_size` (spots, bins) or the cell area column (cells)
7. **Mitochondrial Gene Removal** (optional, `remove_mitochondrial_genes`)
8. **Observation Names**: prefixed with `<sample_id>_`
9. **Per-Sample Cluster Labels**: alignment-only labels; `unit="spot"` uses the `standard` preset, `"bin"` and `"cell"` the `sub_cellular` preset. On an analytical copy: normalize to 10,000 counts → log1p → HVG selection (target 2,000) → sparse PCA (up to 50 PCs) → neighbourhood embedding → neighbors (up to 15) → Leiden (resolution 0.5) → merging of adjacent clusters without a density valley. Original expression and coordinates remain intact
10. **Save**: raw counts, sparse CSR, gzip, atomic write. The input SHA-256 is stored and reused while the input file is unchanged

**Dataset-Level (Merged) Processing**:

The merged file is reused when `force_recomputing` is false, no sample was rebuilt in this run, and its fingerprint (sample fingerprints, normalization, gene rule) matches.

1. Read each per-sample `spot_size` and `var["n_cells_by_counts"]` with a backed read
2. Concatenate **on disk** (`anndata.concat_on_disk`) with an **outer join** (union panel; absent genes are 0)
3. **Gene rule**: keep a gene detected in at least 20 spots (`capture="sequencing"`) or 1 spot (`capture="imaging"`) of at least one sample. There is no highly-variable-gene selection
4. Recompute `total_counts`, `n_genes_by_counts` and gene QC on the final panel; `pct_counts_mt` and the control metrics keep the per-sample values
5. **Normalization**, once, with one global scale: `auto` (technology default), `none`, `log_normalize`, `cp10k_log`, `size_factors` or `area_log_normalize`; the counts move to `.layers["raw"]` and `.X` is float32
6. Per-sample `.obs["cluster"]` labels are kept; `.uns["spot_size"]` is written as `{sample_id: [x, y]}`; `.uns["focus_normalization"]` records the method and its scale

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `capture` | `"sequencing"` | `"sequencing"` or `"imaging"` |
| `unit` | `"spot"` | `"spot"`, `"bin"` or `"cell"` |
| `normalization` | `"auto"` | Method applied to the merged dataset |
| `quality_control` | `true` | Remove the outlier spots of the unit's rule |
| `remove_mitochondrial_genes` | `false` | Drop mitochondrial genes (`MT-`/`MT.` prefix), per sample |
| `force_recomputing` | `false` | Reprocess even if output already exists |

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.h5ad

<dataset_path>/merged/preprocessing/
└── <modality_name>_merged_processed.h5ad
```

**AnnData Structure**:

| Slot | Description |
|------|-------------|
| `.X` | Per sample: raw counts (sparse CSR). Merged: normalized float32 (raw counts with `normalization="none"`) |
| `.layers["raw"]` | Merged only, raw counts; absent with `normalization="none"` |
| `.obs_names` | Spot names, prefixed `<sample_id>_` |
| `.obs["sample_id"]` | Categorical sample identifier |
| `.obs["cluster"]` | Categorical per-sample cluster labels |
| `.obs` (QC columns) | `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt`, `total_counts_control`, `pct_counts_control` and `log1p_` variants; `focus_area` |
| `.var["mt"]` | Boolean mitochondrial flag |
| `.var` (QC columns) | `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants |
| `.obsm["spatial"]` | float32 spatial coordinates, shape (N, 2) |
| `.uns["spot_size"]` | float32 array [width, height] in µm; dict keyed by sample_id in the merged file |
| `.uns["focus_qc"]` | Per sample: outlier rule and spots removed per reason |
| `.uns["focus_normalization"]` | Merged: method and scale |

---

## File Naming Convention

FOCUS uses a consistent naming pattern for all preprocessed outputs.

**Per-Sample Files**:
```
<modality_name>_<sample_id>_processed.<ext>
```

**Merged Files** (omics modalities only; microscopy and Raman produce no merged output):
```
<modality_name>_merged_processed.<ext>
```

**Extension Mapping**:
- Microscopy: `.ome.tiff` (per-sample only)
- Raman: `.ome.tiff` (per-sample only)
- MSI: `.h5ad` (per-sample and merged)
- ST: `.h5ad` (per-sample and merged)

---

## Caching

Each modality checks whether its output file already exists at the start of processing. If the file is found and `force_recomputing` is not set, the step is skipped and the cached path is returned.

Spatial transcriptomics validates its cache by content. Per-sample and merged ST files store `.uns['focus_preprocessing_fingerprint']`, a SHA-256 over the input file content, the effective preprocessing and clustering settings, the method version and the numerical software versions (merged files: the per-sample fingerprints and the merged settings). A file whose fingerprint differs from the current one is rebuilt, and files written before fingerprints existed are rebuilt once. When a rebuild changes the merged ST fingerprint, every alignment pair involving that modality is re-aligned (the alignment GUI opens again) and its registration is recomputed.

MSI files also carry `.uns['focus_preprocessing_fingerprint']` (see [MSI](../modalities/msi.md)).

Microscopy images validate their cache by content as well. The preprocessed OME-TIFF stores, in a FOCUS annotation of its OME-XML, a SHA-256 fingerprint of the method version, the SHA-256 of the whole source file, the sample and modality, every processing setting, the calibration constants and the software versions. The source SHA-256 is stored in the output and reused while the source path, size and modification time are unchanged, so an unchanged source is not re-hashed. An output whose fingerprint differs, or that has none, is regenerated. A regenerated image whose level-0 pixels are identical to the replaced file keeps the alignments made on it; an image whose pixels changed is aligned again (see [Alignment](alignment.md#caching-and-re-running)). A microscopy rebuild does not enter the orchestrator's preprocessing cascade; registration outputs depend on the image's pixel digest through the [registration fingerprint](registration.md#caching).

!!! warning "Raman: changing a processing setting does not invalidate the cache"
    For Raman the cache key is the existence of the output file, **not** the processing settings. Editing a setting and re-running therefore returns the previously computed output unchanged, with no warning. After changing any Raman processing setting, set `force_recomputing: true` for that modality (or delete its outputs) so the new value takes effect.

The Raman pipeline has intermediate caches (`basic_corrected_tiles.npy`, `segmented_tiles.npy`, `raman_corrected_tiles.npy`) that are checked independently at each step, allowing partial resume within a single sample. LIF loading is not cached and runs again on every attempt, since the tile coordinates, pixel size and spectra-slice boundaries feed the later steps. The three files are deleted once the sample's final OME-TIFF exists, so they are only ever found after an interrupted or failed run, which is when they are used. Like the output-level cache, they record no parameter values.

---

## Parallelism and Performance

### Execution model

Modalities are processed one at a time, and within each modality samples are processed sequentially. Peak RAM usage scales with a single sample: typical tissue sections require 40 to 50 GB, and large samples can require up to 100 GB. Loading multiple samples concurrently would multiply this requirement and cause out-of-memory failures.

Parallelism is exploited **within** a single sample's processing steps, where the data is already loaded into memory:

| Modality | Intra-sample parallelism |
|----------|--------------------------|
| Microscopy | The Z projection, the detection canvas and the pyramid staging run chunk by chunk on lazy dask arrays (dask's default threaded scheduler) |
| MSI | Peak counting and calibrant matching are numba-compiled; intensity binning uses `joblib` parallel workers (one chunk per core) |
| Raman | BaSiC correction parallelizes spectral channels via a `ThreadPoolExecutor` (one subprocess per channel); spectral cleaning parallelizes tile × spectra-slice work units via `joblib` |
| ST | Single-threaded (scanpy and scipy operations are internally vectorized) |

### Memory management

The entire stack is built on natively compiled, memory-efficient libraries (NumPy, SciPy, pandas, numba/Numba JIT) that operate on contiguous arrays and avoid Python-level loops wherever possible. Key design decisions:

- **Microscopy**: the source is read lazily, one stored tile or chunk at a time, and never held whole in memory. Tissue detection works on a canvas of about 8 µm per pixel (at most 9 MP), and the pyramid is staged in on-disk zarr chunks and compressed in tile batches of 64 MiB, so peak memory is bounded by tiles and the detection canvas, not by the slide.
- **MSI**: spectra are read directly from the binary IBD file with `np.fromfile` (zero-copy memory mapping). Intensity matrices are kept as sparse CSR throughout. After each processing step the intermediate dense arrays are explicitly deleted and `gc.collect()` is called. The merge step uses an on-disk concatenation routine to avoid loading all samples simultaneously.
- **Raman**: tiles are stored as a single contiguous `(T, C, Y, X)` float32 array, and each stage writes its result to a `.npy` cache so an interrupted run can resume from it. The raw, illumination-corrected/masked, and spectrally cleaned stacks are all held on the sample object for the duration of that sample; within background removal, the intermediate mask images are explicitly released as soon as they are consumed.
- **MSI m/z clustering**: memory per chunk is estimated from available RAM at runtime (`psutil.virtual_memory().available`) before the job is dispatched, so the chunk count scales to the machine.
- **ST**: sparse CSR matrices are used end-to-end; concatenation uses an outer join to avoid densifying the gene matrix.

### GPU

FOCUS does not use GPU acceleration in the preprocessing stage. The GPU is used only by `feature_extraction` registration (Prov-GigaPath inference with PyTorch/CUDA). All preprocessing computations run on CPU.

---

## Quality Control

FOCUS does not perform automatic quality control during preprocessing. The outputs should be manually inspected before proceeding to alignment.

### Spatial Transcriptomics

ST is the only modality where QC metrics are computed automatically: `scanpy.pp.calculate_qc_metrics` populates `obs["n_genes_by_counts"]`, `obs["total_counts"]`, and `obs["pct_counts_mt"]` for every spot. Review these before choosing spot-filtering thresholds.

Suggested checks:

```python
import scanpy as sc
adata = sc.read_h5ad("path/to/<modality>_<sample>_processed.h5ad")

# Violin plots of QC metrics
sc.pl.violin(adata, ["n_genes_by_counts", "total_counts", "pct_counts_mt"], jitter=0.4)

# Spatial plot of total counts
sc.pl.spatial(adata, color="total_counts", spot_size=adata.uns["spot_size"][0])

# UMAP coloured by the preprocessing cluster labels
sc.pp.pca(adata, n_comps=min(50, adata.n_obs - 1, adata.n_vars - 1))
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.pl.umap(adata, color="cluster")
```

### MSI

After preprocessing, inspect the foreground classification and the quality of the m/z alignment:

```python
import anndata as ad
import numpy as np
import scanpy as sc
import matplotlib.pyplot as plt

adata = ad.read_h5ad("path/to/<modality>_<sample>_processed.h5ad")

# Spatial map of foreground mask
coords = adata.obsm["spatial"]
fg = adata.obs["foreground"].astype(bool).to_numpy()
plt.scatter(coords[~fg, 0], coords[~fg, 1], c="lightgray", s=1, label="background")
plt.scatter(coords[fg, 0], coords[fg, 1], c="steelblue", s=1, label="foreground")
plt.legend(); plt.axis("equal"); plt.show()

# Spatial map of a known lipid (if annotated)
hits = np.where(adata.var["lipid_annotation"].to_numpy() != "Unannotated")[0]
if hits.size:
    intensity = adata.X[:, hits[0]].toarray().ravel()
    plt.scatter(coords[:, 0], coords[:, 1], c=intensity, s=1, cmap="viridis")
    plt.colorbar(label=str(adata.var["lipid_annotation"].iloc[hits[0]]))
    plt.axis("equal"); plt.show()

# Preprocessing clusters in embedding space (obs["cluster"], used for alignment colouring)
sc.pp.pca(adata, n_comps=min(50, adata.n_obs - 1, adata.n_vars - 1))
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.pl.umap(adata, color=["cluster", "foreground"])
```

### Raman

The output OME-TIFF holds one channel per spectral channel, ordered by scan. Useful checks:

- Open in QuPath, Napari, or FIJI and inspect individual spectral channels for stitching artifacts or residual background.
- Sum-project all channels to produce a pseudo-brightfield image and verify tissue coverage and tile alignment.
- Inspect channels at known Raman bands (e.g., ~1004 cm⁻¹ phenylalanine, ~2850 cm⁻¹ lipids). The output file does not store the wavenumber axis, so the channel index for a band has to be looked up in the axis computed from the LIF metadata (`RamanImage.wavenumbers` after `load_source()`).

```python
import tifffile, numpy as np, matplotlib.pyplot as plt

img = tifffile.imread("path/to/<modality>_<sample>_processed.ome.tiff")
# Full-resolution pyramid level, shape (C, H, W); lower levels are in
# tifffile.TiffFile(...).series[0].levels

# Sum projection
plt.imshow(img.sum(axis=0), cmap="gray")
plt.title("Sum projection"); plt.colorbar(); plt.show()
```

### Microscopy

Visually inspect the output OME-TIFF:

- Open in QuPath, Napari, or FIJI. The stored pixels are the source pixels; the per-channel display windows are in the `focus_display` annotation, and the tissue outline is the OME ROI `tissue`.
- Check that the tissue outline covers every tissue piece, in particular when `crop_to_tissue` or `remove_background` is enabled, since both use it.
- Check the recorded `focus_image_type` and its evidence; when it was inferred from the image polarity, set `image_type` explicitly.

---

## Next Steps

After inspecting preprocessed outputs:

1. **Adjust parameters** if any modality shows poor output quality, then rerun with `force_recomputing: true` for that modality.
2. **Proceed to Alignment**: once satisfied, continue to the [Alignment Stage](alignment.md).

## Additional Resources

- [Alignment Documentation](alignment.md) - Next pipeline stage
- [Configuration Reference](../configuration/config_fields.md) - Preprocessing parameters
- [API Reference: Preprocessing](../api/preprocessing.md) - Programmatic access
