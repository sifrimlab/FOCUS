# Configuration Field Reference

## Top-Level Fields

### `dataset_path`

**Type**: string
**Required**: ✅ Yes
**Description**: Absolute path to the root directory containing your dataset samples.

**Format**: Must be an absolute path (starting with `/` on Unix, `C:\) on Windows)

**Example**:
```json
"dataset_path": "/data/my_spatial_omics_project"
```

**Validation**: 
- Path must exist and be readable
- Must be a directory, not a file
- User must have read/write permissions

**Best Practices**:
- Use absolute paths to avoid issues
- Ensure sufficient disk space (FOCUS creates intermediate files)
- Verify path is accessible from all compute nodes (for HPC)

**Troubleshooting**:
- **Error**: "Dataset path not found"
- **Solution**: Check path exists and permissions: `ls /data/my_project`

---

### `reference_modality`

**Type**: string
**Required**: ✅ Yes
**Description**: Name of the modality that serves as the spatial reference coordinate system.

**Format**: Must match exactly (case-sensitive) with one of the modality `name` fields

**Example**:
```json
"reference_modality": "microscopy"
```

**Considerations**:
- All other modalities will be aligned to this reference
- Typically the highest-resolution modality (e.g., microscopy)
- Must be a valid modality name defined in the `modalities` array

**Best Practices**:
- Choose modality with clear anatomical features
- Use consistent naming across projects
- Document why this modality was chosen as reference

**Troubleshooting**:
- **Error**: "Reference modality not found"
- **Solution**: Ensure name matches exactly with a modality definition

---

### `perform_alignment`

**Type**: boolean
**Required**: ✅ Yes
**Description**: Enable or disable the alignment stage of the pipeline.

**Options**:
- `true`: Run alignment stage (default for multi-modality projects)
- `false`: Skip alignment stage

**Example**:
```json
"perform_alignment": true
```

**When to Use**:
- **`true`**: When modalities need spatial registration
- **`false`**: When modalities are pre-aligned or only doing preprocessing

**Dependencies**:
- If `true`, requires manual interaction for "manual" alignment strategy
- Alignment files are required for registration stage

**Best Practices**:
- Set to `false` for preprocessing-only runs
- Set to `true` when integrating multiple modalities
- Consider computational cost for large datasets

---

### `perform_registration`

**Type**: boolean
**Required**: ✅ Yes
**Description**: Enable or disable the registration stage of the pipeline.

**Options**:
- `true`: Run registration stage
- `false`: Skip registration stage (default)

**Example**:
```json
"perform_registration": false
```

**When to Use**:
- **`true`**: When feature mapping between modalities is needed
- **`false`**: When only spatial alignment is sufficient

**Dependencies**:
- Requires alignment stage to be completed first
- Some registration types require GPU (feature_extraction)

**Best Practices**:
- Registration significantly increases processing time
- Feature extraction requires HuggingFace token and GPU
- Spot interpolation is CPU-only but faster

**Troubleshooting**:
- **Error**: "Alignment files not found"
- **Solution**: Ensure `perform_alignment` is `true` and completed

---

### `huggingface_token`

**Type**: string
**Required**: ❌ No (Optional)
**Description**: Authentication token for accessing HuggingFace model hub.

**Format**: String starting with "hf_"

**Example**:
```json
"huggingface_token": "hf_xxxxxx"
```

**When Required**:
- Only needed for `registration_type: "feature_extraction"`
- Token is cached locally after first use

**Security Considerations**:
- Token provides access to HuggingFace services
- Remove from shared configuration files
- Use environment variables for better security

**Best Practices**:
- Store token securely
- Use read-only tokens when possible
- Remove from version control

**Environment Variable Alternative**:
```bash
export HUGGINGFACE_TOKEN="hf_xxxxxx"
focus --config /path/to/config.json
```

---

### `logging_level`

**Type**: string
**Required**: ❌ No
**Default**: `"INFO"`
**Description**: Controls the verbosity of logging output.

**Options**:
- `"DEBUG"`: Maximum detail (for troubleshooting)
- `"INFO"`: Standard operational messages (default)
- `"WARNING"`: Only warnings and errors
- `"ERROR"`: Only errors

**Example**:
```json
"logging_level": "DEBUG"
```

**When to Use**:
- **DEBUG**: Development, troubleshooting
- **INFO**: Normal operation
- **WARNING**: Production (reduce log volume)
- **ERROR**: Critical systems only

**Performance Impact**:
- DEBUG level can significantly increase log file size
- May impact performance for very high-throughput runs

**Best Practices**:
- Use DEBUG for initial testing
- Switch to INFO for production runs
- Monitor log files during long runs

---

### `max_cpu_cores`

**Type**: integer
**Required**: ❌ No
**Default**: Auto-detected
**Description**: Maximum number of CPU cores to use for parallel processing.

**Range**: 1 to available cores

**Example**:
```json
"max_cpu_cores": 8
```

**Considerations**:
- Affects preprocessing and registration stages
- Higher values = faster processing but more resource usage
- May be limited by system or container constraints

**Best Practices**:
- Start with half of available cores
- Monitor system load during processing
- Adjust based on other system usage

**Environment Variable Alternative**:
```bash
export FOCUS_THREADS=8
focus --config /path/to/config.json
```

---

### `temp_dir`

**Type**: string
**Required**: ❌ No
**Default**: System temporary directory
**Description**: Directory for temporary files during processing.

**Example**:
```json
"temp_dir": "/scratch/focus_temp"
```

**Considerations**:
- Should have sufficient free space
- SSD recommended for better performance
- Files are cleaned up after pipeline completion

**Best Practices**:
- Use fast storage (SSD/NVMe)
- Ensure sufficient space for large datasets
- Monitor disk usage during processing

---

### `cache_dir`

**Type**: string
**Required**: ❌ No
**Default**: `~/.focus/cache`
**Description**: Directory for caching intermediate results.

**Example**:
```json
"cache_dir": "/data/focus_cache"
```

**Caching Behavior**:
- Intermediate files are stored for reuse
- Significantly speeds up repeated runs
- Cache invalidated when source files change

**Cache Control**:
- Use `force_recomputing: true` to bypass cache
- Clear cache manually: `rm -rf ~/.focus/cache/*`

**Best Practices**:
- Use persistent storage for cache
- Monitor cache size over time
- Clear cache when switching projects

---

### `force_recomputing`

**Type**: boolean
**Required**: ❌ No
**Default**: `false`
**Description**: Force recomputation of all stages, bypassing cache.

**Example**:
```json
"force_recomputing": true
```

**When to Use**:
- After changing source data
- When debugging pipeline issues
- To ensure fresh computation

**Performance Impact**:
- Significantly increases processing time
- Uses more CPU/RAM resources
- Generates fresh intermediate files

**Best Practices**:
- Use sparingly (only when necessary)
- Combine with specific modality forcing for targeted recomputation
- Monitor resource usage when forced recomputation is active

---

### `spatial_annotations`

**Type**: object
**Required**: ❌ No
**Description**: Configuration for spatial annotation transfer.

**Structure**:
```json
{
  "spatial_annotations": {
    "modality_name": "string",
    "annotation_type": "string",
    "force_recomputing": boolean
  }
}
```

**Fields**:
- `modality_name`: Modality containing annotation files
- `annotation_type`: Format of annotation files (currently only "geojson")
- `force_recomputing`: Force recomputation of annotation transfer

**Example**:
```json
"spatial_annotations": {
  "modality_name": "microscopy",
  "annotation_type": "geojson",
  "force_recomputing": false
}
```

**File Requirements**:
- Annotation files must be in GeoJSON format
- Files should be named `<sample_id>.geojson`
- Located in modality directory: `<dataset_path>/<sample_id>/<modality_name>/`

**Best Practices**:
- Validate GeoJSON files before processing
- Ensure coordinate systems match reference modality
- Review transferred annotations in final output

---

## Modality Fields

### `name`

**Type**: string
**Required**: ✅ Yes
**Description**: Unique identifier for this modality.

**Constraints**:
- Must match directory name exactly (case-sensitive)
- Must be unique across modalities
- No spaces or special characters recommended

**Example**:
```json
"name": "msi"
```

**Directory Structure**:
```
dataset_path/
└── sample_001/
    └── msi/		# Must match "name" field
        ├── data.imzML
        └── data.ibd
```

**Best Practices**:
- Use short, descriptive names
- Avoid changing names mid-project
- Document naming conventions

**Troubleshooting**:
- **Error**: "Modality directory not found"
- **Solution**: Verify directory name matches exactly

---

### `type`

**Type**: string
**Required**: ✅ Yes
**Description**: Modality type key determining processing pipeline.

**Supported Values**:
- `"microscopy_image"`: Fluorescence/brightfield microscopy
- `"msi"`: Mass spectrometry imaging
- `"raman"`: Raman spectroscopy
- `"st"`: Spatial transcriptomics

**Example**:
```json
"type": "microscopy_image"
```

**Type-Specific Requirements**:
- **microscopy_image**: TIFF/CZI files
- **msi**: imzML + ibd files
- **raman**: LIF files
- **st**: AnnData (.h5ad) files

**Best Practices**:
- Verify input files match type requirements
- Check file extensions are correct
- Test with small subset first

---

### `processing_settings`

**Type**: object
**Required**: ✅ Yes
**Description**: Modality-specific processing parameters.

**Structure**: Varies by modality type (see modality-specific sections below)

**Example**:
```json
"processing_settings": {
  "color_enhancement": true,
  "background_removal": true
}
```

**Best Practices**:
- Start with default values
- Adjust one parameter at a time
- Document parameter rationale
- Validate outputs after changes

---

### `alignment_strategy`

**Type**: string
**Required**: ✅ Yes
**Description**: Approach for aligning this modality to the reference.

**Options**:
- `"manual"`: Interactive GUI alignment (default)
- `"pre_aligned"`: Assume already aligned
- `"uniform"`: Uniform scaling without GUI

**Example**:
```json
"alignment_strategy": "manual"
```

**Strategy Details**:

**"manual"**:
- Requires user interaction via GUI
- Most accurate for complex alignments
- Time-consuming for many samples

**"pre_aligned"**:
- Skips alignment stage
- Assumes data is already registered
- Fastest option
- No quality checks performed

**"uniform"**:
- Applies uniform scaling
- No GUI interaction needed
- Less accurate than manual
- Faster than manual

**Best Practices**:
- Use "manual" for critical projects
- Use "pre_aligned" for trusted pre-processed data
- Document alignment approach used

---

### `registration_type`

**Type**: string
**Required**: ✅ Yes
**Description**: Method for registering features between modalities.

**Options**:
- `"none"`: No registration (default)
- `"feature_extraction"`: Deep learning patch embeddings
- `"spot_interpolation"`: Gaussian-weighted interpolation

**Example**:
```json
"registration_type": "spot_interpolation"
```

**Registration Details**:

**"none"**:
- Skips registration stage
- Only aligned coordinates available
- Fastest option
- No feature mapping between modalities

**"feature_extraction"**:
- Uses Prov-GigaPath model via HuggingFace
- Requires GPU and HuggingFace token
- Highest accuracy for image-based registration
- Most computationally intensive
- Produces patch-level feature embeddings

**"spot_interpolation"**:
- CPU-only implementation
- Gaussian-weighted feature interpolation
- Good balance of accuracy and performance
- Works with spot-based modalities
- Configurable via `registration_settings`

**Best Practices**:
- Use "none" for preprocessing-only workflows
- Use "feature_extraction" when GPU available
- Use "spot_interpolation" for CPU-only environments
- Consider computational cost vs. benefit

**Troubleshooting**:
- **Error**: "GPU not available"
- **Solution**: Use "spot_interpolation" or install CUDA drivers

---

### `registration_settings`

**Type**: object
**Required**: ❌ No (Required if registration_type ≠ "none")
**Description**: Parameters specific to the registration method.

**Structure**: Varies by registration type

**Example (feature_extraction)**:
```json
"registration_settings": {
  "patch_size": 224,
  "background_color": "white",
  "min_max_rescale": true,
  "batch_size": 16
}
```

**Example (spot_interpolation)**:
```json
"registration_settings": {
  "k_neighbors": 5,
  "max_distance": 100.0,
  "weighting": "distance"
}
```

**Best Practices**:
- Start with default values
- Adjust based on dataset characteristics
- Monitor memory usage with larger values
- Validate registration quality in outputs

---

## Modality-Specific Processing Settings

### Microscopy Image Settings

#### `color_enhancement`

**Type**: boolean
**Default**: `true`
**Description**: Enable gamma correction and contrast stretching.

**Effect**:
- Improves visual quality of images
- Enhances tissue features for alignment
- May affect quantitative analysis

**Best Practices**:
- Enable for visualization and alignment
- Disable for quantitative image analysis
- Test with/without for your use case

---

#### `background_removal`

**Type**: boolean
**Default**: `true`
**Description**: Remove background from microscopy images.

**Method**: Otsu thresholding + morphological operations

**Effect**:
- Reduces file size
- Improves tissue contrast
- May remove faint signals

**Best Practices**:
- Enable for most tissue images
- Disable if background contains important information
- Adjust threshold if needed

---

#### `crop_to_tissue`

**Type**: boolean
**Default**: `true`
**Description**: Crop image to tissue bounding box.

**Effect**:
- Reduces file size significantly
- Removes empty space
- Speeds up processing

**Parameters**:
- `tissue_margin`: Additional pixels around tissue (default: 250)

**Best Practices**:
- Enable for most cases
- Disable if non-tissue regions are important
- Adjust margin for edge cases

---

#### `resolution_level`

**Type**: integer
**Default**: `0`
**Range**: `0-5`
**Description**: OME-TIFF pyramid level to use for processing.

**Levels**:
- `0`: Full resolution
- `1`: 1/2 resolution
- `2`: 1/4 resolution
- etc.

**Effect**:
- Higher levels = faster processing, lower quality
- Level 0 = best quality, slowest

**Best Practices**:
- Use level 0 for final results
- Use higher levels for testing/previews
- Balance quality vs. performance

---

#### `gamma`

**Type**: number
**Default**: `1.0`
**Range**: `0.1-3.0`
**Description**: Gamma correction value for image enhancement.

**Effect**:
- `gamma < 1.0`: Darkens image
- `gamma > 1.0`: Brightens image
- `gamma = 1.0`: No correction

**Best Practices**:
- Start with default (1.0)
- Adjust based on visual inspection
- Values 0.8-1.2 most common

---

#### `contrast_stretch`

**Type**: number
**Default**: `1.0`
**Range**: `0.1-5.0`
**Description**: Contrast stretching percentage.

**Effect**:
- Enhances contrast between light/dark areas
- Values >1.0 increase contrast
- Values <1.0 decrease contrast

**Best Practices**:
- Start with default (1.0)
- Increase for low-contrast images
- Avoid extreme values (>2.0)

---

#### `background_threshold`

**Type**: number
**Default**: `0.1`
**Range**: `0.0-1.0`
**Description**: Threshold for background detection.

**Method**: Otsu thresholding with this sensitivity

**Effect**:
- Lower values = more aggressive background removal
- Higher values = more conservative

**Best Practices**:
- Default works for most cases
- Adjust if too much/much tissue removed
- Test with visual inspection

---

#### `tissue_margin`

**Type**: integer
**Default**: `250`
**Range**: `0-1000`
**Description**: Additional pixels to include around detected tissue.

**Unit**: Pixels at current resolution level

**Effect**:
- Prevents cropping too close to tissue edge
- Higher values = larger output files

**Best Practices**:
- 250 pixels (~250µm at 1µm/px) good default
- Increase for irregular tissue shapes
- Decrease to minimize file size

---

### MSI Processing Settings

#### `ion_mode`

**Type**: string
**Default**: `"positive"`
**Options**: `"positive"`, `"negative"`, `"both"`
**Description**: Ionization mode of the MSI data.

**Effect**:
- Determines which m/z ranges to process
- "both" processes positive and negative modes separately

**Best Practices**:
- Match acquisition parameters
- "both" doubles processing time
- Validate ion mode in raw data

---

#### `mass_range`

**Type**: array
**Default**: `[100, 1000]`
**Format**: `[min_mz, max_mz]`
**Description**: m/z range to process.

**Considerations**:
- Narrower ranges = faster processing
- Wider ranges = more comprehensive
- Must cover features of interest

**Best Practices**:
- Start with broad range [50, 1200]
- Narrow based on expected features
- Consider instrument limitations

---

#### `intensity_normalization`

**Type**: string
**Default**: `"tic"`
**Options**: `"tic"`, `"max"`, `"none"`
**Description**: Method for normalizing intensity values.

**Methods**:
- **TIC**: Total Ion Current normalization
- **Max**: Normalize to maximum intensity
- **None**: No normalization

**Effect**:
- TIC: Good for comparative analysis
- Max: Preserves relative intensities
- None: Raw intensities (may vary between samples)

**Best Practices**:
- Use TIC for most analyses
- Use Max if TIC introduces artifacts
- None for specialized applications

---

#### `background_detection`

**Type**: boolean
**Default**: `true`
**Description**: Enable background spot detection and filtering.

**Method**: Gaussian Mixture Model (GMM) classification

**Effect**:
- Removes low-quality spectra
- Improves downstream analysis
- May remove valid low-intensity features

**Best Practices**:
- Enable for most datasets
- Disable if background contains important signals
- Review filtered spots in outputs

---

#### `recalibration`

**Type**: boolean
**Default**: `true`
**Description**: Enable m/z recalibration using reference peaks.

**Method**: Uses known lipid peaks for mass accuracy correction

**Effect**:
- Improves mass accuracy
- Better lipid annotation
- May fail with poor reference peaks

**Best Practices**:
- Enable for most datasets
- Disable if reference peaks unavailable
- Check recalibration quality in logs

---

#### `lipid_annotation`

**Type**: boolean
**Default**: `true`
**Description**: Enable lipid database annotation.

**Database**: Uses internal lipid database for matching

**Effect**:
- Adds lipid class information to features
- Enables lipid-specific analysis
- Increases processing time

**Best Practices**:
- Enable for lipidomics studies
- Disable for untargeted metabolomics
- Review annotation confidence scores

---

#### `min_intensity`

**Type**: number
**Default**: `0.0`
**Range**: `≥0`
**Description**: Minimum intensity threshold for spectra.

**Effect**:
- Filters out low-intensity peaks
- Reduces noise
- May remove weak signals

**Best Practices**:
- Start with default (0.0)
- Increase for noisy data
- Balance signal retention vs. noise reduction

---

#### `max_intensity`

**Type**: number
**Default**: `1e6`
**Range**: `>min_intensity`
**Description**: Maximum intensity threshold (saturation limit).

**Effect**:
- Clips saturated peaks
- Prevents single peaks dominating
- May affect quantitative accuracy

**Best Practices**:
- Set based on instrument saturation
- Default (1e6) works for most instruments
- Adjust if saturation artifacts observed

---

#### `ppm_tolerance`

**Type**: number
**Default**: `10.0`
**Range**: `1-50`
**Description**: Mass accuracy tolerance for annotation (parts per million).

**Effect**:
- Lower values = more stringent matching
- Higher values = more matches, potential false positives

**Best Practices**:
- 10 ppm good for most instruments
- 5 ppm for high-accuracy instruments
- 20 ppm for lower-accuracy instruments

---

### Raman Processing Settings

#### `wavenumber_range`

**Type**: array
**Default**: `[400, 1800]`
**Format**: `[min_cm⁻¹, max_cm⁻¹]`
**Description**: Wavenumber range to process.

**Considerations**:
- Typical biological range: 400-1800 cm⁻¹
- Narrower ranges = faster processing
- Must cover features of interest

**Best Practices**:
- Start with default range
- Narrow based on expected features
- Consider instrument specifications

---

#### `basic_correction`

**Type**: boolean
**Default**: `true`
**Description**: Enable BaSiC correction for background and shading.

**Method**: Uses external FOCUS_BaSiCpy conda environment

**Effect**:
- Corrects background fluorescence
- Removes shading artifacts
- Improves spectral quality

**Best Practices**:
- Enable for most Raman data
- Disable if artifacts introduced
- Requires BaSiC conda environment

---

#### `background_removal`

**Type**: boolean
**Default**: `true`
**Description**: Enable background removal.

**Method**: Otsu thresholding + morphological operations

**Effect**:
- Removes non-tissue background
- Reduces file size
- May remove faint signals

**Best Practices**:
- Enable for tissue sections
- Disable for pure samples
- Similar to microscopy background removal

---

#### `ashlar_stitching`

**Type**: boolean
**Default**: `true`
**Description**: Enable ASHLAR stitching for tiled images.

**Method**: Uses external FOCUS_ASHLAR conda environment

**Effect**:
- Combines tiled acquisitions
- Corrects stitching artifacts
- Essential for large-area Raman

**Best Practices**:
- Enable for tiled acquisitions
- Disable for single-tile data
- Requires ASHLAR conda environment

---

#### `despike`

**Type**: boolean
**Default**: `true`
**Description**: Enable cosmic ray spike removal.

**Method**: Median filtering-based despiking

**Effect**:
- Removes cosmic ray artifacts
- Improves spectral quality
- May affect sharp peaks

**Best Practices**:
- Enable for most Raman data
- Disable if over-aggressive
- Check despiking in sample spectra

---

#### `denoise`

**Type**: boolean
**Default**: `true`
**Description**: Enable spectral denoising.

**Method**: Wavelet-based denoising

**Effect**:
- Reduces noise
- Preserves spectral features
- May smooth sharp peaks

**Best Practices**:
- Enable for noisy data
- Disable if spectral features blurred
- Balance noise reduction vs. feature preservation

---

#### `baseline_correction`

**Type**: boolean
**Default**: `true`
**Description**: Enable baseline correction.

**Method**: Polynomial baseline subtraction

**Effect**:
- Removes baseline drift
- Improves peak detection
- May affect low-frequency features

**Best Practices**:
- Enable for most Raman spectra
- Disable if baseline contains information
- Check baseline correction quality

---

#### `normalization`

**Type**: boolean
**Default**: `true`
**Description**: Enable spectral normalization.

**Method**: Area normalization

**Effect**:
- Normalizes total spectral intensity
- Enables comparative analysis
- May obscure concentration differences

**Best Practices**:
- Enable for comparative studies
- Disable for quantitative analysis
- Consider normalization method

---

### Spatial Transcriptomics Processing Settings

#### `qc_mito_threshold`

**Type**: number
**Default**: `0.2`
**Range**: `0.0-1.0`
**Description**: Maximum mitochondrial gene percentage for quality control.

**Effect**:
- Filters low-quality spots
- Typical threshold: 5-20%
- Higher thresholds = more spots retained

**Best Practices**:
- 0.2 (20%) good starting point
- Adjust based on tissue type
- Review QC metrics in outputs

---

#### `min_genes_per_spot`

**Type**: integer
**Default**: `200`
**Range**: `50-10000`
**Description**: Minimum number of genes detected per spot.

**Effect**:
- Filters low-complexity spots
- Higher values = more stringent filtering
- Tissue-dependent optimal values

**Best Practices**:
- 200 good for most tissues
- Lower for sparse tissues
- Higher for dense tissues

---

#### `max_genes_per_spot`

**Type**: integer
**Default**: `5000`
**Range**: `>min_genes`
**Description**: Maximum number of genes detected per spot.

**Effect**:
- Filters potential doublets/multiplets
- Higher values = more spots retained
- Rarely needs adjustment

**Best Practices**:
- Default (5000) works for most cases
- Increase for complex tissues
- Review distribution in QC plots

---

#### `min_cells_per_gene`

**Type**: integer
**Default**: `3`
**Range**: `1-100`
**Description**: Minimum number of cells expressing a gene.

**Effect**:
- Filters rarely expressed genes
- Higher values = more stringent
- Affects downstream analysis

**Best Practices**:
- 3 good default
- Increase for more conservative filtering
- Consider biological relevance

---

#### `normalization`

**Type**: string
**Default**: `"total_counts"`
**Options**: `"total_counts"`, `"none"`
**Description**: Normalization method for gene expression.

**Methods**:
- **total_counts**: Normalize by total counts per spot
- **none**: No normalization

**Effect**:
- total_counts: Enables comparative analysis
- none: Preserves absolute counts

**Best Practices**:
- Use total_counts for most analyses
- Use none for specialized applications
- Consider downstream analysis requirements

---

#### `log_transform`

**Type**: boolean
**Default**: `true`
**Description**: Apply log1p transformation to normalized counts.

**Effect**:
- Stabilizes variance
- Makes data more normally distributed
- Required for many statistical tests

**Best Practices**:
- Enable for most analyses
- Disable for specific algorithms requiring counts
- Standard for single-cell/ST analysis

---

#### `n_hvgs`

**Type**: integer
**Default**: `2000`
**Range**: `100-10000`
**Description**: Number of highly variable genes to select.

**Effect**:
- Focuses on most informative genes
- Reduces dimensionality
- Improves downstream analysis

**Best Practices**:
- 2000 good for most datasets
- Adjust based on dataset size
- Consider analysis requirements

---

## Registration Settings Fields

### Feature Extraction Settings

#### `patch_size`

**Type**: integer
**Default**: `224`
**Range**: `64-512`
**Description**: Size of image patches for feature extraction.

**Considerations**:
- Must be compatible with model architecture
- 224x224 standard for Prov-GigaPath
- Larger patches = more context, slower processing

**Best Practices**:
- Use default (224) for standard model
- Ensure divisible by model requirements
- Consider memory constraints

---

#### `background_color`

**Type**: string
**Default**: `"white"`
**Options**: `"white"`, `"black"`
**Description**: Background color handling for patches.

**Effect**:
- Affects patch normalization
- Match to your image background
- White: Assumes white background
- Black: Assumes black background

**Best Practices**:
- Match to actual image background
- White for most microscopy
- Black for some specialized stains

---

#### `min_max_rescale`

**Type**: boolean
**Default**: `true`
**Description**: Normalize patch intensities to [0,1] range.

**Effect**:
- Standardizes input to model
- Improves feature extraction quality
- May affect quantitative interpretation

**Best Practices**:
- Enable for most cases
- Disable for specialized applications
- Standard for deep learning models

---

#### `gpu_device`

**Type**: integer
**Default**: `0`
**Range**: `0, 1, 2,...`
**Description**: GPU device ID to use.

**Considerations**:
- 0 = first GPU
- Use `nvidia-smi` to list available GPUs
- Multi-GPU systems only

**Best Practices**:
- Use default (0) for single GPU
- Specify for multi-GPU systems
- Monitor GPU utilization

---

#### `batch_size`

**Type**: integer
**Default**: `32`
**Range**: `1-128`
**Description**: Number of patches to process simultaneously.

**Memory Impact**:
- Higher batch size = faster processing
- Higher batch size = more GPU memory
- Adjust based on GPU memory

**Best Practices**:
- Start with 32
- Increase until memory limit
- Typical range: 16-64

---

### Spot Interpolation Settings

#### `k_neighbors`

**Type**: integer
**Default**: `5`
**Range**: `1-20`
**Description**: Number of nearest neighbors for interpolation.

**Effect**:
- Higher k = smoother interpolation
- Lower k = more local variation
- Affects registration accuracy

**Best Practices**:
- 5 good default
- Increase for smoother results
- Decrease for more local accuracy

---

#### `max_distance`

**Type**: number
**Default**: `100.0`
**Range**: `>0`
**Description**: Maximum distance for neighbor search.

**Unit**: Spatial units (typically micrometers)

**Effect**:
- Limits search radius
- Higher values = more neighbors found
- Lower values = more local interpolation

**Best Practices**:
- Set based on spot density
- 100 µm good for 10x Visium
- Adjust for other technologies

---

#### `weighting`

**Type**: string
**Default**: `"distance"`
**Options**: `"distance"`, `"uniform"`
**Description**: Weighting function for interpolation.

**Methods**:
- **distance**: Weight by inverse distance
- **uniform**: Equal weighting

**Effect**:
- distance: Closer points have more influence
- uniform: All points contribute equally

**Best Practices**:
- Use distance for most cases
- Use uniform for specific applications
- distance generally more accurate

---

## Configuration Examples by Use Case

### Use Case 1: Preprocessing Only

**Scenario**: Prepare data for external analysis tools

```json
{
  "dataset_path": "/data/preprocessing_project",
  "reference_modality": "microscopy",
  "perform_alignment": false,
  "perform_registration": false,
  "modalities": [
    {
      "name": "microscopy",
      "type": "microscopy_image",
      "processing_settings": {
        "color_enhancement": true,
        "background_removal": true,
        "crop_to_tissue": true
      },
      "alignment_strategy": "manual",
      "registration_type": "none"
    },
    {
      "name": "msi",
      "type": "msi",
      "processing_settings": {
        "ion_mode": "positive",
        "mass_range": [100, 1000]
      },
      "alignment_strategy": "manual",
      "registration_type": "none"
    }
  ]
}
```

**Key Points**:
- Both pipeline stages disabled
- Basic preprocessing settings
- No registration needed

---

### Use Case 2: Multi-Modal Integration

**Scenario**: Integrate microscopy and MSI for comprehensive analysis

```json
{
  "dataset_path": "/data/multi_modal_project",
  "reference_modality": "microscopy",
  "perform_alignment": true,
  "perform_registration": true,
  "huggingface_token": "hf_xxxxxx",
  "modalities": [
    {
      "name": "microscopy",
      "type": "microscopy_image",
      "processing_settings": {
        "color_enhancement": true,
        "background_removal": true,
        "crop_to_tissue": true
      },
      "alignment_strategy": "manual",
      "registration_type": "feature_extraction",
      "registration_settings": {
        "patch_size": 224,
        "batch_size": 16
      }
    },
    {
      "name": "msi",
      "type": "msi",
      "processing_settings": {
        "ion_mode": "positive",
        "mass_range": [50, 1200],
        "background_detection": true
      },
      "alignment_strategy": "manual",
      "registration_type": "spot_interpolation",
      "registration_settings": {
        "k_neighbors": 5,
        "max_distance": 75.0
      }
    }
  ]
}
```

**Key Points**:
- Full pipeline enabled
- Feature extraction for microscopy (GPU)
- Spot interpolation for MSI (CPU)
- Comprehensive processing settings

---

### Use Case 3: High-Throughput Screening

**Scenario**: Process many samples quickly with minimal interaction

```json
{
  "dataset_path": "/data/high_throughput",
  "reference_modality": "microscopy",
  "perform_alignment": true,
  "perform_registration": false,
  "max_cpu_cores": 16,
  "modalities": [
    {
      "name": "microscopy",
      "type": "microscopy_image",
      "processing_settings": {
        "color_enhancement": true,
        "background_removal": true,
        "crop_to_tissue": true,
        "resolution_level": 1
      },
      "alignment_strategy": "uniform",
      "registration_type": "none"
    },
    {
      "name": "st",
      "type": "st",
      "processing_settings": {
        "qc_mito_threshold": 0.2,
        "min_genes_per_spot": 200,
        "normalization": "total_counts"
      },
      "alignment_strategy": "pre_aligned",
      "registration_type": "none"
    }
  ]
}
```

**Key Points**:
- Uniform alignment (no GUI)
- Pre-aligned ST data
- Higher resolution level for speed
- Maximum CPU cores

---

### Use Case 4: Raman + MSI Integration

**Scenario**: Combine Raman spectroscopy and MSI for metabolomics

```json
{
  "dataset_path": "/data/raman_msi_project",
  "reference_modality": "raman",
  "perform_alignment": true,
  "perform_registration": true,
  "modalities": [
    {
      "name": "raman",
      "type": "raman",
      "processing_settings": {
        "wavenumber_range": [400, 1800],
        "basic_correction": true,
        "background_removal": true,
        "ashlar_stitching": true
      },
      "alignment_strategy": "manual",
      "registration_type": "none"
    },
    {
      "name": "msi",
      "type": "msi",
      "processing_settings": {
        "ion_mode": "both",
        "mass_range": [50, 1000],
        "lipid_annotation": true
      },
      "alignment_strategy": "manual",
      "registration_type": "spot_interpolation"
    }
  ]
}
```

**Key Points**:
- Raman as reference modality
- Both ion modes for MSI
- Comprehensive Raman processing
- Spot interpolation for MSI registration

---

## Configuration Validation Checklist

**Before Running Pipeline:**

1. ✅ **Dataset Path**: Absolute path exists and is accessible
2. ✅ **Reference Modality**: Name matches a defined modality
3. ✅ **Directory Structure**: Matches configuration exactly
4. ✅ **File Formats**: Input files match modality requirements
5. ✅ **JSON Syntax**: Valid JSON (use `python -m json.tool`)
6. ✅ **Required Fields**: All required fields present
7. ✅ **Value Ranges**: All values within valid ranges
8. ✅ **Dependencies**: Required tools/environments available
9. ✅ **Permissions**: Read/write access to all directories
10. ✅ **Disk Space**: Sufficient space for intermediate files

**For GPU Registration:**
11. ✅ **HuggingFace Token**: Present if using feature_extraction
12. ✅ **CUDA Drivers**: Installed and working
13. ✅ **GPU Memory**: Sufficient for batch size

**For Raman Processing:**
14. ✅ **BaSiC Environment**: FOCUS_BaSiCpy conda environment
15. ✅ **ASHLAR Environment**: FOCUS_ASHLAR conda environment

## Next Steps

Now that you understand all configuration fields:

1. **Create Your Configuration**: Start with a template
2. **Validate**: Use the validation checklist
3. **Test**: Run with a small subset
4. **Iterate**: Refine based on results
5. **Document**: Keep notes on your configuration

## Additional Resources

- [Configuration Structure](config_structure.md) - Overall structure
- [Quick Start Guide](../quick_start/gui_usage.md) - Create configs interactively
- [CLI Usage Guide](../quick_start/cli_usage.md) - Run configurations
- [Troubleshooting Guide](../troubleshooting.md) - Common issues