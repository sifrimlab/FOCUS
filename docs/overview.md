# FOCUS System Overview

## Problem statement

Spatial multiomics experiments capture complementary biological information from the same tissue section using multiple instruments: fluorescence microscopy reveals morphology and cellular organization, mass spectrometry imaging (MSI) resolves the tissue lipidome, Raman spectroscopy imaging provides spatially resolved, label-free biochemical maps, and spatial transcriptomics quantifies gene expression with spatial resolution. Each modality is acquired on a different instrument, producing data in a different coordinate system, at a different spatial resolution, and in a different file format. Jointly analyzing these complementary layers requires co-registering them into a shared spatial coordinate frame — a process that is technically demanding and not yet standardized.

FOCUS addresses this challenge by providing an end-to-end, configuration-driven pipeline that takes raw multi-modal data from a single tissue section and produces a single, analysis-ready MuData object.

---

## The four-stage pipeline

```
Raw Data → [1] Preprocessing → [2] Alignment → [3] Registration → [4] Compilation → MuData (.h5mu)
```

### Stage 1 — Preprocessing

Preprocessing converts raw instrument output into a standardized, quality-controlled representation. Each modality is handled by a dedicated processor that applies domain-appropriate algorithms: background subtraction and pyramid tiling for microscopy images, m/z consensus alignment and intensity normalization for MSI, BaSiC illumination correction and ASHLAR tile stitching for Raman, and leiden clustering with mitochondrial-gene filtering for spatial transcriptomics. The output of this stage is either an OME-TIFF pyramid (for image modalities) or an AnnData `.h5ad` file (for omics modalities), stored under `{sample_id}/preprocessing/{modality_name}/`.

### Stage 2 — Alignment

Alignment establishes the spatial correspondence between each non-reference modality and the reference modality. This is performed interactively through a web-based alignment GUI that launches automatically during pipeline execution. The user visually overlays the reference modality onto the target modality using translation, rotation, and scale transformations (via drag controls), until the two are spatially aligned. FOCUS records this transformation as additional coordinate keys in the reference modality AnnData (`.obsm['{non_ref_name}_spatial']`), enabling the registration stage to compute which target spots/pixels correspond to each reference spot. A single alignment GUI session presents all samples for all non-reference modalities in sequence; the main pipeline pauses while alignment is in progress and automatically resumes once the user closes the alignment tab. For modalities that are already co-registered, a `pre_aligned` strategy skips the GUI entirely.

### Stage 3 — Registration

Registration uses the alignment transforms computed in Stage 2 to map the feature content of each modality onto the reference coordinate system. Two strategies are available, each suited to a different modality type. **Feature extraction** is used for microscopy images: patches centered at each reference spot location are extracted from the OME-TIFF and encoded into dense embedding vectors by a pretrained vision model (Prov-GigaPath, 1536-dimensional). **Spot interpolation** is used for sparse omics modalities (MSI, ST) and currently for Raman: reference spot coordinates are expressed in the target modality's coordinate space (via the alignment transform), and a Gaussian-weighted average of the nearest target spots/pixels is computed, yielding an interpolated feature vector at each reference location. Both strategies produce a per-modality AnnData file with `.obsm['spatial']` aligned to the reference frame.

### Stage 4 — Compilation (conditional)

**MuData assembly occurs only when both conditions are met:**
- **Reference modality is spot-based** (MSI or ST)
- **Registration stage is active** (i.e., at least one modality has `registration_type` other than `"none"`)

If compilation runs, it reads all per-modality registered AnnData files and assembles them into a single MuData object (`.h5mu`) stored at `{dataset_path}/merged/multimodal_dataset.h5mu`. Observation indices are harmonized across modalities so that row $i$ in every modality AnnData corresponds to the same reference spot. If spatial annotations (GeoJSON regions) are provided, region labels are transferred to `.obs['spatial_annotation']` at this stage.

**If registration is inactive (all modalities have `registration_type: "none"`),** the pipeline stops after alignment and annotation transfer (if enabled). Outputs are stored in `merged/aligned/` or `merged/annotation/` respectively.

**If the reference modality is image-based,** FOCUS currently does not support compiling mixed image and spot modalities. All non-reference modalities must also be image-based. Per-sample cropped images are produced (one per target), but no merged dataset is created.

---

## Supported modalities

| Modality type key | Input format | Output format | Registration method |
|-------------------|-------------|--------------|---------------------|
| `microscopy_image` | `.tiff` / `.tif` / `.czi` | OME-TIFF pyramid | `feature_extraction` (GPU) |
| `msi` | `.imzML` + `.ibd` | AnnData `.h5ad` | `spot_interpolation` (CPU) |
| `raman` | `.lif` | OME-TIFF hyperspectral | `spot_interpolation` (CPU) |
| `st` | AnnData `.h5ad` | AnnData `.h5ad` | `spot_interpolation` (CPU) or `none` |

!!! note "GPU requirement"
    Feature extraction registration for `microscopy_image` requires an NVIDIA GPU with CUDA. All other stages, including spot interpolation for Raman, MSI, and ST, run on CPU.

---

## Reference modality concept

One modality in the pipeline is designated as the **reference modality** via the `reference_modality` field in the configuration. The reference modality provides the canonical spatial coordinate frame: its spot locations (or a regular grid derived from its pixel coordinates) serve as the anchor points to which all other modalities are mapped. The reference is typically chosen as the modality with the lowest spatial resolution (coarser spot grid), since higher-resolution modalities can be reliably aggregated down to match it, but reliable upscaling is not possible. When multiple modalities have similar resolution, the one with the clearest visible tissue structure is preferred, as it makes the visual overlay during alignment easier — most commonly the microscopy image. All alignment transforms, registration outputs, and final MuData coordinates are expressed in the reference modality's coordinate system (micrometers).

---

## Directory structure convention

FOCUS expects input data and writes intermediate and final outputs according to a fixed directory layout rooted at `dataset_path`:

```
<dataset_path>/
├── <sample_id_1>/
│   ├── <modality_name>/               # Raw input files
│   │   └── ...
│   ├── preprocessing/
│   │   └── <modality_name>/           # Preprocessed OME-TIFF or .h5ad
│   ├── alignment/                     # Aligned reference .h5ad per sample
│   └── registration/                  # Registered .h5ad per modality per sample
├── <sample_id_2>/
│   └── ...
└── merged/
    ├── preprocessing/                 # Merged preprocessing outputs
    ├── alignment/                     # Merged alignment outputs
    ├── registration/                  # Merged registration outputs
    └── multimodal_dataset.h5mu        # Final MuData output
```

!!! warning "Consistent modality names"
    Modality directory names must **exactly match** (case-sensitive) the `"name"` field in the configuration. Every sample directory must contain every modality defined in the configuration.

### Input file requirements by modality

| Modality | Required files | Sub-directory |
|----------|---------------|---------------|
| `microscopy_image` | One `.tiff`, `.tif`, or `.czi` file | directly in `<modality_name>/` |
| `msi` (positive mode only) | `data.imzML` + `data.ibd` | `<modality_name>/pos/` |
| `msi` (dual mode) | 2 × (`.imzML` + `.ibd`) | `<modality_name>/pos/` and `<modality_name>/neg/` |
| `raman` | One `.lif` file | directly in `<modality_name>/` |
| `st` | One `.h5ad` file | directly in `<modality_name>/` |

---

## Output structure

The final output is a single MuData file:

```
<dataset_path>/merged/multimodal_dataset.h5mu
```

This file contains one AnnData per modality (`.mod['{modality_name}']`), shared observation indices aligned to the reference coordinate frame, and shared `.obs['sample_id']` and `.obs['spatial_annotation']` columns. See [Data Schemas](api/data_types.md) for the complete attribute reference.
