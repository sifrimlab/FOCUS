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

**Input Formats**: `.ome.tiff`, `.ome.tif`, `.qptiff`, `.tiff`, `.tif`, `.czi` (searched in this priority order)  
**Output Format**: Multi-resolution OME-TIFF, stored in the source file's dtype (`uint8`/`uint16` pass through; float sources stay `float32`; other integer depths become `uint16`), zlib-compressed with a predictor matched to the storage dtype

The pipeline has five labeled steps, reported as `1/5` … `5/5`. Steps 2 to 4 are switchable from the configuration; when one is off, its step line reports `not required`. A sample that raises does not stop the run: the exception is caught, `Error processing sample <sample_id>: <error>` is printed to the console, and the next sample is processed. A **missing input file** is the exception: it is raised while the sample objects are constructed, before processing begins, and aborts the modality.

**Processing Steps**:

1. **File Loading and Normalization**
   - Load the first file matching the highest-priority extension present in the sample's modality directory
   - For TIFF/OME-TIFF: read the first series at its base level; a pyramid already in the file is not reused
   - For qpTIFF: compare all series/pyramid levels by pixel count and load only the highest-resolution one
   - For CZI: reduce leading axes by taking index 0 until three remain; a message is printed when the outermost axis holds more than one entry
   - Move channel axis to last position using a shape heuristic
   - Convert to float32 in [0, 1]: a `float32` array already at or below 1 is left as is, an integer array is divided by its dtype maximum, any other float array by its own maximum
   - Clip to at most 3 channels

2. **Color Enhancement** (optional, `color_enhancement=True`)
   - Gamma correction: `I = I^gamma` (default γ=0.45, which brightens the image)
   - Contrast stretching: one pair of percentiles, `contrast_saturation` and `100 − contrast_saturation`, so 0.35% saturated at each end by default. The pair is computed over the non-zero pixels of the whole image with all channels pooled, then applied as a clip-and-rescale to every channel. Above 9 megapixels of non-zero pixels the percentiles come from a strided subsample; the rescale still touches every pixel

3. **Background Removal** (optional, `remove_background=True`). The tissue mask is detected once on a downsampled proxy capped at 9 megapixels and shared with step 4, so it is computed whenever background removal **or** cropping is enabled; the mask is upsampled back to full resolution before being applied
   - Promote the proxy to 3 channels when it has fewer (a single channel is replicated, two channels gain a zero third channel), so the grayscale conversion accepts it
   - Reduce to a grayscale whose bright class is the tissue. For a bright background: replace pixels that are 0 in every channel with white, convert to grayscale and invert. For a dark background: convert to grayscale and stop there. The polarity is probed only for promoted 1- and 2-channel images, by comparing the median of a border frame against the median of the whole grayscale; 3-channel images always take the bright-background path
   - Clip at `clip_percentile` (default: 99th percentile) then apply Gaussian blur (kernel fixed at 25 px, in proxy pixels)
   - Compute Otsu threshold on the blurred image, apply to the unblurred, unclipped grayscale. The clip and blur shape only the histogram the threshold value comes from, never the mask
   - Remove connected components of 50 pixels or fewer (fixed, in proxy pixels)
   - Fill holes in the binary tissue mask
   - Refine by contour area: keep only contours covering ≥ `min_object_coverage` fraction of the proxy area (default: 1%)
   - Apply mask: tissue pixels are kept, background is filled with the background color

4. **Tissue Cropping** (optional, `crop_to_tissue=True`)
   - Compute the bounding box of the step-3 tissue mask, in proxy coordinates, and scale it back to full resolution
   - Add `crop_margin` pixels on all sides (default: 250 px), clamped to image boundaries
   - An entirely background mask raises `ValueError`

5. **Pyramid Construction and Saving**
   - Build resolution levels by successive 2× downsampling (area interpolation) from the final image size. The number of levels is **computed automatically** so the smallest level fits within a 3,000 × 3,000 pixel cap (for GUI rendering); it is not configurable.
   - Quantize each level from float32 to the storage dtype (`uint8`/`uint16` pass through; float sources stay `float32`; other integer depths become `uint16`)
   - Write as multi-image BigTIFF OME-TIFF with zlib compression and a predictor matched to the storage dtype (2 for integer, 3 for float). Each level is one top-level IFD group, and the OME-XML for all levels sits in the first IFD's description
   - RGB images: interleaved `YXC` layout; single/multi-channel: separate `minisblack` planes per channel

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `color_enhancement` | `true` | Apply gamma correction and contrast stretching |
| `gamma` | `0.45` | Gamma exponent (< 1 brightens, > 1 darkens) |
| `contrast_saturation` | `0.35` | Percentage of non-zero pixels saturated at each end of the histogram |
| `remove_background` | `true` | Remove background using Otsu thresholding |
| `background_color` | `"white"` | Color to fill removed background (`"white"` or `"black"`; any other value raises `ValueError`) |
| `clip_percentile` | `99` | Intensity percentile for clipping before blur |
| `min_object_coverage` | `0.01` | Minimum tissue contour area, as a fraction of the detection-proxy area |
| `crop_to_tissue` | `true` | Crop image to tissue bounding box |
| `crop_margin` | `250` | Pixel margin added around the tissue bounding box |
| `force_recomputing` | `false` | Reprocess even if output already exists |

The number of pyramid resolution levels is not a parameter: it is computed automatically from the image size. The Gaussian blur kernel size and the speck-removal size used during tissue detection are also not parameters. Detection always runs on a downsampled proxy capped at 9 megapixels, so these are fixed internal constants expressed in proxy pixels rather than in the source image's native resolution.

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
   - If double ion mode: filter unpaired spots (experimental artifacts), compute affine alignment between positive and negative physical coordinates, and average the two coordinate sets
   - Normalize physical coordinates to origin; shift to raster center
   - Compute raster bounding-box coordinates for each spot (in µm)

2. **Background Detection** (optional, `detect_background=True` **and** `lipid_annotation_db` set). Without a database the step is silently skipped and every spot is marked foreground
   - For each spot, compute three spectral complexity features: Shannon entropy of the normalized intensity distribution, number of detected peaks, and log(1 + TIC)
   - Add a 4th feature from the database (fraction of peaks matching the DB at the configured mass tolerance)
   - Min-max normalize each feature and average into a composite score
   - **Tissue sections** (`sample_type="tissue"`): fit a 1-component and a 2-component Gaussian Mixture Model; use BIC to select between them. If the 2-component model wins, classify spots with posterior ≥ 0.5 on the higher-mean component as tissue. Apply morphological cleanup (hole filling + binary opening) on the pixel grid.
   - **Microgrid samples** (`sample_type="microgrid"`): use Otsu thresholding with a 25th-percentile floor to protect weak single-cell signals; no spatial cleanup.
   - The foreground classification is stored as `obs["foreground"]`; all spots (including background) are included in the output and can be filtered downstream.

3. **Recalibration Reference Selection**
   - Every spectrum of every sample contributes. Each sample is reduced on arrival to a compact m/z occurrence histogram on a logarithmic grid and its raw peaks are released, so memory stays bounded by the mass range and tolerance rather than the spectrum count
   - Candidates are the annotation-matched m/z when a `lipid_annotation_db` is configured, otherwise all m/z
   - Histogram bins are grouped within `mass_tolerance` ppm (same weighted sliding window used for the m/z backbone), collapsing the many slightly different measurements of one calibrant into a single candidate
   - For each ion mode, greedily select at least 5 candidates by descending score (occurrences × fraction of samples containing the candidate, ties broken by ascending m/z), adding more if needed until every sample is covered
   - Alternatively, a user-supplied `recalibration_reference` dictionary can be passed directly, which skips this step entirely

4. **Per-Row m/z Recalibration** (per sample)
   - For each reference m/z peak, find the highest-intensity peak within `mass_tolerance` in each spectrum
   - Compute per-column (spatial row) mean offset between observed and reference m/z values
   - Apply the row-wise offset to all m/z values in that row

5. **Per-Sample m/z Backbone Computation**
   - Pool all (recalibrated) m/z values of a sample, restricted to foreground spots only when background detection ran (see step 2), otherwise all spots
   - Cluster nearby values using adaptive sliding-window clustering with weighted centroids (parallel, chunked)
   - Filter clusters by frequency: keep only those appearing in ≥ `frequency_threshold` fraction of the maximum cluster weight

6. **Global Reference m/z Backbone**
   - Concatenate all per-sample backbone m/z vectors and apply a final clustering pass (no frequency filter)
   - This single reference grid is used to align all samples

7. **Lipid Annotation** (optional, requires `lipid_annotation_db`)
   - Match each reference m/z to the database within `mass_tolerance` ppm
   - Store semicolon-separated matching `db_name` entries in `var["lipid_annotation"]` (or `"Unannotated"`)

8. **Intensity Interpolation** (per sample, parallel)
   - Rebin all spectra (including background spots) onto the global reference m/z grid
   - Each original peak distributes its intensity to reference bins within `mass_tolerance` ppm using inverse-distance weighting
   - Produces a dense (N_spots × N_features) float32 matrix

9. **Intensity Normalization** (applied independently per sample and per ion mode)
   - `"tic"`: divide each spectrum by its total ion count (each spectrum sums to 1)
   - `"log"`: apply log(1 + x) transform
   - `"clr"`: sparsity-preserving centered log-ratio. Log-centers each spectrum over its nonzero entries only, leaving structural zeros at 0
   - `"tic_mean_scaled"`: rescale each spectrum to the mean total ion count over that sample's spots for that ion mode (each spectrum's total becomes the mean TIC). This is similar to `"tic"`, but it preserves an interpretable absolute intensity scale instead of forcing a sum of 1. The mean is per sample, so values are not comparable across samples
   - `"none"`: keep raw interpolated intensities

10. **Per-Sample Cluster Labels** (used only to colour spots during alignment)
    - Samples above 100,000 spots are coarsened first: a uniform spatial grid of at most 100,000 bins is laid over the spots, all spots in a bin are **summed** into one pseudo-spot, and the pseudo-spots are re-normalized so bin occupancy washes out. Smaller samples skip binning
    - PCA (up to 50 components) → neighbor graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`) on that matrix; each bin's label then propagates back to every spot it contains
    - Labels stored in `obs["cluster"]`. The binned matrix, PCA embedding and neighbor graph are all discarded (only `obs["cluster"]` is kept) to minimize file size

11. **Save Per-Sample AnnData and Merge**
    - Each sample is saved separately (gzip compression)
    - All samples are concatenated on disk into a single merged h5ad (inner join on features, gzip compression)
    - Merged file's `uns["spot_size"]` updated to a per-sample dict

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mass_tolerance` | `10` | Mass tolerance in ppm for m/z clustering, recalibration, and annotation |
| `frequency_threshold` | `0.01` | Minimum fraction of max cluster weight for backbone m/z inclusion |
| `intensity_normalization` | `"none"` | Normalization method (per ion mode): `"tic"`, `"log"`, `"clr"`, `"tic_mean_scaled"`, or `"none"` |
| `recalibration_reference` | `null` | User-supplied reference m/z dict per ion mode; auto-computed if null |
| `min_intensity_threshold` | `10000.0` | Minimum intensity for a peak to be used in recalibration offset estimation |
| `detect_background` | `false` | Run background detection to classify tissue vs background spots. Requires `lipid_annotation_db`; without it the step is skipped |
| `sample_type` | `"tissue"` | Sample type for background detection: `"tissue"` or `"microgrid"` |
| `lipid_annotation_db` | `null` | Path to lipid annotation database (CSV or JSON with `db_name`, `ionized_mass`, `ion_mode` columns) |
| `force_recomputing` | `false` | Reprocess even if output already exists |

The defaults above are applied both by the pipeline's settings extractor (`_extract_msi_settings`) and by the `MsiDataset.process_dataset()` signature, so config runs and direct Python calls agree. `mass_tolerance` must be an `int`; a float raises `ValueError`.

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
| `.obs["foreground"]` | Categorical boolean: tissue (True) vs background (False). Always present; all True when background detection did not run |
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

**Input Formats**: AnnData (`.h5ad`), the first `.h5ad` file found in the sample directory  
**Output Format**: AnnData (`.h5ad`), one per sample and one merged

Supports any spot-based or cell-based spatial transcriptomics technology (Visium, Xenium, MERFISH, etc.) as long as the input AnnData has raw gene counts in `.X` and spatial coordinates in `.obsm["spatial"]`.

**Per-Sample Processing Steps**:

1. **Loading and Validation**
   - Load the first `.h5ad` file found in the sample directory
   - Validate that `.obsm["spatial"]` exists (spatial coordinates are required)
   - Convert spatial coordinates to float32
   - Normalize `uns["spot_size"]` to a float32 array of shape (2,): `[width, height]`; defaults to `[1.0, 1.0]` if not present
   - Convert `.X` to sparse CSR format

2. **Mitochondrial Flag**
   - Flag mitochondrial genes in `.var["mt"]` by a case-insensitive `MT-`/`MT.` name prefix (name-based; Ensembl-ID inputs are not flagged)

3. **Spot Filtering** (all filters optional)
   - `min_count_per_spot` / `max_count_per_spot`: filter by total UMI count
   - `min_genes_per_spot` / `max_genes_per_spot`: filter by number of detected genes
   - All thresholds default to `null` (no filtering)

4. **QC Metrics Computation**
   - `scanpy.pp.calculate_qc_metrics(qc_vars=["mt"], percent_top=None)` on the retained spots, with mitochondrial genes still present
   - `.obs` gains `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt` and their `log1p_` variants; `.var` gains `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants

5. **Mitochondrial Gene Removal** (optional)
   - When `remove_mitochondrial_genes=true`, drop the `.var["mt"]`-flagged genes from the feature set
   - Runs after step 4, so the QC metrics already in `.obs` (including `pct_counts_mt`) describe the matrix before removal

6. **Observation Names**
   - Prefixed with `<sample_id>_`, unless every name already carries that prefix

7. **Per-Sample Cluster Labels**
   - Stored in `.obs["cluster"]`; used to colour spots in the alignment GUI
   - Computed on `.X` at this point: post-filter, post-removal, still raw counts
   - Samples above 100,000 spots are first coarsened onto a uniform spatial grid of at most 100,000 cells, summing the spots in each cell into one pseudo-spot
   - The run matrix is normalized to 10,000 counts and log1p-transformed on a throwaway copy, then PCA (`min(50, n_rows-1, n_genes-1)` components) → neighbor graph (`min(15, n_rows-1)` neighbours) → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`); each cell's label propagates back to its spots
   - Only `.obs["cluster"]` is persisted. Fewer than 2 run rows, fewer than 2 usable PCs, or a single resulting cluster all give the label `'0'`. `random_state=0`

8. **Raw Count Layer**
   - `.layers["raw"]` receives a copy of `.X` **only when** `total_counts_normalize` or `log1p_transform` is enabled. With both off, `.X` already holds raw counts and no layer is written

9. **Normalization** (both optional, default off)
   - Total count normalization: scale each spot to `target_sum = 10,000`
   - log(1 + x) transformation, applied after normalization
   - With both off (the default), `.X` stays raw; downstream stages consume it as-is

**Dataset-Level (Merged) Processing**:

After per-sample preprocessing, all samples are merged. The merged file is reused as-is when it exists, `force_recomputing` is false, and its `.obs["sample_id"]` set equals the active sample set.

1. Read each per-sample `.uns["spot_size"]` with a backed read, without materializing `.X`
2. Concatenate **on disk** (`anndata.concat_on_disk`) with **outer join**: genes absent from a sample are filled with 0 counts. `.uns` is dropped by the concat
3. Restore raw counts on the merged object: `.X = .layers.pop("raw", .X)`. The layer exists only if the per-sample files were normalized, otherwise `.X` is already raw
4. **Cross-sample gene filtering** (both optional; skipped entirely when both are `null`):
   - `min_spots_per_gene`: a sample passes for a gene when the gene is expressed in ≥ this fraction of that sample's spots
   - `min_count_spots_ratio_per_gene`: a sample passes when the gene's (total counts / expressed spots) ≥ this value; samples where the gene is unexpressed count as neither pass nor fail
   - A gene is retained when it passes in **at least one** sample; the number of samples it is detected in does not matter. With both thresholds set, a gene must satisfy each in at least one sample, not necessarily the same one
   - There is no highly-variable-gene selection
5. Re-derive `.var["mt"]` and recompute QC metrics on the merged raw matrix
6. `.layers["raw"]` under the same condition as step 8 above, then normalize merged `.X` with the same `total_counts_normalize` / `log1p_transform` flags
7. Per-sample `.obs["cluster"]` labels carried through the concat are kept unchanged; no clustering runs on the merged matrix
8. `.uns["spot_size"]` is written as `{sample_id: [x, y]}`

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_count_per_spot` | `null` | Minimum total UMI counts per spot to retain |
| `max_count_per_spot` | `null` | Maximum total UMI counts per spot to retain |
| `min_genes_per_spot` | `null` | Minimum detected genes per spot to retain |
| `max_genes_per_spot` | `null` | Maximum detected genes per spot to retain |
| `min_spots_per_gene` | `null` | Minimum fraction of a sample's spots expressing a gene for that sample to pass; must satisfy `0 < value < 1`. Dataset-level only |
| `min_count_spots_ratio_per_gene` | `null` | Minimum ratio of a gene's total counts to its expressed spots, per sample; must be `> 0`. Dataset-level only |
| `remove_mitochondrial_genes` | `false` | Drop mitochondrial genes (`MT-`/`MT.` prefix) from the feature set. Applied per sample |
| `total_counts_normalize` | `false` | Normalize total counts per spot to 10,000 |
| `log1p_transform` | `false` | Apply log(1 + x) transformation |
| `force_recomputing` | `false` | Reprocess even if output already exists |

All filtering and normalization steps are opt-in; the Python method signatures use the
same defaults as the config extractor.

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
| `.X` | Counts (sparse CSR); raw unless `total_counts_normalize`/`log1p_transform` are set |
| `.layers["raw"]` | Raw counts before normalization (sparse CSR). Present **only** when `.X` was normalized |
| `.obs_names` | Spot names, prefixed `<sample_id>_` |
| `.obs["sample_id"]` | Categorical sample identifier |
| `.obs["cluster"]` | Categorical per-sample cluster labels |
| `.obs` (QC columns) | `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt` and their `log1p_` variants |
| `.var["mt"]` | Boolean mitochondrial flag |
| `.var` (QC columns) | `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants |
| `.obsm["spatial"]` | float32 spatial coordinates, shape (N, 2) |
| `.uns["spot_size"]` | float32 array [width, height] in µm (or [1.0, 1.0] if not provided); dict keyed by sample_id in the merged file |

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

Each modality checks whether its output file already exists at the start of processing. If the file is found and `force_recomputing` is not set, the step is skipped and the cached path is returned. There is no hash- or timestamp-based validation: deleting the output file or setting `force_recomputing: true` is the only way to trigger reprocessing.

!!! warning "Changing a processing setting does not invalidate the cache"
    The cache key is the existence of the output file (plus, for the merged MSI/ST files, the set of active sample IDs), **not** the processing settings. Editing a setting and re-running therefore returns the previously computed output unchanged, with no warning. After changing any processing setting, set `force_recomputing: true` for that modality (or delete its outputs) so the new value takes effect.

The Raman pipeline has intermediate caches (`basic_corrected_tiles.npy`, `segmented_tiles.npy`, `raman_corrected_tiles.npy`) that are checked independently at each step, allowing partial resume within a single sample. LIF loading is not cached and runs again on every attempt, since the tile coordinates, pixel size and spectra-slice boundaries feed the later steps. The three files are deleted once the sample's final OME-TIFF exists, so they are only ever found after an interrupted or failed run, which is when they are used. Like the output-level cache, they record no parameter values.

---

## Parallelism and Performance

### Execution model

Modalities are processed one at a time, and within each modality samples are processed sequentially. Peak RAM usage scales with a single sample: typical tissue sections require 40 to 50 GB, and large samples can require up to 100 GB. Loading multiple samples concurrently would multiply this requirement and cause out-of-memory failures.

Parallelism is exploited **within** a single sample's processing steps, where the data is already loaded into memory:

| Modality | Intra-sample parallelism |
|----------|--------------------------|
| Microscopy | No explicit parallelism: every step is a whole-array NumPy or OpenCV call |
| MSI | m/z backbone clustering runs on a `ProcessPoolExecutor` across CPU cores; intensity interpolation uses `joblib` parallel workers (one chunk per core) |
| Raman | BaSiC correction parallelizes spectral channels via a `ThreadPoolExecutor` (one subprocess per channel); spectral cleaning parallelizes tile × spectra-slice work units via `joblib` |
| ST | Single-threaded (scanpy and scipy operations are internally vectorized) |

### Memory management

The entire stack is built on natively compiled, memory-efficient libraries (NumPy, SciPy, pandas, numba/Numba JIT) that operate on contiguous arrays and avoid Python-level loops wherever possible. Key design decisions:

- **Microscopy**: the full-resolution image is held once as a float32 `(H, W, C)` array. Tissue detection runs on a proxy capped at 9 megapixels and releases each intermediate (uint8 copy, blurred image, thresholded mask) as soon as it is consumed, and the contrast-stretch percentiles are estimated from a strided subsample above the same cap, so neither step allocates a second full-resolution working set.
- **MSI**: spectra are read directly from the binary IBD file with `np.fromfile` (zero-copy memory mapping). Intensity matrices are kept as sparse CSR throughout. After each processing step the intermediate dense arrays are explicitly deleted and `gc.collect()` is called. The merge step uses an on-disk concatenation routine to avoid loading all samples simultaneously.
- **Raman**: tiles are stored as a single contiguous `(T, C, Y, X)` float32 array, and each stage writes its result to a `.npy` cache so an interrupted run can resume from it. The raw, illumination-corrected/masked, and spectrally cleaned stacks are all held on the sample object for the duration of that sample; within background removal, the intermediate mask images are explicitly released as soon as they are consumed.
- **MSI m/z clustering**: memory per chunk is estimated from available RAM at runtime (`psutil.virtual_memory().available`) before the job is dispatched, so the chunk count scales to the machine.
- **ST**: sparse CSR matrices are used end-to-end; concatenation uses an outer join to avoid densifying the gene matrix.

### GPU

FOCUS does not use GPU acceleration in the preprocessing stage. GPU resources are reserved for the registration stage (PyTorch/CUDA-based deformable registration). All preprocessing computations run on CPU.

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

- Open in QuPath, Napari, or FIJI and verify that background removal did not erase tissue regions.
- Check that the tissue is not over-cropped (increase `crop_margin` if needed).
- If color enhancement produced over-saturation, reduce `contrast_saturation` or set `color_enhancement: false`.

---

## Next Steps

After inspecting preprocessed outputs:

1. **Adjust parameters** if any modality shows poor output quality, then rerun with `force_recomputing: true` for that modality.
2. **Proceed to Alignment**: once satisfied, continue to the [Alignment Stage](alignment.md).

## Additional Resources

- [Alignment Documentation](alignment.md) - Next pipeline stage
- [Configuration Reference](../configuration/config_fields.md) - Preprocessing parameters
- [API Reference: Preprocessing](../api/preprocessing.md) - Programmatic access
