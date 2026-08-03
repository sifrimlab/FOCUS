# FOCUS System Overview

## Problem statement

Spatial multiomics experiments capture complementary biological information from the same tissue section using multiple instruments: fluorescence microscopy reveals morphology and cellular organization, mass spectrometry imaging (MSI) resolves the tissue lipidome, Raman spectroscopy imaging provides spatially resolved, label-free biochemical maps, and spatial transcriptomics quantifies gene expression with spatial resolution. Each modality is acquired on a different instrument, producing data in a different coordinate system, at a different spatial resolution, and in a different file format. Jointly analyzing these complementary layers requires co-registering them into a shared spatial coordinate frame. That process is technically demanding and not yet standardized.

FOCUS provides an end-to-end, configuration-driven pipeline that takes raw multi-modal data from a single tissue section and produces a single, analysis-ready MuData object.

---

## The four-stage pipeline

```
Raw Data → [1] Preprocessing → [2] Alignment → [3] Registration → [4] Compilation → MuData (.h5mu)
```

### Stage 1: Preprocessing

Preprocessing converts raw instrument output into a standardized, quality-controlled representation. Each modality is handled by a dedicated processor that applies domain-appropriate algorithms: colour enhancement, Otsu background removal, tissue cropping and pyramid construction for microscopy images, m/z consensus alignment and intensity normalization for MSI, BaSiC illumination correction and ASHLAR tile stitching for Raman, and mitochondrial flagging with QC metrics, optional count/gene filtering and optional normalisation for spatial transcriptomics. The output of this stage is either an OME-TIFF pyramid (for image modalities) or an AnnData `.h5ad` file (for omics modalities), stored under `{sample_id}/preprocessing/{modality_name}/`.

### Stage 2: Alignment

Alignment establishes the spatial correspondence between each non-reference modality and the reference modality. This is performed interactively through a web-based alignment GUI that launches automatically during pipeline execution. The user visually overlays the reference modality onto the fixed target modality using translation, rotation, scaling, flip, and free per-corner distortion (via drag controls), until the two are spatially aligned. FOCUS applies the resulting 3×3 projective transform to every reference spot and records the mapped positions as additional coordinate keys in the reference modality AnnData (`.obsm['{non_ref_name}_spatial']`), which the registration stage uses to find the target spots/pixels corresponding to each reference spot. One GUI session runs per non-reference modality and steps through that modality's samples; the main pipeline pauses while a session is open and resumes when its last sample is confirmed. For modalities that are already co-registered, a `pre_aligned` strategy skips the GUI entirely.

### Stage 3: Registration

Registration uses the alignment transforms computed in Stage 2 to map the feature content of each modality onto the reference coordinate system. Four strategies are available, each tied to a modality type. **Feature extraction** (`microscopy_image`): patches centered at each reference spot location are extracted from the OME-TIFF and encoded into dense embedding vectors by a pretrained vision model (Prov-GigaPath, 1536-dimensional). The model is pretrained on H&E-stained brightfield tiles, so this mode applies to H&E histology only; other microscopy (fluorescence, IHC, other stains) should use `none`. **Spot interpolation** (`msi`, `st`): reference spot coordinates are expressed in the target modality's coordinate space (via the alignment transform), and a Gaussian-weighted average of the target spots within each reference spot's footprint yields an interpolated feature vector at each reference location. **Spot aggregation** (`msi`, `st`): the same footprint, with the target spots inside it summed at equal weight and left unnormalized, for subcellular-resolution data such as Visium HD. **Raman pixel interpolation** (`raman`): the same Gaussian footprint interpolation applied to the pixels of the hyperspectral OME-TIFF. All four produce a per-modality AnnData file whose rows correspond one-to-one with the reference spots.

### Stage 4: Compilation (conditional)

**MuData assembly occurs only when both conditions are met:**
- **Reference modality is spot-based** (MSI or ST)
- **Registration stage is active** (i.e., `perform_registration` is `true`)

If compilation runs, it reads all per-modality registered AnnData files and assembles them into a single MuData object (`.h5mu`) stored at `{dataset_path}/merged/multimodal_dataset.h5mu`. Observation indices are harmonized across modalities so that row $i$ in every modality AnnData corresponds to the same reference spot; reference spots left uncovered (all-zero features) in any modality are dropped from all of them. If spatial annotations were transferred, compilation **promotes** the existing `.obs['spatial_annotation']` label to the top level of the MuData object. See [Compilation](pipeline/compilation.md) for the full procedure.

**If registration is inactive (all modalities have `registration_type: "none"`),** the pipeline stops after alignment and annotation transfer (if enabled). Outputs are stored in `merged/alignment/` or `merged/annotations/` respectively.

!!! note "Annotation transfer is a separate stage"
    When `spatial_annotations` is configured, region labels are transferred in a dedicated stage that runs **after alignment** (so the pipeline reports five stages, not four). That stage writes `.obs['spatial_annotation']` onto the reference modality file and runs even when registration and compilation are inactive. It is **not** performed during compilation. Compilation, when it runs, only promotes the already-written label. See [Spatial Annotation Transfer](scientific/annotation_transfer.md) for details.

**If the reference modality is image-based,** FOCUS currently does not support compiling mixed image and spot modalities. All non-reference modalities must also be image-based. Per-sample cropped images are produced (one per target), but no merged dataset is created.

---

## Supported modalities

| Modality type key | Input format | Output format | Registration method |
|-------------------|-------------|--------------|---------------------|
| `microscopy_image` | `.tiff` / `.tif` / `.ome.tiff` / `.ome.tif` / `.qptiff` / `.czi` | OME-TIFF pyramid | `feature_extraction` (GPU, H&E brightfield only) or `none` |
| `msi` | `.imzML` + `.ibd` | AnnData `.h5ad` | `spot_interpolation` or `spot_aggregation` (CPU) |
| `raman` | `.lif` | OME-TIFF hyperspectral | `raman_pixel_interpolation` (CPU) |
| `st` | AnnData `.h5ad` | AnnData `.h5ad` | `spot_interpolation` or `spot_aggregation` (CPU), or `none` |

!!! note "GPU requirement"
    Feature extraction registration for `microscopy_image` requires an NVIDIA GPU with CUDA. All other stages, including `spot_interpolation` (MSI, ST) and `raman_pixel_interpolation` (Raman) registration, run on CPU.

!!! warning "Feature extraction is for H&E histology"
    Its encoder (Prov-GigaPath) is pretrained on H&E-stained brightfield tiles. A microscopy modality that is not an H&E brightfield RGB section should use `"registration_type": "none"`: FOCUS never checks the stain, so it would otherwise produce embeddings that look valid but describe nothing.

---

## Reference modality concept

One modality in the pipeline is designated as the **reference modality** via the `reference_modality` field in the configuration. The reference modality provides the canonical spatial coordinate frame: its spot locations (or a regular grid derived from its pixel coordinates) serve as the anchor points to which all other modalities are mapped. The reference is typically chosen as the modality with the lowest spatial resolution (coarser spot grid), since higher-resolution modalities can be reliably aggregated down to match it, but reliable upscaling is not possible. When multiple modalities have similar resolution, the one with the clearest visible tissue structure is preferred, most commonly the microscopy image, because it makes the visual overlay during alignment easier. All alignment transforms, registration outputs, and final MuData coordinates are expressed in the reference modality's coordinate system (micrometers).

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
│   ├── registration/                  # Registered .h5ad per modality per sample
│   └── annotations/                   # Annotated reference .h5ad per sample (if spatial_annotations enabled)
├── <sample_id_2>/
│   └── ...
└── merged/
    ├── preprocessing/                 # Merged preprocessing outputs
    ├── alignment/                     # Merged alignment outputs
    ├── registration/                  # Merged registration outputs
    ├── annotations/                   # Merged annotated reference output (if spatial_annotations enabled)
    └── multimodal_dataset.h5mu        # Final MuData output
```

!!! warning "Consistent modality names"
    Modality directory names must **exactly match** (case-sensitive) the `"name"` field in the configuration. Every sample directory must contain every modality defined in the configuration.

### Input file requirements by modality

| Modality | Required files | Sub-directory |
|----------|---------------|---------------|
| `microscopy_image` | One `.tiff`, `.tif`, `.ome.tiff`, `.ome.tif`, `.qptiff`, or `.czi` file | directly in `<modality_name>/` |
| `msi` (positive mode only) | `data.imzML` + `data.ibd` | `<modality_name>/pos/` |
| `msi` (negative mode only) | `data.imzML` + `data.ibd` | `<modality_name>/neg/` |
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
