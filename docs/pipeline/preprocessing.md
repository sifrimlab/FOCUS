# Preprocessing Stage

## Overview

The preprocessing stage is the first step in the FOCUS pipeline, where raw data from each modality is cleaned, normalized, and converted to standardized formats for subsequent alignment and registration.

For each modality defined in the configuration, FOCUS discovers all sample subdirectories, creates the output directory structure, and dispatches to the modality-specific processing pipeline. Preprocessing can be skipped for any modality if the output file already exists and `force_recomputing` is not set.

## Modality-Specific Preprocessing

Each modality undergoes specialized processing tailored to its data characteristics:

---

### 1. Microscopy Image Preprocessing

**Input Formats**: `.ome.tiff`, `.ome.tif`, `.qptiff`, `.tiff`, `.tif`, `.czi` (searched in this priority order)  
**Output Format**: Multi-resolution OME-TIFF, stored in the source file's dtype (`uint8`/`uint16` pass through; float sources stay `float32`), zlib-compressed with a predictor matched to the storage dtype

**Processing Steps**:

1. **File Loading and Normalization**
   - Detect file format (TIFF/OME-TIFF vs qpTIFF vs CZI)
   - For qpTIFF: compare all series/pyramid levels by pixel count and load only the highest-resolution one
   - For CZI: squeeze extra dimensions, use first scene only if multiple scenes are present
   - Move channel axis to last position using a shape heuristic
   - Convert to float32 in [0, 1] range (using dtype maximum or image maximum)
   - Clip to at most 3 channels

2. **Color Enhancement** (optional, `color_enhancement=True`)
   - Gamma correction: `I = I^gamma` (default γ=0.45, which brightens the image)
   - Contrast stretching using percentile-based saturation (default: saturate 0.35% of pixels)

3. **Background Removal** (optional, `remove_background=True`) — runs on a downsampled proxy capped at ~9 megapixels rather than the full-resolution image (a tissue boundary is a smooth shape that doesn't need full-resolution input to locate), with the resulting mask upsampled back to full resolution before being applied
   - Replace pure-black pixels with white to avoid thresholding artifacts
   - Convert to grayscale and invert (white background becomes dark)
   - Clip at `clip_percentile` (default: 99th percentile) then apply Gaussian blur (kernel fixed at 25 px, tuned for the detection-proxy canvas)
   - Compute Otsu threshold on the blurred image, apply to unblurred grayscale
   - Remove small connected components (fixed at < 50 pixels, tuned for the detection-proxy canvas)
   - Fill holes in the binary tissue mask
   - Refine by contour area: keep only contours covering ≥ `min_object_coverage` fraction of image area (default: 1%)
   - Apply mask: tissue pixels are kept, background is filled with the background color

4. **Tissue Cropping** (optional, `crop_to_tissue=True`)
   - Identify non-background pixels from the filled background color
   - Compute bounding box of tissue region
   - Add `crop_margin` pixels on all sides (default: 250 px), clamped to image boundaries

5. **Pyramid Construction and Saving**
   - Build resolution levels by successive 2× downsampling. The number of levels is **computed automatically** from the image dimensions so the smallest level fits within a 3,000 × 3,000 pixel cap (for GUI rendering); it is not configurable.
   - Quantize each level from float32 back to the source file's original dtype (`uint8`/`uint16` pass through; float sources stay `float32`)
   - Write as multi-image BigTIFF OME-TIFF with zlib compression and a predictor matched to the storage dtype (2 for integer, 3 for float)
   - RGB images: interleaved `YXC` layout; single/multi-channel: separate planes per channel

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `color_enhancement` | `true` | Apply gamma correction and contrast stretching |
| `gamma` | `0.45` | Gamma exponent (< 1 brightens, > 1 darkens) |
| `contrast_saturation` | `0.35` | Percentage of pixels to saturate when stretching contrast |
| `remove_background` | `true` | Remove background using Otsu thresholding |
| `background_color` | `"white"` | Color to fill removed background (`"white"` or `"black"`) |
| `clip_percentile` | `99` | Intensity percentile for clipping before blur |
| `min_object_coverage` | `0.01` | Minimum tissue area fraction (0–1) for contour filtering |
| `crop_to_tissue` | `true` | Crop image to tissue bounding box |
| `crop_margin` | `250` | Pixel margin added around the tissue bounding box |
| `force_recomputing` | `false` | Reprocess even if output already exists |

The number of pyramid resolution levels is not a parameter — it is computed automatically from the image size. The Gaussian blur kernel size and minimum object size used during tissue detection are also not parameters — detection always runs on a downsampled proxy capped at ~9 megapixels, so these are fixed internal constants tuned for that canvas rather than the source image's native resolution.

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.ome.tiff
```

---

### 2. MSI (Mass Spectrometry Imaging) Preprocessing

**Input Formats**: `.imzML` + `.ibd` pairs inside `pos/` and/or `neg/` subdirectories  
**Output Format**: AnnData (`.h5ad`) — one per sample and one merged across all samples

The MSI pipeline operates at the **dataset level**: all samples are processed together to compute a shared reference m/z backbone, ensuring consistent feature alignment across samples.

**Processing Steps**:

1. **Initialization and Metadata Parsing** (per sample)
   - Detect ion modes from subdirectory presence (`pos/`, `neg/`, or both)
   - Parse imzML XML: extract data types, raster size (µm), pixel coordinates, and physical coordinates
   - Correct rotation error in physical coordinates via linear regression on the densest pixel column
   - If double ion mode: filter unpaired spots (experimental artifacts), compute affine alignment between positive and negative physical coordinates, and average the two coordinate sets
   - Normalize physical coordinates to origin; shift to raster center
   - Compute raster bounding-box coordinates for each spot (in µm)

2. **Background Detection** (optional, `detect_background=True`)
   - For each spot, compute three spectral complexity features: Shannon entropy of the normalized intensity distribution, number of detected peaks, and log(1 + TIC)
   - If a lipid annotation database is provided: add a 4th feature (fraction of peaks matching the DB at the configured mass tolerance)
   - Min-max normalize each feature and average into a composite score
   - **Tissue sections** (`sample_type="tissue"`): fit a 1-component and a 2-component Gaussian Mixture Model; use BIC to select between them. If the 2-component model wins, classify spots with posterior ≥ 0.5 on the higher-mean component as tissue. Apply morphological cleanup (hole filling + binary opening) on the pixel grid.
   - **Microgrid samples** (`sample_type="microgrid"`): use Otsu thresholding with a 25th-percentile floor to protect weak single-cell signals; no spatial cleanup.
   - The foreground classification is stored as `obs["foreground"]`; all spots (including background) are included in the output and can be filtered downstream.

3. **Recalibration Reference Selection**
   - Randomly subsample 30% of spectra per sample to estimate representative m/z values
   - For each ion mode, greedily select the 5 highest-scoring candidate m/z values (scored by global frequency × sample coverage) that collectively cover all samples
   - Alternatively, a user-supplied `recalibration_reference` dictionary can be passed directly

4. **Per-Row m/z Recalibration** (per sample)
   - For each reference m/z peak, find the highest-intensity peak within `mass_tolerance` in each spectrum
   - Compute per-column (spatial row) mean offset between observed and reference m/z values
   - Apply the row-wise offset to all m/z values in that row

5. **Per-Sample m/z Backbone Computation**
   - Pool all (recalibrated) m/z values from foreground spots of a sample
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

9. **Intensity Normalization** (applied independently per ion mode)
   - `"tic"`: divide each spectrum by its total ion count (each spectrum sums to 1)
   - `"log"`: apply log(1 + x) transform
   - `"clr"`: sparsity-preserving centered log-ratio — log-centers each spectrum over its nonzero entries only, leaving structural zeros at 0
   - `"global_scaling"`: rescale each spectrum to the mean total ion count of its ion mode (each spectrum's total becomes the mean TIC) — like `"tic"` but preserves an interpretable absolute intensity scale instead of forcing a sum of 1
   - `"none"`: keep raw interpolated intensities

10. **Per-Sample Leiden Clustering**
    - PCA (up to 50 components) → neighbor graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`) on the normalized matrix
    - Labels stored in `obs["leiden"]`. The PCA embedding, neighbor graph, and associated metadata are then discarded (only `obs["leiden"]` is kept) to minimize file size

11. **Save Per-Sample AnnData and Merge**
    - Each sample is saved separately (gzip compression)
    - All samples are concatenated on disk into a single merged h5ad (inner join on features, gzip compression)
    - Merged file's `uns["spot_size"]` updated to a per-sample dict

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mass_tolerance` | `10` | Mass tolerance in ppm for m/z clustering, recalibration, and annotation |
| `frequency_threshold` | `0.01` | Minimum fraction of max cluster weight for backbone m/z inclusion |
| `intensity_normalization` | `"none"` | Normalization method (per ion mode): `"tic"`, `"log"`, `"clr"`, `"global_scaling"`, or `"none"` |
| `recalibration_reference` | `null` | User-supplied reference m/z dict per ion mode; auto-computed if null |
| `min_intensity_threshold` | `10000.0` | Minimum intensity for a peak to be used in recalibration offset estimation |
| `detect_background` | `false` | Run background detection to classify tissue vs background spots |
| `sample_type` | `"tissue"` | Sample type for background detection: `"tissue"` or `"microgrid"` |
| `lipid_annotation_db` | `null` | Path to lipid annotation database (CSV or JSON with `db_name`, `ionized_mass`, `ion_mode` columns) |
| `force_recomputing` | `false` | Reprocess even if output already exists |

The defaults above are the values applied when running through the configuration file (the pipeline's settings extractor). When calling `MsiDataset.process_dataset()` directly in Python, two signature defaults differ: `intensity_normalization` defaults to `"tic"` and `detect_background` defaults to `True`.

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
| `.obs["foreground"]` | Boolean: tissue (True) vs background (False) |
| `.obs["leiden"]` | Categorical Leiden cluster labels |
| `.obsm["spatial"]` | Physical spot center coordinates in µm, shape (N, 2), float32 |
| `.obsm["raster_coordinates"]` | Raster bounding boxes in µm, shape (N, 2, 2): [[x1,y1],[x2,y2]] |
| `.var["mz"]` | Consensus reference m/z values (float32) |
| `.var["mz_mode"]` | Ion mode for each m/z: `"pos"` or `"neg"` |
| `.var["lipid_annotation"]` | Lipid annotation string (if DB provided, else `"Unannotated"`) |
| `.uns["spot_size"]` | Raster pixel size [width, height] in µm (per-sample: list; merged: dict keyed by sample_id) |

---

### 3. Raman Spectroscopy Imaging Preprocessing

**Input Formats**: `.lif` (Leica Image File format)  
**Output Format**: Multi-channel OME-TIFF (hyperspectral, uint8, zlib-compressed)

The pipeline has five labeled steps. All steps are always executed; none can be individually disabled via configuration — only the per-step parameters are tunable. Intermediate results are cached as `.npy` files and deleted after the final OME-TIFF is produced.

**Processing Steps**:

1. **LIF File Loading and Metadata Parsing**
   - Scan the input directory for the first `.lif` file
   - Parse LIF XML metadata: scan dimensions (width, height), spectral parameters (wavenumber range, number of steps, laser pump wavelength), tile count, tile coordinates (µm), and pixel size (µm)
   - Only tiled acquisitions (tile count ≥ 2) are processed; single-field images are skipped
   - Extract raw tile data as float32 array of shape (T, C, Y, X) where T = tiles, C = spectral channels
   - If multiple spectral scans are present in the LIF: concatenate along the channel axis and resolve wavenumber overlaps (re-scanned spectral regions are trimmed at the overlap boundary)
   - Normalize to [0, 1] (divides by 255 or 65535 depending on data range)
   - Compute wavenumber array from laser excitation range and Stokes shift

2. **BaSiC Illumination Correction**
   - Requires the `FOCUS_BaSiCpy` conda environment
   - Each spectral channel is processed independently via a subprocess call to the BaSiCpy tool
   - Channels are processed in parallel using a thread pool (up to `max_workers` threads)
   - Output is globally normalized to [0, 1] across all channels and tiles

3. **Background Removal**
   - Quick-stitch BaSiC-corrected tiles into a mosaic using weighted blending (distance-transform weights)
   - Reduce the hyperspectral mosaic to a single grayscale image via PCA (1 component)
   - Apply CLAHE for local contrast enhancement
   - Compute Otsu threshold, scaled by `otsu_threshold_factor` (default: 0.7, lowers threshold to be more inclusive)
   - Remove small objects (< `min_object_size` pixels, default: 500)
   - Fill holes in the binary mask
   - Filter by contour area: keep contours covering ≥ `bg_min_area_fraction` of image area (default: 5%)
   - Back-project the mosaic mask onto individual tiles to generate per-tile segmentation masks
   - Zero out background regions in the BaSiC-corrected tiles

4. **Spectral Cleaning** (per tile, parallel)
   - Skip zero-variance spectra (constant signal that causes numerical errors)
   - Apply RamanSPy pipeline to each non-zero spectrum:
     1. **Despiking**: Whitaker-Hayes cosmic ray removal
     2. **Denoising**: Savitzky-Golay filter (default: window=7, polyorder=3)
     3. **Baseline correction**: IASLS algorithm
     4. **Normalization**: MinMax per-spectrum
   - Tiles processed in parallel (up to `max_workers` workers)
   - Results cached to disk (`raman_corrected_tiles.npy`)

5. **ASHLAR Stitching**
   - Requires the `FOCUS_ASHLAR` conda environment
   - Flip y-axis of tile coordinates (Leica → OME-TIFF convention)
   - Select the highest mean-intensity spectral channel within the first scan as the alignment reference
   - Write per-cycle OME-TIFF input files with embedded physical coordinates and pixel size metadata
   - Run ASHLAR via subprocess to stitch tiles with sub-pixel alignment
   - Rename output to `<modality_name>_<sample_id>_processed.ome.tiff`

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | `8` | Maximum parallel workers for BaSiC correction and spectral cleaning |
| `savgol_window` | `7` | Savitzky-Golay filter window length for denoising |
| `savgol_polyorder` | `3` | Savitzky-Golay filter polynomial order |
| `bg_min_area_fraction` | `0.05` | Minimum contour area as fraction of mosaic area for background removal |
| `otsu_threshold_factor` | `0.7` | Multiplicative factor applied to the Otsu threshold before background segmentation |
| `min_object_size` | `500` | Minimum connected component size in pixels for morphological cleanup |
| `force_recomputing` | `false` | Reprocess even if output already exists |

**Output Files**:
```
<dataset_path>/<sample_id>/preprocessing/<modality_name>/
└── <modality_name>_<sample_id>_processed.ome.tiff
```

---

### 4. Spatial Transcriptomics Preprocessing

**Input Formats**: AnnData (`.h5ad`) — the first `.h5ad` file found in the sample directory  
**Output Format**: AnnData (`.h5ad`) — one per sample and one merged

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
   - `scanpy.pp.calculate_qc_metrics(qc_vars=["mt"], percent_top=None)` on the retained spots (mito genes still present, so `pct_counts_mt` is meaningful)
   - Adds per-spot (`n_genes_by_counts`, `total_counts`, `pct_counts_mt`, ...) and per-gene (`n_cells_by_counts`, ...) metrics as inspectable metadata

5. **Mitochondrial Gene Removal** (optional)
   - When `remove_mitochondrial_genes=true`, drop the `.var["mt"]`-flagged genes from the feature set. Off by default (high mito fraction is often biological in spatial data)
   - Observation names are then prefixed with `<sample_id>_` to ensure uniqueness across samples

6. **Raw Count Preservation**
   - Store the filtered, post-feature-selection (unnormalized) count matrix in `.layers["raw"]`

7. **Normalization** (both optional, default off)
   - Total count normalization: scale each spot to `target_sum = 10,000`
   - log(1 + x) transformation
   - With both off (the default), `.X` stays raw; downstream stages consume it as-is

8. **Per-Sample Leiden Clustering**
   - Labels colour spots during alignment; only `.obs["leiden"]` is kept
   - Computed on a throwaway, internally normalized + log1p copy (so labels are meaningful even when `.X` is raw): PCA (up to 50 components) → neighbor graph → Leiden (`resolution=0.5`, `flavor="igraph"`, `n_iterations=2`, `directed=False`)
   - PCA/neighbour-graph intermediates are **not** persisted; samples with < 2 spots (or too few PCs) get label `'0'`

**Dataset-Level (Merged) Processing**:

After per-sample preprocessing, all samples are merged:

1. Reload per-sample files; revert `.X` to raw counts from `.layers["raw"]`
2. Concatenate with **outer join**: genes absent from a sample are filled with 0 counts
3. **Cross-sample gene filtering** (both optional):
   - `min_spots_per_gene`: a gene must be expressed in ≥ this fraction of spots in ≥ 5% of samples
   - `min_count_spots_ratio_per_gene`: a gene's (total counts / expressed spots) ratio must exceed this value in ≥ 5% of samples; samples where the gene is absent are excluded from the denominator
   - Note: the design preserves every gene with signal in ≥ 1 sample (rare cell-type markers); FOCUS does not subset to highly variable genes
4. Recompute QC metrics on the merged matrix so `.obs`/`.var` QC reflect the retained spots/genes
5. Store post-filter raw counts in `.layers["raw"]`
6. Normalize merged `.X` (same opt-in `total_counts_normalize` / `log1p_transform` flags)
7. Per-sample Leiden labels from individual processing are preserved in `.obs["leiden"]`

**Parameters**:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_count_per_spot` | `null` | Minimum total UMI counts per spot to retain |
| `max_count_per_spot` | `null` | Maximum total UMI counts per spot to retain |
| `min_genes_per_spot` | `null` | Minimum detected genes per spot to retain |
| `max_genes_per_spot` | `null` | Maximum detected genes per spot to retain |
| `min_spots_per_gene` | `null` | Minimum fraction of spots per sample expressing a gene (0–1) |
| `min_count_spots_ratio_per_gene` | `null` | Minimum ratio of total counts to expressed spots per gene |
| `remove_mitochondrial_genes` | `false` | Opt-in. Drop mitochondrial genes (`MT-`/`MT.` prefix) from the feature set |
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
| `.layers["raw"]` | Filtered, post-feature-selection raw counts (sparse CSR) |
| `.obs["sample_id"]` | Categorical sample identifier |
| `.obs["leiden"]` | Categorical per-sample Leiden cluster labels |
| `.obs` (QC columns) | `n_genes_by_counts`, `total_counts`, `pct_counts_mt`, etc. from `calculate_qc_metrics` |
| `.obsm["spatial"]` | float32 spatial coordinates, shape (N, 2) |
| `.uns["spot_size"]` | float32 array [width, height] in µm (or [1.0, 1.0] if not provided); dict keyed by sample_id in merged file |

---

## File Naming Convention

FOCUS uses a consistent naming pattern for all preprocessed outputs.

**Per-Sample Files**:
```
<modality_name>_<sample_id>_processed.<ext>
```

**Merged Files** (omics modalities only — microscopy and Raman produce no merged output):
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

The Raman pipeline has intermediate caches (`basic_corrected_tiles.npy`, `segmented_tiles.npy`, `raman_corrected_tiles.npy`) that are checked independently at each step, allowing partial resume within a single sample. These intermediate files are deleted after the final OME-TIFF is produced.

---

## Parallelism and Performance

### Execution model

Modalities are processed one at a time, and within each modality samples are processed sequentially. This is intentional: peak RAM usage scales with a single sample — typical tissue sections require 40–50 GB, and large samples can require up to 100 GB. Loading multiple samples concurrently would multiply this requirement and cause out-of-memory failures.

Parallelism is exploited **within** a single sample's processing steps, where the data is already loaded into memory:

| Modality | Intra-sample parallelism |
|----------|--------------------------|
| Microscopy | Single-threaded (image ops are already vectorized) |
| MSI | m/z backbone clustering runs on a `ProcessPoolExecutor` across CPU cores; intensity interpolation uses `joblib` parallel workers (one chunk per core) |
| Raman | BaSiC correction parallelizes spectral channels via a `ThreadPoolExecutor`; spectral cleaning parallelizes tiles via `joblib` |
| ST | Single-threaded (scanpy and scipy operations are internally vectorized) |

### Memory management

The entire stack is built on natively compiled, memory-efficient libraries (NumPy, SciPy, pandas, numba/Numba JIT) that operate on contiguous arrays and avoid Python-level loops wherever possible. Key design decisions:

- **MSI**: spectra are read directly from the binary IBD file with `np.fromfile` (zero-copy memory mapping). Intensity matrices are kept as sparse CSR throughout. After each processing step the intermediate dense arrays are explicitly deleted and `gc.collect()` is called. The merge step uses an on-disk concatenation routine to avoid loading all samples simultaneously.
- **Raman**: tiles are stored as a single contiguous `(T, C, Y, X)` float32 array. Intermediate `.npy` caches are written to disk and deleted from memory between pipeline stages to keep the working set small.
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

# UMAP colored by cluster
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.pl.umap(adata, color="leiden")
```

### MSI

After preprocessing, inspect the foreground classification and the quality of the m/z alignment:

```python
import anndata as ad
import matplotlib.pyplot as plt

adata = ad.read_h5ad("path/to/<modality>_<sample>_processed.h5ad")

# Spatial map of foreground mask
coords = adata.obsm["spatial"]
fg = adata.obs["foreground"].astype(bool)
plt.scatter(coords[~fg, 0], coords[~fg, 1], c="lightgray", s=1, label="background")
plt.scatter(coords[fg, 0], coords[fg, 1], c="steelblue", s=1, label="foreground")
plt.legend(); plt.axis("equal"); plt.show()

# Spatial map of a known lipid (if annotated)
import numpy as np
hits = np.where(adata.var["lipid_annotation"] != "Unannotated")[0]
if hits.size:
    sc.pl.spatial equivalent or plt scatter with adata.X[:, hits[0]].toarray().ravel()

# Leiden clusters in embedding space
import scanpy as sc
sc.pp.neighbors(adata)
sc.tl.umap(adata)
sc.pl.umap(adata, color=["leiden", "foreground"])
```

### Raman

The output OME-TIFF contains one channel per wavenumber bin. Useful checks:

- Open in QuPath, Napari, or FIJI and inspect individual spectral channels for stitching artifacts or residual background.
- Sum-project all channels to produce a pseudo-brightfield image and verify tissue coverage and tile alignment.
- Load a subset of channels corresponding to known Raman bands (e.g., ~1004 cm⁻¹ phenylalanine, ~2850 cm⁻¹ lipids) and check spatial signal distribution.

```python
import tifffile, numpy as np, matplotlib.pyplot as plt

img = tifffile.imread("path/to/<modality>_<sample>_processed.ome.tiff")
# img shape: (C, H, W) or (H, W, C) depending on how ASHLAR wrote it

# Sum projection
plt.imshow(img.sum(axis=0) if img.ndim == 3 else img.sum(axis=-1), cmap="gray")
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
