# FOCUS — Architecture & Component Analysis

**Version analyzed:** 0.1.0
**Total Python LOC:** ~6,300 lines across 13 files
**Supported modalities:** Microscopy Images, MSI (Mass Spectrometry Imaging / Lipidomics), Raman Spectroscopy, Spatial Transcriptomics (ST)

---

## 1. High-Level Architecture

FOCUS implements a three-stage pipeline for spatial multiomics data:

```
[Raw Data] → PREPROCESSING → ALIGNMENT → REGISTRATION → [Multimodal Dataset]
```

Each stage operates on a **dataset** of **samples**, where each sample may contain multiple modalities. The pipeline is configured via a JSON config file and can be run via CLI (`main.py`) or programmatically (`debug.py` demonstrates this).

### Directory Convention

The pipeline expects and produces this on-disk layout:

```
<data_source_path>/
├── <sample_id_1>/
│   ├── <modality_name>/          ← raw input
│   ├── preprocessing/<modality>/ ← preprocessed output
│   ├── alignment/                ← aligned output
│   └── registration/             ← registered output
├── <sample_id_2>/
│   └── ...
└── merged/
    ├── preprocessing/            ← combined across samples
    ├── alignment/
    └── registration/
```

---

## 2. Entry Points

### 2.1 `main.py` — CLI Entry Point

- Parses a JSON config via argparse (`-c` flag)
- Validates config with `utils.parse_config()`
- Runs preprocessing for each modality (if enabled)
- Runs alignment of each non-anchor modality to the anchor

**Issues identified:**
- Imports `Aligner` from `alignment.alignment`, but the actual class is `DirectMappingAligner` — **import will fail at runtime**
- Uses bare `import utils` and `import preprocessing.preprocessing` (non-package-relative imports) — inconsistent with the `focus.` prefixed imports used in other files
- Registration step is not implemented in main.py (only preprocessing + alignment)
- The `sample_id` parameter was removed from `preprocess_modality()` but is still passed in main.py — **will fail at runtime**

### 2.2 `debug.py` — Programmatic Usage Example

- Hardcoded paths and an exposed HuggingFace token
- Demonstrates the full pipeline: preprocessing → alignment → registration
- Uses `focus.`-prefixed imports (different from main.py)

### 2.3 `GUI/direct_mapping_alignment.py` — Flask Web GUI

- Serves a Vite-bundled frontend for manual coordinate alignment
- Runs on `localhost:8000`
- Provides REST API: `/status`, `/<type>/metadata`, `/<type>/payload`, `/confirm`
- Synchronization between the backend alignment thread and GUI uses `threading.Event`

---

## 3. Core Module: `constants.py`

### Path Templates (lambdas)

| Lambda | Purpose | Output file type |
|--------|---------|-----------------|
| `MODALITY_PREPROCESSING` | Per-sample preprocessed file | `{mod}_{sample}_processed.{ext}` |
| `MODALITY_PREPROCESSING_MERGED` | Cross-sample merged preprocessed file | `{mod}_merged_processed.{ext}` |
| `MODALITY_ALIGNMENT` | Per-sample aligned file | `{mod}_{sample}_processed_aligned.{ext}` |
| `MODALITY_ALIGNMENT_MERGED` | Merged aligned file | `{mod}_merged_processed_aligned.{ext}` |
| `MODALITY_REGISTRATION` | Per-sample registered file | `{mod}_{sample}_processed_aligned_registered.{ext}` |
| `MODALITY_REGISTRATION_MERGED` | Merged registered file | `{mod}_merged_processed_aligned_registered.{ext}` |
| `MULTIMODAL_DATASET` | Final multimodal dataset | `multimodal_dataset.{ext}` |

### Enum-like Classes (all inherit `_AbstractEnum`)

| Class | Purpose |
|-------|---------|
| `FocusOutputDirectories` | Standard output folder names |
| `ImzMLFileParser` | XML namespace constants for imzML parsing |
| `SegmentationBackgroundColor` | Background color options (WHITE/BLACK) |
| `MsiMetadata` | Keys for MSI metadata dictionary |
| `MsiIonMode` | Positive/Negative ion modes |
| `ConfigParameters` | Top-level config JSON keys |
| `ModalityParameters` | Per-modality config keys |
| `RegistrationSettings` / `RegistrationType` | Registration config |
| `MicroscopyImageProcessingParams` | Microscopy preprocessing params |
| `MsiPreprocessingParams` | MSI preprocessing params |
| `RamanPreprocessingParams` | Raman preprocessing params |
| `STPreprocessingParams` | ST preprocessing params |
| `ModalityType` | Supported modality types |
| `TransformationType` | Geometric transformation types |
| `MsiIntensityNormalization` | MSI normalization methods |
| `DecompositionMethod` | PCA/NMF decomposition |

**Issues identified:**
- Uses custom `_AbstractEnum` pattern instead of Python's `enum.Enum` — loses type safety, IDE support, and `in` operator behavior
- `_AbstractEnum.list()` returns values via `vars()` introspection — fragile and order-dependent
- `TransformationType`, `DecompositionMethod`, `RegistrationSettings`, `RegistrationType` are defined but unused in the codebase
- Path templates use lambdas instead of proper functions — no type hints, no docstrings

---

## 4. Core Module: `utils.py`

| Function | Purpose |
|----------|---------|
| `available_cpus()` | Returns CPU count respecting cgroups/affinity |
| `parse_config(config)` | Validates JSON config structure |
| `enhance_contrast(channel)` | Histogram stretching with saturated pixel clipping |
| `gamma_correction(channel)` | Power-law gamma correction |

**Issues identified:**
- `parse_config()` docstring says it returns a dict, but it actually returns `None`
- `enhance_contrast` and `gamma_correction` operate on single channels but are called on whole images in `microscopy_image.py` without per-channel splitting

---

## 5. Preprocessing Module

### 5.1 `preprocessing/preprocessing.py` — Orchestrator

**Function:** `preprocess_modality(path, modality_name, modality_type, preprocessing_settings) → dict[str, str]`

This is the single entry point for all preprocessing. It:
1. Discovers samples by listing subdirectories under `path`
2. Creates output directories
3. Dispatches to modality-specific classes based on `modality_type`
4. Returns `{sample_id: output_path, "merged": merged_path}`

**Issues identified:**
- Massive if/elif chain — each modality branch extracts settings, creates Sample objects, creates Dataset objects, and calls `process_dataset()` with different signatures
- No common interface: each modality's Dataset class has a completely different `process_dataset()` signature
- The `sample_id` parameter was removed from the function signature but `main.py` still passes it
- The function is 210 lines of essentially duplicated boilerplate per modality

### 5.2 `preprocessing/microscopy_image.py` — Microscopy Image Processing

**Classes:** `MicroscopyImage` (single sample), `MicroscopyImageDataset` (collection)

**`MicroscopyImage` pipeline:**
1. Load TIFF or CZI file → normalize to float32 [0,1], channels-last
2. Optional: color enhancement (gamma + contrast stretch)
3. Optional: background removal (Otsu threshold + morphological cleaning)
4. Optional: crop to tissue bounding box (250px margin)
5. Save as multi-resolution OME-TIFF pyramid

**Output format:** OME-TIFF (float32, multi-resolution pyramid)

**Issues identified:**
- Channel detection via `np.argmin(shape)` is fragile — fails if spatial dim < channel count (e.g., tiny image with 4 channels)
- `_load_tiff` and `_load_czi` have near-identical post-processing logic (transpose, normalize, clip channels) — violates DRY
- `_remove_background` has hardcoded Gaussian blur kernel size (251,251) — not adaptable to different image scales
- `_crop_image` hardcodes 250px margin
- `_save_image_pyramid` mixes OME-XML construction with TIFF writing — complex and hard to test
- `MicroscopyImageDataset.process_dataset` silently catches and prints exceptions — errors are lost

### 5.3 `preprocessing/lipidomics.py` — MSI / Lipidomics Processing (1,906 lines)

**Classes:** `MsiSample` (single sample), `MsiDataset` (collection)

**`MsiSample` responsibilities:**
- Parse imzML XML metadata (custom XML parser, not using `pyimzml`)
- Read binary IBD files for m/z and intensity vectors
- Handle dual ion mode (positive + negative) with coordinate alignment
- Correct rotation error between physical and pixel coordinates
- Recalibrate m/z vectors using reference peaks
- Filter background spots using annotation-based GMM classification

**`MsiDataset` pipeline (9 steps):**
1. Initialize samples (parse metadata)
2. Select high-confidence tissue spots (optional background detection)
3. Find/use recalibration reference m/z peaks
4. Compute per-sample reference m/z backbone
5. Compute global reference m/z backbone
6. Annotate m/z features using lipid database
7. Interpolate intensities to reference m/z grid (parallel, Numba-optimized)
8. Generate per-sample AnnData objects with Leiden clustering
9. Merge all samples into combined dataset

**Output format:** AnnData (h5ad) with:
- `X`: raw interpolated intensities (spots × m/z features)
- `layers["X_<normalization>"]`: normalized intensities
- `obsm["spatial"]`: physical coordinates
- `obsm["raster_coordinates"]`: raster pixel coordinates
- `obs["foreground"]`: background detection mask
- `obs["leiden"]`: cluster labels
- `var["mz"]`, `var["mz_mode"]`, `var["lipid_annotation"]`: feature metadata
- `uns["raster_size"]`: raster pixel dimensions

**Issues identified:**
- Largest file (1,906 lines) — multiple responsibilities crammed into `MsiSample`
- `_filter_datapoint_without_annotations` is 270 lines of dense statistical computation — should be its own module
- `_spectra_to_dict` manually parses XML spectrum-by-spectrum — very slow for large datasets
- `load_payload` modifies internal state (`filtered_idx`, `recalibration_offset`) as a side effect
- `process_dataset` is 370 lines with 9 sequential steps — orchestration logic mixed with computation
- Memory management via explicit `gc.collect()` calls — indicates design issue
- Custom imzML parser instead of using `pyimzml` library
- Three separate `load_payload` calls per sample during dataset processing (steps 2, 4, 7) — data is re-read from disk each time
- `@staticmethod` missing on `_compute_raster_coordinates`

### 5.4 `preprocessing/raman.py` — Raman Spectroscopy Processing (1,383 lines)

**Classes:** `RamanMetadata` (metadata container), `RamanImage` (single sample), `RamanDataset` (collection)

**`RamanMetadata`:** Property-based container with validation for all Raman scan parameters (name, wavelength range, tile info, pixel size).

**`RamanImage` pipeline:**
1. Load LIF file → parse metadata → extract tiled hyperspectral data
2. Handle wavenumber overlaps (re-scanned regions)
3. BaSiC correction (via external conda environment `FOCUS_BaSiCpy` subprocess)
4. Background removal (Otsu + morphological, applied to quick-stitched mosaic then back-projected to tiles)
5. Raman spectral cleaning per tile (RamanSPy: despike → denoise → baseline → normalize)
6. ASHLAR stitching (via external conda environment `FOCUS_ASHLAR` subprocess)

**Output format:** OME-TIFF (stitched hyperspectral mosaic)

**Issues identified:**
- Two external conda environment subprocess calls (BaSiC, ASHLAR) — fragile, hard to debug, tight coupling to local environment
- `_load_lif` is 140 lines — single method doing too much
- `_parse_lif_metadata` is 150 lines of XML traversal with multiple fallback paths
- `basic_correct` shells out to a Python subprocess per spectral channel — very high process creation overhead
- `_quick_stitch` is 100 lines implementing a custom mosaic stitcher just for visualization
- Background removal code is duplicated from `microscopy_image.py` (Otsu + morphological approach)
- Intermediate .npy files used as caching mechanism — no versioning or invalidation
- `tools_basedir` path computed via string replacement on `__file__` — fragile
- `RamanMetadata` has 12 properties each with getter/setter/validation — could use `dataclasses` or `pydantic`

### 5.5 `preprocessing/transcriptomic.py` — Spatial Transcriptomics Processing

**Classes:** `SpatialTranscriptomic` (single sample), `SpatialTranscriptomicDataset` (collection)

**`SpatialTranscriptomic` pipeline:**
1. Load first .h5ad file found in sample directory
2. Calculate QC metrics (mitochondrial genes)
3. Filter cells by count/gene thresholds
4. Save with unique observation names

**`SpatialTranscriptomicDataset` pipeline:**
1. Process each sample individually
2. Concatenate all samples
3. Filter genes by cross-sample expression frequency
4. Filter genes by count/spots ratio
5. Normalize (total counts) and log1p transform
6. Save combined dataset

**Output format:** AnnData (h5ad)

**Issues identified:**
- `preprocess_data` signature doesn't include all parameters that `process_dataset` passes (missing `min_genes_per_spot`, `max_genes_per_spot` — wait, they are there as optional params)
- `NUM_SAMPLES_FILTER = 0.05` hardcoded constant inside method
- Gene filtering logic (steps 3-4) is applied at the dataset level but could be refactored
- Validation is duplicated between `SpatialTranscriptomic.preprocess_data` and `SpatialTranscriptomicDataset.process_dataset`

---

## 6. Alignment Module

### 6.1 `alignment/alignment.py` — `DirectMappingAligner`

Handles interactive coordinate-based alignment between two modalities. The reference and target can each be either IMAGE (OME-TIFF) or SPOT (AnnData with spatial coordinates).

**Flow:**
1. Find common samples between reference and target
2. Start a Flask GUI server (`DirectMappingAlignmentGUI`)
3. For each sample, prepare data and push to GUI
4. User manually aligns coordinates in the web interface
5. Receive aligned coordinates from GUI
6. Scale coordinates to full resolution
7. Save aligned AnnData/TIFF files and generate merged dataset

**Supported alignment combinations:**
- IMAGE ↔ IMAGE: crops target to aligned bounding box
- IMAGE → SPOT: stores aligned coordinates in AnnData `obsm`
- SPOT → SPOT: stores aligned coordinates in AnnData `obsm`
- SPOT → IMAGE: **not implemented** (prints warning)

**Issues identified:**
- Hyperspectral images are reduced to RGB via NMF — lossy and non-deterministic (random_state=None)
- `_load_ome_tiff` and `_load_anndata_coordinates` are duplicated from `registration.py`
- GUI runs on hardcoded port 8000 — no port conflict handling
- Threading model: alignment thread + GUI server thread + main thread — complex synchronization
- `uniform_aligned_dataset` method has a confusing name — it's a passthrough alignment
- `align_dataset` method is 125 lines mixing GUI orchestration with file I/O

### 6.2 `GUI/direct_mapping_alignment.py` — Flask GUI Server

- Serves static Vite-built frontend assets
- REST API for data exchange between alignment backend and browser UI
- Uses `threading.Event` for synchronization with the alignment thread
- Manual CORS headers (no flask-cors)

**Issues identified:**
- `enable_gui` blocks the calling thread — the Flask dev server is used in production
- `_disable_gui` has a hardcoded 2-second sleep
- No authentication or access control on the GUI
- No error handling if the port is already in use

---

## 7. Registration Module

### 7.1 `registration/registration.py`

**Class `FeatureExtractorRegistration`:**
- Loads a pre-trained model (Prov-GigaPath via HuggingFace) to extract patch embeddings from microscopy images
- Patch embeddings are extracted at coordinates provided by the alignment step
- Results stored as AnnData

**Class `SpotInterpolationRegistration`:**
- Inverse distance weighted interpolation of features from reference to target coordinate space
- Uses k-nearest neighbors with optional max distance cutoff
- Results stored as AnnData

Both classes share the same `register_dataset()` interface signature.

**Issues identified:**
- `from registration.microscopy_image import ...` — non-package-relative import (should be `from focus.registration...`)
- `_load_ome_tiff` and `_load_anndata_coordinates` are duplicated from `alignment.py`
- `register_dataset` in both classes has ~100 lines of identical boilerplate (file existence checks, directory creation, merging, normalization) — only the feature extraction differs
- `FeatureExtractorRegistration` requires HuggingFace login in `__init__` — side effect on import
- `SpotInterpolationRegistration._extract_features` iterates Python loops over spots — slow for large datasets

### 7.2 `registration/microscopy_image.py` — `MicroscopyImageFeatureExtractor`

- Uses `timm` + Prov-GigaPath model for patch-level feature extraction
- Extracts 224×224 patches (either grid-based or at specified coordinates)
- Filters out background patches
- Returns embeddings (N, 1536)

**Issues identified:**
- HuggingFace login called in `__init__` — requires token even for local cached models
- Hardcoded normalization constants (ImageNet mean/std)
- No support for modalities other than microscopy images (despite being in `registration/`)

---

## 8. Cross-Cutting Issues

### 8.1 API Inconsistency

The three stages have completely different calling conventions:

| Stage | Entry Point | Parameters | Return |
|-------|------------|------------|--------|
| Preprocessing | `preprocess_modality(path, modality_name, modality_type, settings)` | Flat function, settings dict | `dict[str, str]` |
| Alignment | `DirectMappingAligner(path, ref, tgt, ref_name, tgt_name, ref_type, tgt_type).align_dataset()` | Class constructor + method | `dict[str, str]` |
| Registration | `FeatureExtractorRegistration(path, hf_token).register_dataset(ref, tgt, ref_type, tgt_name)` | Class constructor + method | `dict[str, dict[str, str]]` |

There is no unified API. Each stage uses different parameter conventions, different constructor patterns, and different return types.

### 8.2 Duplicate Code

| Code | Duplicated In |
|------|--------------|
| OME-TIFF loading | `alignment.py`, `registration.py` |
| AnnData coordinate loading | `alignment.py`, `registration.py` |
| Background removal (Otsu + morphological) | `microscopy_image.py`, `raman.py` |
| Channel transpose logic | `microscopy_image._load_tiff`, `microscopy_image._load_czi`, `alignment._load_ome_tiff`, `registration._load_ome_tiff` |
| Dataset merging boilerplate | Both registration classes |
| Input validation patterns | Every class constructor |
| Cache-checking patterns | Every `process_*` / `register_*` method |

### 8.3 Import Inconsistencies

- `main.py` uses bare imports: `import utils`, `import preprocessing.preprocessing`
- `debug.py` uses package imports: `from focus.preprocessing import preprocessing`
- `registration/registration.py` uses mixed: `from registration.microscopy_image import ...`
- No `__init__.py` files exist in any subpackage

### 8.4 Missing Package Infrastructure

- No `__init__.py` files → not a proper Python package
- No public API surface definition (`__all__`)
- No type hints on most return values
- No abstract base classes for the Sample/Dataset pattern

### 8.5 Data Flow Design

Each modality follows the same pattern but implements it independently:

```
Sample class  →  loads raw data  →  processes single sample  →  saves file
Dataset class →  creates samples →  calls process on each    →  merges results
```

Yet there are no shared base classes. Each modality's Sample and Dataset classes have different method names, different constructor signatures, and different return types.

### 8.6 main.py vs debug.py Discrepancy

`main.py` and `debug.py` use completely different calling conventions:
- `main.py` uses `Aligner` (doesn't exist) and passes `sample_id` to preprocessing (parameter removed)
- `debug.py` uses `DirectMappingAligner` with explicit modality name/type params and no `sample_id`

`main.py` appears to be out of date relative to the actual codebase.

---

## 9. Summary of Key Refactoring Opportunities

1. **Unified public API**: Define a consistent interface for preprocessing, alignment, and registration that works identically regardless of modality
2. **Base classes**: Create `BaseSample` and `BaseDataset` abstract classes that enforce the Sample/Dataset pattern
3. **Eliminate duplication**: Extract shared utilities (image loading, AnnData loading, background removal, cache management, dataset merging)
4. **Proper Python packaging**: Add `__init__.py` files, define `__all__`, fix import paths
5. **Fix main.py**: Update CLI to use current API
6. **Decouple GUI**: The alignment GUI should be a standalone service, not embedded in the alignment logic
7. **Configuration**: Replace the custom `_AbstractEnum` pattern with Python `enum.Enum` or dataclasses
8. **Error handling**: Replace silent `print` + `continue` with proper logging and error propagation
9. **External tool management**: Abstract the conda subprocess calls (BaSiC, ASHLAR) behind a clean interface
10. **Reduce lipidomics.py complexity**: Split into multiple focused modules (parsing, calibration, background detection, interpolation)