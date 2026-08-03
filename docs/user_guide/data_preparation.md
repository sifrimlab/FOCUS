# Data Preparation

This guide explains how to organise your raw data before running FOCUS. Getting the directory layout right and following the per-modality file requirements prevents the most common errors before the pipeline starts.

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
- All samples must contain the same set of modality directories. A sample that is missing any modality directory causes FOCUS to raise an error during config validation, before any processing begins.
- Input file names inside a modality directory are not significant. FOCUS selects files by extension, not by name.
- FOCUS writes its output (preprocessed, aligned, registered files and the final `multimodal_dataset.h5mu`) back into `dataset_path` automatically. You do not need to create any output directories yourself.

---

## Per-Modality File Requirements

### Microscopy Image (`microscopy_image`)

Place exactly one image file per sample inside the modality directory. Supported extensions are `.tiff`, `.tif`, `.ome.tiff`, `.ome.tif`, `.qptiff`, and `.czi`. If multiple supported files are present, FOCUS selects the first match in extension-priority order (`.ome.tiff` > `.ome.tif` > `.qptiff` > `.tiff` > `.tif` > `.czi`). A modality directory with no supported file raises `FileNotFoundError` before processing starts.

- **TIFF/OME-TIFF**: any number of channels; any pixel type. The first series is read at its base level, so a pyramid already in the file is not reused. FOCUS builds its own.
- **qpTIFF (Akoya/PerkinElmer)**: if the file contains multiple resolutions or auxiliary series (pyramid levels, thumbnail, macro, label), only the highest-resolution series/level is used.
- **CZI (Zeiss)**: multi-scene and multi-dimensional CZI files are supported. FOCUS keeps index 0 of every leading axis until three axes remain, and prints a warning when the outermost axis holds more than one entry.

Only the first three channels are kept, whatever the source. Fewer channels are accepted: background removal and cropping promote a 1- or 2-channel image to 3 channels internally to compute the tissue mask, and detect whether its background is the bright or the dark class. For dark-background (fluorescence) acquisitions also set `background_color: "black"` in the modality's `processing_settings`, so the removed background is not filled brighter than the tissue.

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

    FOCUS detects the dual-mode layout automatically when both `pos/` and `neg/` hold a complete `.imzML` + `.ibd` pair. The two ion modes are processed independently and stored as separate AnnData objects in the merged dataset.

!!! tip "An unused ion mode folder can be left empty"
    A sample's ion modes are decided by the **files** present, not by the directory structure: an ion mode subdirectory holding neither an `.imzML` nor an `.ibd` is read as "this polarity was not acquired" and is ignored. The GUI scaffolds both `pos/` and `neg/` for every MSI sample, so if you only have one ion mode, leave the other folder empty. There is no need to delete it. Ion modes may also differ from sample to sample within one dataset.

!!! warning "imzML and ibd must be paired"
    The `.imzML` and `.ibd` files must share the same base name and be in the same directory. An incomplete pair (an `.imzML` without its `.ibd`, an `.ibd` without its `.imzML`, or mismatched base names) is reported as an error during configuration validation, before any processing starts. Only an ion-mode folder holding neither file kind is treated as "this polarity was not acquired" and ignored.

---

### Raman Spectroscopy Imaging (`raman`)

Place exactly one `.lif` (Leica Image Format) file per sample inside the modality directory. FOCUS loads the first file it finds whose name ends in `.lif`, so a directory holding several of them gives no guarantee about which one is used.

```
<dataset_path>/
└── sample_001/
    └── raman/
        └── acquisition.lif
```

The acquisition must be a **tile scan**: FOCUS only processes LIF image elements with at least two tiles, and a file containing no such element fails while loading, with nothing left to merge. Elements with fewer tiles are ignored, including single-field acquisitions and any automatically stitched image saved next to the tiles. Several tile-scan elements in one file are treated as consecutive spectral scans and merged along the channel axis, provided they share the same tile count and tile size.

FOCUS applies BaSiCpy flat-field correction per spectral channel, removes background, cleans the spectra, and assembles the tiles into a single mosaic with ASHLAR before writing the output OME-TIFF.

!!! warning "Raman requires auxiliary conda environments"
    The Raman preprocessing stage depends on two additional conda environments,
    `FOCUS_BaSiCpy` and `FOCUS_ASHLAR`. These are created automatically by the default
    installer. `install.sh` (or `install.ps1` on Windows) scans the `tools/` directory and builds
    one `FOCUS_<Name>` environment per subfolder, installing OpenJDK into each of them so
    ASHLAR has the Java it needs for tile stitching. No extra flag is required.

    If these environments are absent (for example, after a partial install), re-run the
    installer with `--reinstall`. Without them, every Raman sample fails: the error is
    printed as `Error processing sample <sample_id>: ...`, the pipeline continues with the
    next sample, and the modality ends up with no preprocessed output.

---

### Spatial Transcriptomics (`st`)

Place one AnnData file (`.h5ad`) per sample inside the modality directory. FOCUS loads the first `.h5ad` it finds there, so a directory holding more than one gives no guarantee about which is used.

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
| `.uns['spot_size']` | Physical spot size in µm: scalar, list `[w, h]`, or 1-D array (normalised internally to a float32 `(2,)` array). **Optional but strongly recommended.** If absent, FOCUS falls back to `[1.0, 1.0]` µm, which makes the physical footprint used during registration meaningless. Provide the real spot size whenever possible. Used during the registration stage. |

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
    Within each modality directory, FOCUS discovers files by extension. You can keep the original file names from your instrument software. There is no need to rename them.
