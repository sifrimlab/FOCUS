# Configuration File Structure

## Overview

FOCUS uses a JSON configuration file to define the entire pipeline execution. The configuration file specifies dataset locations, modality definitions, processing parameters, and pipeline options.

## Basic Structure

```json
{
  "dataset_path": "string",
  "reference_modality": "string",
  "perform_alignment": boolean,
  "perform_registration": boolean,
  "huggingface_token": "string (optional)",
  "logging_level": "string (optional)",
  "max_cpu_cores": integer (optional),
  "modalities": [
    {
      "name": "string",
      "type": "string",
      "processing_settings": {},
      "alignment_strategy": "string",
      "registration_type": "string",
      "registration_settings": {} (optional)
    }
  ],
  "spatial_annotations": {} (optional)
}
```

## Top-Level Configuration Fields

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `dataset_path` | string | Absolute path to dataset directory | `"/data/my_project"` |
| `reference_modality` | string | Name of the reference modality | `"microscopy"` |
| `perform_alignment` | boolean | Enable/disable alignment stage | `true` |
| `perform_registration` | boolean | Enable/disable registration stage | `false` |
| `modalities` | array | List of modality configurations | See below |

### Optional Fields

| Field | Type | Description | Default | Example |
|-------|------|-------------|---------|---------|
| `huggingface_token` | string | Token for HuggingFace model access | `null` | `"hf_xxxxxx"` |
| `logging_level` | string | Logging verbosity level | `"INFO"` | `"DEBUG"` |
| `max_cpu_cores` | integer | Maximum CPU cores to use | Auto-detect | `8` |
| `temp_dir` | string | Temporary directory path | System temp | `"/tmp/focus"` |
| `cache_dir` | string | Cache directory path | `~/.focus/cache` | `"/data/cache"` |
| `force_recomputing` | boolean | Force recompute all stages | `false` | `true` |
| `spatial_annotations` | object | Spatial annotation transfer settings | `null` | See below |

**Spatial Annotations Configuration:**

When `spatial_annotations` is enabled, FOCUS transfers spatial annotations from GeoJSON files to the reference modality. This requires:

1. **Annotation Files**: One GeoJSON file per sample in the specified modality directory
2. **Consistency**: All samples must have annotation files if this feature is enabled
3. **File Format**: GeoJSON format with polygon features
4. **Coordinate System**: Must match the reference modality's coordinate system

```json
{
  "spatial_annotations": {
    "modality_name": "microscopy",
    "annotation_type": "geojson",
    "force_recomputing": false
  }
}
```

**Fields:**
- `modality_name`: Modality containing annotation files (must match a defined modality name)
- `annotation_type`: Currently only "geojson" is supported
- `force_recomputing`: Force recomputation of annotation transfer

**File Requirements:**
- Annotation files must be named `<sample_id>.geojson` (recommended)
- Files must be placed in `<dataset_path>/<sample_id>/<modality_name>/`
- All samples must have annotation files if this feature is enabled

## Modality Configuration

Each modality in the `modalities` array has the following structure:

### Required Modality Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `name` | string | Modality name (must match directory) | `"msi"` |
| `type` | string | Modality type key | `"microscopy_image"` |
| `processing_settings` | object | Modality-specific processing params | `{}` |
| `alignment_strategy` | string | Alignment approach | `"manual"` |
| `registration_type` | string | Registration method | `"none"` |

### Optional Modality Fields

| Field | Type | Description | Default | Example |
|-------|------|-------------|---------|---------|
| `registration_settings` | object | Registration-specific parameters | `{}` | See below |

### Supported Modality Types

| Type Key | Description | Directory Name Example |
|----------|-------------|------------------------|
| `microscopy_image` | Fluorescence/brightfield microscopy | `microscopy` |
| `msi` | Mass spectrometry imaging | `msi` |
| `raman` | Raman spectroscopy imaging | `raman` |
| `st` | Spatial transcriptomics | `st` |

## Modality-Specific Processing Settings

### Microscopy Image Processing Settings

```json
{
  "color_enhancement": boolean,
  "background_removal": boolean,
  "crop_to_tissue": boolean,
  "resolution_level": integer,
  "gamma": number,
  "contrast_stretch": number,
  "background_threshold": number,
  "tissue_margin": integer,
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Range |
|-------|------|-------------|---------|-------|
| `color_enhancement` | boolean | Enable gamma correction and contrast stretching | `true` | `true`/`false` |
| `background_removal` | boolean | Enable background removal | `true` | `true`/`false` |
| `crop_to_tissue` | boolean | Crop to tissue bounding box | `true` | `true`/`false` |
| `resolution_level` | integer | OME-TIFF pyramid level to use | `0` | `0-5` |
| `gamma` | number | Gamma correction value | `1.0` | `0.1-3.0` |
| `contrast_stretch` | number | Contrast stretch percentage | `1.0` | `0.1-5.0` |
| `background_threshold` | number | Background detection threshold | `0.1` | `0.0-1.0` |
| `tissue_margin` | integer | Margin around tissue (pixels) | `250` | `0-1000` |
| `force_recomputing` | boolean | Force recompute even if cached | `false` | `true`/`false` |

### MSI Processing Settings

```json
{
  "ion_mode": "string",
  "mass_range": [number, number],
  "intensity_normalization": "string",
  "background_detection": boolean,
  "recalibration": boolean,
  "lipid_annotation": boolean,
  "min_intensity": number,
  "max_intensity": number,
  "ppm_tolerance": number,
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Options/Range |
|-------|------|-------------|---------|----------------|
| `ion_mode` | string | Ionization mode | `"positive"` | `"positive"`, `"negative"`, `"both"` |
| `mass_range` | array | m/z range to process | `[100, 1000]` | `[min, max]` |
| `intensity_normalization` | string | Normalization method | `"tic"` | `"tic"`, `"max"`, `"none"` |
| `background_detection` | boolean | Enable background detection | `true` | `true`/`false` |
| `recalibration` | boolean | Enable m/z recalibration | `true` | `true`/`false` |
| `lipid_annotation` | boolean | Enable lipid annotation | `true` | `true`/`false` |
| `min_intensity` | number | Minimum intensity threshold | `0.0` | `≥0` |
| `max_intensity` | number | Maximum intensity threshold | `1e6` | `>min_intensity` |
| `ppm_tolerance` | number | Mass accuracy tolerance (ppm) | `10.0` | `1-50` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

### Raman Processing Settings

```json
{
  "wavenumber_range": [number, number],
  "basic_correction": boolean,
  "background_removal": boolean,
  "ashlar_stitching": boolean,
  "despike": boolean,
  "denoise": boolean,
  "baseline_correction": boolean,
  "normalization": boolean,
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Range/Options |
|-------|------|-------------|---------|----------------|
| `wavenumber_range` | array | Wavenumber range (cm⁻¹) | `[400, 1800]` | `[min, max]` |
| `basic_correction` | boolean | Enable BaSiC correction | `true` | `true`/`false` |
| `background_removal` | boolean | Enable background removal | `true` | `true`/`false` |
| `ashlar_stitching` | boolean | Enable ASHLAR stitching | `true` | `true`/`false` |
| `despike` | boolean | Enable despiking | `true` | `true`/`false` |
| `denoise` | boolean | Enable denoising | `true` | `true`/`false` |
| `baseline_correction` | boolean | Enable baseline correction | `true` | `true`/`false` |
| `normalization` | boolean | Enable normalization | `true` | `true`/`false` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

### Spatial Transcriptomics Processing Settings

```json
{
  "qc_mito_threshold": number,
  "min_genes_per_spot": integer,
  "max_genes_per_spot": integer,
  "min_cells_per_gene": integer,
  "normalization": "string",
  "log_transform": boolean,
  "n_hvgs": integer,
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Range |
|-------|------|-------------|---------|-------|
| `qc_mito_threshold` | number | Mitochondrial gene percentage threshold | `0.2` | `0.0-1.0` |
| `min_genes_per_spot` | integer | Minimum genes per spot | `200` | `50-10000` |
| `max_genes_per_spot` | integer | Maximum genes per spot | `5000` | `>min_genes` |
| `min_cells_per_gene` | integer | Minimum cells per gene | `3` | `1-100` |
| `normalization` | string | Normalization method | `"total_counts"` | `"total_counts"`, `"none"` |
| `log_transform` | boolean | Apply log1p transform | `true` | `true`/`false` |
| `n_hvgs` | integer | Number of highly variable genes | `2000` | `100-10000` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

## Alignment Configuration

### Alignment Strategy Options

| Strategy | Description | When to Use |
|----------|-------------|--------------|
| `"manual"` | Interactive GUI alignment | Default for most cases |
| `"pre_aligned"` | Assume modalities are pre-aligned | Trusted pre-aligned data |
| `"uniform"` | Uniform scaling without GUI | Simple scaling cases |

### Alignment Configuration Example

```json
{
  "name": "msi",
  "type": "msi",
  "alignment_strategy": "manual",
  "processing_settings": {}
}
```

## Registration Configuration

### Registration Type Options

| Type | Description | Requirements | GPU |
|------|-------------|--------------|-----|
| `"none"` | No registration | - | ❌ |
| `"feature_extraction"` | Deep learning patch embeddings | Microscopy images | ✅ |
| `"spot_interpolation"` | Gaussian-weighted interpolation | Spot-based modalities | ❌ |

### Feature Extraction Registration Settings

```json
{
  "patch_size": integer,
  "background_color": "string",
  "min_max_rescale": boolean,
  "gpu_device": integer,
  "batch_size": integer,
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Range/Options |
|-------|------|-------------|---------|----------------|
| `patch_size` | integer | Size of image patches | `224` | `64-512` |
| `background_color` | string | Background color handling | `"white"` | `"white"`, `"black"` |
| `min_max_rescale` | boolean | Normalize patch intensities | `true` | `true`/`false` |
| `gpu_device` | integer | GPU device ID | `0` | `0, 1, 2,...` |
| `batch_size` | integer | Batch size for processing | `32` | `1-128` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

### Spot Interpolation Registration Settings

```json
{
  "k_neighbors": integer,
  "max_distance": number,
  "weighting": "string",
  "force_recomputing": boolean
}
```

| Field | Type | Description | Default | Range/Options |
|-------|------|-------------|---------|----------------|
| `k_neighbors` | integer | Number of nearest neighbors | `5` | `1-20` |
| `max_distance` | number | Maximum interpolation distance | `100.0` | `>0` |
| `weighting` | string | Distance weighting function | `"distance"` | `"distance"`, `"uniform"` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

## Spatial Annotations Configuration

```json
{
  "spatial_annotations": {
    "modality_name": "string",
    "annotation_type": "string",
    "force_recomputing": boolean
  }
}
```

| Field | Type | Description | Default | Options |
|-------|------|-------------|---------|---------|
| `modality_name` | string | Modality containing annotations | Required | Modality name |
| `annotation_type` | string | Annotation file format | `"geojson"` | `"geojson"` |
| `force_recomputing` | boolean | Force recompute | `false` | `true`/`false` |

## Complete Configuration Examples

### Basic Configuration (Preprocessing Only)

```json
{
  "dataset_path": "/data/my_project",
  "reference_modality": "msi",
  "perform_alignment": false,
  "alignment_force_recomputing": false,
  "perform_registration": false,
  "huggingface_token": null,
  "spatial_annotations": null,
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "msi",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "min_intensity_threshold": 10000,
        "detect_background": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "msi"
    },
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": true,
        "crop_to_tissue": true,
        "gamma": 0.45,
        "pyramid_levels": 4,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "microscopy_image"
    }
  ]
}
```

### Full Pipeline Configuration

```json
{
  "dataset_path": "/data/complex_project",
  "reference_modality": "st",
  "perform_alignment": true,
  "alignment_force_recomputing": false,
  "perform_registration": true,
  "huggingface_token": "hf_xxxxxx",
  "spatial_annotations": {
    "file_type": "geojson",
    "modality_name": "microscopy"
  },
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "st",
      "processing_settings": {
        "min_count_per_spot": 200,
        "max_count_per_spot": null,
        "min_genes_per_spot": 250,
        "max_genes_per_spot": 6000,
        "min_spots_per_gene": null,
        "min_count_spots_ratio_per_gene": null,
        "total_counts_normalize": true,
        "log1p_transform": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "st"
    },
    {
      "alignment_strategy": "manual",
      "name": "msi",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "min_intensity_threshold": 10000,
        "detect_background": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "spot_interpolation",
      "type": "msi"
    },
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": true,
        "crop_to_tissue": true,
        "gamma": 0.45,
        "pyramid_levels": 4,
        "force_recomputing": false
      },
      "registration_settings": {
        "patch_size": 224,
        "min_max_rescale": true,
        "force_recomputing": false
      },
      "registration_type": "feature_extraction",
      "type": "microscopy_image"
    }
  ]
}
```

## Configuration Validation

FOCUS validates configuration files automatically, but you can validate manually:

```python
import json
from focus.utils import parse_config

# Load configuration
with open('focus_config.json', 'r') as f:
    config = json.load(f)

# Validate
try:
    validated_config = parse_config(config)
    print("✅ Configuration is valid!")
except Exception as e:
    print(f"❌ Configuration error: {e}")
```

### Common Validation Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `dataset_path not found` | Invalid path | Check path exists and permissions |
| `reference_modality not found` | Modality not in list | Add modality or correct name |
| `invalid modality type` | Unknown type key | Use supported type keys |
| `missing required field` | Field omitted | Add required field |
| `invalid field value` | Value out of range | Use valid value range |

## Configuration Best Practices

### Organization

1. **Consistent Naming**: Use clear, consistent modality names
2. **Directory Matching**: Ensure `name` fields match directory names exactly
3. **Logical Grouping**: Group related settings together
4. **Comments**: Use comments in JSON (if your editor supports it)

### Parameter Selection

1. **Start Simple**: Begin with default parameters
2. **Test Incrementally**: Change one parameter at a time
3. **Document Changes**: Keep notes on parameter rationale
4. **Validate Results**: Check outputs after parameter changes

### Performance Optimization

1. **Batch Sizes**: Adjust based on available memory
2. **CPU Cores**: Match to your system capabilities
3. **Resolution Levels**: Use appropriate pyramid levels
4. **Caching**: Enable caching for repeated runs

### Reproducibility

1. **Version Control**: Track configuration files with git
2. **Backup Configs**: Keep backups of working configurations
3. **Document Versions**: Note which configuration produced which results
4. **Environment Consistency**: Use same FOCUS version for reproducibility

## Configuration Templates

Create reusable templates for common workflows:

### Template: Microscopy + MSI

```json
{
  "dataset_path": "REPLACE_ME",
  "reference_modality": "msi",
  "perform_alignment": true,
  "alignment_force_recomputing": false,
  "perform_registration": true,
  "huggingface_token": "REPLACE_ME",
  "spatial_annotations": null,
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "msi",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "min_intensity_threshold": 10000,
        "detect_background": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "msi"
    },
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": true,
        "crop_to_tissue": true,
        "gamma": 0.45,
        "pyramid_levels": 4,
        "force_recomputing": false
      },
      "registration_settings": {
        "patch_size": 224,
        "min_max_rescale": true,
        "force_recomputing": false
      },
      "registration_type": "feature_extraction",
      "type": "microscopy_image"
    }
  ]
}
```

### Template: Preprocessing Only

```json
{
  "dataset_path": "REPLACE_ME",
  "reference_modality": "msi",
  "perform_alignment": false,
  "alignment_force_recomputing": false,
  "perform_registration": false,
  "huggingface_token": null,
  "spatial_annotations": null,
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "msi",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "min_intensity_threshold": 10000,
        "detect_background": true,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "msi"
    },
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": false,
        "crop_to_tissue": false,
        "gamma": 0.45,
        "pyramid_levels": 4,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "microscopy_image"
    }
  ]
}
```

## Configuration Management Tools

### JSON Schema Validation

Use JSON schema validation for better error detection:

```bash
# Install ajv-cli
npm install -g ajv-cli

# Validate against schema
ajv validate -s focus_schema.json -d focus_config.json
```

### Configuration Diffing

Compare configuration versions:

```bash
# Using jq
diff <(jq --sort-keys . config_v1.json) <(jq --sort-keys . config_v2.json)

# Using Python
python -c "
import json
with open('config_v1.json') as f1, open('config_v2.json') as f2:
    c1 = json.load(f1)
    c2 = json.load(f2)
    
print('Differences:')
for key in set(c1.keys()) | set(c2.keys()):
    if c1.get(key) != c2.get(key):
        print(f'{key}: {c1.get(key)} -> {c2.get(key)}')
"
```

### Configuration Merging

Merge multiple configuration files:

```python
import json

# Load base config
with open('base_config.json') as f:
    base = json.load(f)

# Load override config
with open('override_config.json') as f:
    override = json.load(f)

# Merge (override takes precedence)
merged = {**base, **override}

# Save merged config
with open('merged_config.json', 'w') as f:
    json.dump(merged, f, indent=2)
```

## Troubleshooting Configuration Issues

### Common Problems

**Issue: Configuration not loading**
- **Cause**: Invalid JSON syntax
- **Solution**: Validate with `python -m json.tool config.json`

**Issue: Pipeline stages not running**
- **Cause**: `perform_alignment` or `perform_registration` set to `false`
- **Solution**: Set to `true` to enable stages

**Issue: Modality not processed**
- **Cause**: Directory name doesn't match `name` field
- **Solution**: Ensure exact match (case-sensitive)

**Issue: Registration failing**
- **Cause**: Missing HuggingFace token for feature extraction
- **Solution**: Add `huggingface_token` field

### Debugging Techniques

**Enable debug logging:**
```json
{
  "logging_level": "DEBUG"
}
```

**Check parsed configuration:**
```python
from focus.utils import parse_config
import json

with open('focus_config.json') as f:
    config = json.load(f)

parsed = parse_config(config)
print(json.dumps(parsed, indent=2))
```

**Validate directory structure:**
```bash
# Check dataset structure
tree /data/my_project -L 3
```

## Configuration File Location

FOCUS looks for configuration files in these locations (in order):

1. **Explicit path**: `--config /path/to/config.json`
2. **Current directory**: `./focus_config.json`
3. **Dataset directory**: `<dataset_path>/focus_config.json`
4. **Home directory**: `~/.focus/config.json`

## Next Steps

Now that you understand configuration:

1. **Create Your Config**: Start with a template and customize
2. **Validate**: Ensure your configuration is valid
3. **Test**: Run with a small dataset first
4. **Iterate**: Refine parameters based on results
5. **Document**: Keep notes on your configuration choices

## Additional Resources

- [Quick Start Guide](../quick_start/gui_usage.md) - Create configs interactively
- [CLI Usage Guide](../quick_start/cli_usage.md) - Run with configuration files
- [Pipeline Documentation](../pipeline/preprocessing.md) - Understand processing stages
- [Troubleshooting Guide](../troubleshooting.md) - Common issues and solutions