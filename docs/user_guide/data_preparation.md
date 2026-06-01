# Data Preparation

This guide explains how to organise your raw data before running FOCUS. Getting the directory layout right and understanding the per-modality file requirements will prevent the most common errors before the pipeline even starts.

---

## Required Directory Structure

FOCUS expects a two-level directory hierarchy rooted at the `dataset_path` you specify in the configuration:

```
<dataset_path>/
├── sample_001/
│   ├── <modality_name>/          ← must match "name" in config exactly (case-sensitive)
│   │   └── data file(s)
│   ├── <modality_name_2>/
│   │   └── data file(s)
│   └── ...
├── sample_002/
│   ├── <modality_name>/
│   │   └── data file(s)
│   ├── <modality_name_2>/
│   │   └── data file(s)
│   └── ...
└── ...
```

**Rules:**

- Every first-level subdirectory of `dataset_path` is treated as a sample. The directory name becomes the sample identifier throughout the pipeline and in the final MuData output.
- Every second-level subdirectory must be named exactly after the corresponding modality's `name` field in the config. The comparison is case-sensitive.
- All samples must contain the same set of modality directories. A sample that is missing even one modality directory will cause FOCUS to raise an error during config validation, before any processing begins.
- Input file names inside a modality directory are not significant. FOCUS selects files by extension, not by name.
- FOCUS writes its output (preprocessed, aligned, registered files and the final `multimodal_dataset.h5mu`) back into `dataset_path` automatically. You do not need to create any output directories yourself.

---

## Per-Modality File Requirements

### Microscopy Image (`microscopy_image`)

Place exactly one image file per sample inside the modality directory. Supported extensions are `.tiff`, `.tif`, `.ome.tiff`, `.ome.tif`, and `.czi`. If multiple supported files are present, FOCUS selects the first match in extension-priority order (`.ome.tiff` > `.ome.tif` > `.tiff` > `.tif` > `.czi`).

- **TIFF/OME-TIFF**: any number of channels; uint8, uint16, or float32 pixel type.
- **CZI (Zeiss)**: multi-scene and multi-time CZI files are supported. FOCUS reads the first scene and first timepoint.

```
<dataset_path>/
└── sample_001/
    └── microscopy/
        └── H&E_staining.tiff
```

!!! note "OME-TIFF input is preserved"
    If you supply an OME-TIFF as input, FOCUS will still re-encode the output as a fresh multi-resolution OME-TIFF pyramid after applying its processing steps (background removal, colour enhancement, cropping). The original file is not modified.

---

### Mass Spectrometry Imaging (`msi`)

MSI data requires both an `.imzML` file (XML metadata) and a matching `.ibd` file (binary spectra). These two files must share the same base name and reside in the same directory.

FOCUS supports two acquisition modes:

=== "Single ion mode"

    Place the `.imzML` and `.ibd` files inside the `pos/` subdirectory. Any base name is accepted.

    ```
    <dataset_path>/
    └── sample_001/
        └── msi/
            └── pos/
                ├── data.imzML
                └── data.ibd
    ```

=== "Dual ion mode (positive + negative)"

    Create two subdirectories named `pos/` and `neg/`, each containing their own `.imzML` + `.ibd` pair.

    ```
    <dataset_path>/
    └── sample_001/
        └── msi/
            ├── pos/
            │   ├── acquisition_pos.imzML
            │   └── acquisition_pos.ibd
            └── neg/
                ├── acquisition_neg.imzML
                └── acquisition_neg.ibd
    ```

    FOCUS detects the dual-mode layout automatically when `pos/` and `neg/` subdirectories are present. The two ion modes are processed independently and stored as separate AnnData objects in the merged dataset.

!!! warning "imzML and ibd must be paired"
    The `.imzML` and `.ibd` files must share the same base name and be in the same directory. A missing `.ibd` file will cause a file-not-found error when the parser tries to read spectra.

---

### Raman Spectroscopy Imaging (`raman`)

Place exactly one `.lif` (Leica Image Format) file per sample inside the modality directory.

```
<dataset_path>/
└── sample_001/
    └── raman/
        └── acquisition.lif
```

Multi-tile LIF files are fully supported. FOCUS assembles the individual tiles into a single mosaic image using ASHLAR and applies BaSiCpy flat-field correction before writing the output OME-TIFF.

!!! warning "Raman requires auxiliary conda environments"
    The Raman preprocessing stage depends on two additional conda environments,
    `FOCUS_BaSiCpy` and `FOCUS_ASHLAR`. These are created automatically by the default
    installer — `install.sh` (or `install.bat`) scans the `tools/` directory and builds
    one `FOCUS_<Name>` environment per subfolder, installing OpenJDK into `FOCUS_ASHLAR`
    for tile stitching. No extra flag is required.

    If these environments are absent (for example, after a partial install), re-run the
    installer with `--reinstall`. FOCUS raises an error when it encounters a Raman
    modality and the environments are missing.

---

### Spatial Transcriptomics (`st`)

Place exactly one AnnData file (`.h5ad`) per sample inside the modality directory.

```
<dataset_path>/
└── sample_001/
    └── st/
        └── visium_counts.h5ad
```

**Required AnnData contents:**

| Slot | Description |
|------|-------------|
| `.X` | Expression matrix (sparse CSR recommended). Raw counts or normalised values. |
| `.obsm['spatial']` | (n_spots × 2) float array of spatial coordinates. |
| `.var` | Gene metadata DataFrame (index = gene identifiers). |

**Optional but recommended:**

| Slot | Description |
|------|-------------|
| `.uns['spot_size']` | Physical spot size in µm: scalar, list `[w, h]`, or 1-D array (normalised internally to a float32 `(2,)` array). **Optional but strongly recommended.** If absent, FOCUS falls back to `[1.0, 1.0]` µm — which makes the physical footprint used during registration meaningless, so provide the real spot size whenever possible. Used during the registration stage. |

If `.X` is a dense array, FOCUS will convert it to sparse CSR automatically during loading.

!!! tip "Technology compatibility"
    The `st` modality is technology-agnostic. Any platform that exports standard AnnData with `.obsm['spatial']` is supported: 10x Visium, 10x Xenium, MERFISH, Slide-seq, Stereo-seq, and custom pipelines. Use `squidpy.read.visium()` or equivalent readers to generate the `.h5ad` input if your platform does not output it natively.

---

## Spatial Annotations (Optional)

If you want FOCUS to propagate spatial region labels (e.g., tissue compartments, histological annotations) onto the spots in the registered dataset, provide one GeoJSON annotation file per sample.

```
<dataset_path>/
└── sample_001/
    └── microscopy/
        ├── H&E_staining.tiff
        └── annotations.geojson     ← annotation file alongside the image
```

**GeoJSON requirements:**

- The file must be a valid GeoJSON `FeatureCollection`.
- Each `Feature` must have a `geometry` of type `Polygon` or `MultiPolygon`.
- Label resolution order: `properties.classification.name` → `properties.name` → `feature.id`.
- Interior polygon holes are ignored; only exterior rings are used.
- Coordinates are interpreted as pixel values in the same coordinate space as the image.

The annotation modality and file type are declared in the config under the `spatial_annotations` key (see the [Configuration guide](configuration.md)).

!!! warning "Alignment required for non-reference annotation modalities"
    If the annotation modality is **not** the reference modality, you must enable `perform_alignment`. The transfer reads the aligned coordinates produced by the alignment stage to place each spot into the GeoJSON's pixel space; without alignment, validation fails.

!!! tip "Exporting from QuPath"
    QuPath's *Annotations → Export as GeoJSON* produces a file that is directly compatible with FOCUS. Make sure to check "Include classification" when exporting so that `classification.name` is populated.

---

## Common Mistakes

!!! warning "Case-sensitive directory naming"
    The modality directory name and the `name` field in the config are compared case-sensitively. A directory named `Microscopy` will **not** match a config entry with `"name": "microscopy"`. Use exactly the same casing in both places.

!!! warning "All samples must have all modalities"
    FOCUS validates the full directory structure before running any processing. If one sample is missing a modality directory that is declared in the config, the pipeline will raise a `FileNotFoundError` immediately. FOCUS does not support missing modalities (support for this is planned for a future release). To resolve this: either exclude that sample from your dataset, or remove the modality from your configuration and process only the other modalities.

!!! warning "Do not place output files in input directories"
    FOCUS writes output into `preprocessing/`, `alignment/`, and `registration/` subdirectories it creates itself. Do not place your raw input files in those paths, or name your input directories with those names, as they will conflict with the pipeline's output.

!!! tip "Naming samples"
    Sample IDs are inferred from the names of the subdirectories directly under `dataset_path`. Use descriptive, filesystem-safe names such as `patient_01_section_A` for traceability. Avoid spaces; use underscores or hyphens instead. These names appear as keys in the final MuData object.

!!! tip "Input file names do not matter"
    Within each modality directory, FOCUS discovers files by extension. You can keep the original file names from your instrument software — there is no need to rename them.
