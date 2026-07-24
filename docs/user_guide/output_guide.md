# Output Guide

This page describes every file and directory that FOCUS produces, explains the structure of the final multimodal dataset, and provides code examples for loading and inspecting outputs.

---

## Output Scenarios

The final output structure depends on your pipeline configuration:

### Scenario 1: Full Pipeline (Reference is Spot-Based + Registration Active)

**Conditions:**
- Reference modality is `msi` or `st` (spot-based)
- At least one non-reference modality has registration enabled

**Final Output:**
- **Primary:** `merged/multimodal_dataset.h5mu` — a single MuData file containing all registered modalities
- Per-sample files in `sample_*/preprocessing/`, `sample_*/alignment/`, and `sample_*/registration/`
- Merged stage files: `merged/preprocessing/`, `merged/alignment/`, `merged/registration/`

### Scenario 2: Alignment Only (Registration Inactive)

**Conditions:**
- All non-reference modalities have `registration_type: "none"`

**Final Output:**
- **Primary:** Merged aligned files in `merged/alignment/`
- If annotation transfer is enabled: `merged/annotations/`
- **No MuData file is created**
- Per-sample preprocessing and alignment files available

### Scenario 3: Image-Based Reference

**Conditions:**
- Reference modality is `microscopy_image` or `raman` (image-based)
- Currently, all non-reference modalities must also be image-based

**Final Output:**
- Per-sample cropped images in `sample_*/alignment/`
- For spot-based targets: spot coordinates expressed in reference frame
- Per-sample and merged preprocessed files available
- **No MuData file is created** (mixed image/spot references not yet supported)
- If spot-based targets are present: merged results available in `merged/alignment/`

---

## Complete Output Directory Structure (Full Pipeline Scenario)

FOCUS writes all outputs back into `dataset_path`. Nothing is written outside of this tree. The layout mirrors the pipeline stages:

```
<dataset_path>/
│
├── sample_001/
│   ├── preprocessing/
│   │   ├── microscopy/
│   │   │   └── microscopy_sample_001_processed.ome.tiff
│   │   ├── msi/
│   │   │   └── msi_sample_001_processed.h5ad        ← single file; dual ion mode is combined here, distinguished by .var['mz_mode']
│   │   └── st/
│   │       └── st_sample_001_processed.h5ad
│   │
│   ├── alignment/
│   │   └── st_sample_001_processed_aligned.h5ad    ← reference modality only
│   │
│   ├── registration/
│   │   ├── msi_sample_001_processed_aligned_registered.h5ad
│   │   └── microscopy_sample_001_processed_aligned_registered.h5ad
│   │
│   └── annotations/
│       └── st_sample_001_annotated.h5ad            ← if spatial_annotations enabled
│
├── sample_002/
│   └── ... (same structure as sample_001)
│
├── merged/
│   ├── preprocessing/
│   │   ├── msi_merged_processed.h5ad          ← omics modalities only; microscopy has no merged preprocessing output
│   │   └── st_merged_processed.h5ad
│   │
│   ├── alignment/
│   │   └── st_merged_processed_aligned.h5ad
│   │
│   ├── registration/
│   │   ├── msi_merged_processed_aligned_registered.h5ad
│   │   └── microscopy_merged_processed_aligned_registered.h5ad
│   │
│   ├── annotations/
│   │   └── st_merged_annotated.h5ad                ← if spatial_annotations enabled
│   │
│   └── multimodal_dataset.h5mu                     ← FINAL OUTPUT
│
└── focus.log
```

!!! note "Naming convention"
    Output file names follow a deterministic pattern based on modality name and sample ID:

    - Preprocessed: `<modality_name>_<sample_id>_processed.<ext>`
    - Aligned: `<modality_name>_<sample_id>_processed_aligned.<ext>`
    - Registered: `<modality_name>_<sample_id>_processed_aligned_registered.<ext>`
    - Merged (all samples combined): `<modality_name>_merged_processed[_aligned][_registered].<ext>`

---

## The Final Output: `multimodal_dataset.h5mu`

!!! note "Conditional Output"
    The file `merged/multimodal_dataset.h5mu` is only created when **both** conditions are met:
    - The reference modality is spot-based (`msi` or `st`)
    - `perform_registration` is `true`

    Additionally, at least two modalities must pass row-alignment validation during compilation, otherwise no file is written. See [Compilation](../pipeline/compilation.md).
    
    See [Output Scenarios](#output-scenarios) above to understand which outputs are created for your pipeline configuration.

The file `merged/multimodal_dataset.h5mu` is the primary output of the FOCUS pipeline when conditions are met. It is a [MuData](https://mudata.readthedocs.io/) HDF5 file that holds all registered modalities in a single container, ready for downstream analysis with scanpy, squidy, or any AnnData-compatible tool.

### Loading and Inspecting

```python
import mudata as md

mdata = md.read_h5mu("merged/multimodal_dataset.h5mu")
print(mdata)
# MuData object with n_obs × n_vars = ... × ...
#   2 modalities
#   obs:  'sample_id', 'spatial_annotation'
#   var:  ...
#   obsm: 'spatial'
#   mod:
#     'st'          AnnData (n_spots × n_genes)
#     'msi'         AnnData (n_pixels × n_features)

# Access individual modalities
st = mdata.mod["st"]       # AnnData for spatial transcriptomics
msi = mdata.mod["msi"]    # AnnData for MSI
```

### Contents of the MuData

Spatial coordinates and spot size are stored **once at the top level** of the MuData, not on each modality:

| Slot | Content |
|------|---------|
| `mdata.obsm['spatial']` | (n_obs × 2) `float32` array of spot coordinates in the reference modality's coordinate space |
| `mdata.obs['sample_id']` | Sample identifier per spot, shared across all modalities |
| `mdata.obs['spatial_annotation']` | Region labels, present only when annotation transfer is enabled |
| `mdata.uns['spot_size']` | Physical spot size in µm, a length-2 `float32` array copied from the reference modality. Present only if the reference had it. |

Each per-modality AnnData (`mdata.mod['<modality>']`) contains:

| Slot | Content |
|------|---------|
| `.X` | Feature matrix (expression counts, ion intensities, embeddings, etc.) |
| `.obs` | Spot/pixel metadata, including `sample_id` |
| `.var` | Feature metadata, with **namespaced** names of the form `{modality}:{name}` (e.g. `st:CD3E`, `msi:0`) |

!!! note "Feature names are namespaced"
    During compilation every feature name is rewritten to `{modality}:{name}` to keep names collision-free across modalities. Query features with the prefixed name (e.g. `st:CD3E`), not the bare one. See [Compilation](../pipeline/compilation.md) for details.

!!! note "Spot count may be smaller than the reference grid"
    Compilation drops reference spots that are uncovered (all-zero features) in any modality, so the MuData's `n_obs` can be lower than the reference spot count. See [Compilation](../pipeline/compilation.md).

### Example: Spatial Plotting with squidpy

```python
import squidpy as sq

# Spatial scatter plot coloured by a gene (feature names are namespaced)
sq.pl.spatial_scatter(
    mdata.mod["st"],
    color="st:CD3E",
    spot_size=mdata.uns["spot_size"][0],
)
```

---

## Per-File Format Reference

| File pattern | Format | How to open |
|---|---|---|
| `*_processed.ome.tiff` | Multi-resolution OME-TIFF | `tifffile`, napari, QuPath, FIJI/ImageJ |
| `*_processed.h5ad` | AnnData HDF5 | `anndata.read_h5ad()` |
| `*_processed_aligned.h5ad` | AnnData HDF5 | `anndata.read_h5ad()` |
| `*_processed_aligned_registered.h5ad` | AnnData HDF5 | `anndata.read_h5ad()` |
| `*_annotated.h5ad` | AnnData HDF5 | `anndata.read_h5ad()` |
| `multimodal_dataset.h5mu` | MuData HDF5 | `mudata.read_h5mu()` |

### Opening OME-TIFF files

```python
import tifffile

with tifffile.TiffFile("microscopy_sample_001_processed.ome.tiff") as tif:
    # Full-resolution level (level 0)
    image = tif.asarray()
    print(image.shape)   # RGB: (H, W, 3); single/multi-channel: (H, W) or (C, H, W)

    # FOCUS stores each pyramid level as a separate series (level 0 = full resolution)
    n_levels = len(tif.series)
    half_res = tif.series[1].asarray() if n_levels > 1 else None
```

### Opening AnnData files at any stage

```python
import anndata as ad

# Load a preprocessed spatial transcriptomics file
adata = ad.read_h5ad("preprocessing/st/st_sample_001_processed.h5ad")
print(adata)

# Load a registered MSI file
adata_msi = ad.read_h5ad(
    "registration/msi_sample_001_processed_aligned_registered.h5ad"
)
```

---

## Intermediate Files

FOCUS keeps every intermediate file it produces (preprocessed, aligned, registered). This behaviour is intentional:

- **Resumable runs**: If a run is interrupted or fails partway through, you can restart with the same config and FOCUS will skip any stage whose output file already exists, as long as `force_recomputing` is `false` in the processing or registration settings.
- **Stage inspection**: You can examine the output of any individual stage (e.g., inspect an aligned AnnData before registration) without re-running the full pipeline.

!!! warning "Disk space"
    Keeping all intermediate files is space-intensive, particularly for high-resolution microscopy images stored as multi-resolution OME-TIFF pyramids. Microscopy pyramids are stored in the source file's original dtype (not upsampled to float32), so plan for roughly the raw data size per sample, plus a fraction more for the added pyramid levels. Use `force_recomputing: true` only when you intentionally want to regenerate outputs from scratch.

---

## The Merged Directory

After processing all individual samples, FOCUS concatenates the omics modalities into a single per-modality file in `merged/` (image modalities such as microscopy are merged only at the registration stage, not during preprocessing):

- Spot/pixel coordinates are preserved in each sample's original reference space and tagged with a `sample_id` observation column.
- The final `multimodal_dataset.h5mu` is built from the merged registered files.
- If any modality has `registration_type: "none"`, it is excluded from the MuData but its preprocessed and aligned files are still written.

---

## Log File

`focus.log` is written at the root of `dataset_path`. It contains a full timestamped record of every pipeline step, all warnings, and any errors. The file handler always writes at DEBUG level, so even in normal (non-`--debug`) runs, the log file captures verbose detail useful for diagnosing problems.

```
2024-03-15 09:12:34 [INFO] focus: Config loaded and validated: /data/cohort/focus_config.json
2024-03-15 09:12:34 [INFO] focus: Starting preprocessing for modality 'st', sample 'sample_001'
2024-03-15 09:12:41 [DEBUG] focus: st_sample_001_processed.h5ad written (2.3 MB)
...
```

Pass `--debug` on the command line to also see DEBUG-level messages in the console output (they are always written to the log file regardless).
