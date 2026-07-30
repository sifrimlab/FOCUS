# Key Concepts

!!! note
    If you are new to spatial multiomics, we recommend reading the [Overview](../overview.md) before this page.

This page defines the terminology used throughout the FOCUS documentation and pipeline configuration. Each term is used consistently in configuration files, log output, and the Python API.

---

## Modality

A **modality** is a data type produced by a single instrument or measurement technology. Examples include a fluorescence microscopy scan, an MSI acquisition, a Raman spectroscopy imaging acquisition, or a spatial transcriptomics library. In FOCUS, each modality is identified by a user-chosen name (e.g., `"microscopy"`, `"msi"`, `"st"`) and a type key (e.g., `"microscopy_image"`, `"msi"`, `"raman"`, `"st"`) that tells FOCUS which processing algorithms to apply.

---

## Sample

A **sample** is a single tissue section that has been processed through one or more modalities. In a typical experiment, all modalities for a given sample were acquired from the same physical section (or consecutive serial sections from the same block). Multiple samples in a dataset represent biological or technical replicates. In FOCUS, each sample corresponds to one subdirectory under `dataset_path/` (e.g., `dataset/sample_001/`), and the directory name becomes the sample identifier (`sample_id`) in all output AnnData and MuData files.

---

## Spot

A **spot** is a discrete spatial measurement location. The concept applies to omics modalities (MSI, Raman, and spatial transcriptomics) where the instrument records one feature vector per spatial position rather than a continuous image. Each spot has:

- spatial coordinates $(x, y)$ expressed in micrometers ($\mu\text{m}$) and stored in `.obsm['spatial']`
- a feature vector (ion intensities, Raman spectrum, or gene expression counts) stored in `.X`
- a physical footprint described by `spot_size` (width × height in $\mu\text{m}$)

In the pipeline, spots from non-reference modalities are interpolated onto the reference spot grid during the [Registration stage](../pipeline/registration.md).

---

## Reference modality

The **reference modality** is the modality whose coordinate system is used as the common spatial frame for the entire dataset. All other modalities are warped into the reference coordinate system during the Alignment and Registration stages. The reference modality is specified by the `reference_modality` field in the pipeline configuration.

The reference modality is typically chosen as the modality with the lowest spatial resolution (coarser spot grid), since higher-resolution modalities can be reliably aggregated down to match it, but reliable upscaling is not possible. When multiple modalities have similar resolution, the one with the clearest visible tissue structure is preferred — most often the microscopy image — because it is easiest to visually overlap with the other modalities during the alignment step.

!!! note
    The reference modality itself is never transformed; it defines the target coordinate space. Only non-reference modalities are aligned and registered.

---

## Preprocessing

**Preprocessing** is the first pipeline stage. It applies modality-specific quality control, normalization, background removal, and format conversion to each raw data file, producing a standardized output (OME-TIFF for image modalities, AnnData `.h5ad` for omics modalities). Preprocessing is fully automated for all modalities; modality-specific parameters, such as intensity normalization, background handling, and binning, can be tuned in the configuration.

---

## Alignment

**Alignment** is the process of establishing spatial correspondence between two modalities by visually overlaying the reference modality onto the target. In FOCUS, alignment is performed interactively using a web-based GUI that displays the reference modality overlaid on the fixed target modality. The user transforms the reference modality as a whole — translation, rotation, scaling, horizontal/vertical flip, and free per-corner distortion (dragging one corner while the others stay fixed) — until it aligns with the target. Because corners can be dragged independently, the mapping is a free-form (perspective-style) warp rather than a rigid or affine transform, so FOCUS does not fit a parametric transform matrix: it stores the resulting mapped coordinates directly as an additional coordinate key in the reference modality AnnData (`.obsm['{non_ref_name}_spatial']`), expressing each reference spot's location in the non-reference modality's coordinate space. The registration stage then uses these coordinates to map features between modalities.

---

## Registration

**Registration** is the third pipeline stage. It uses the alignment transforms computed in the previous stage to map the feature content of each modality onto the reference coordinate grid, producing one AnnData per modality in which every observation corresponds to the same reference spot.

FOCUS implements four registration strategies:

- **Feature extraction** (`microscopy_image`): a square patch centered at each reference spot is extracted from the OME-TIFF and encoded by a pretrained vision model (Prov-GigaPath) into a dense embedding vector.
- **Spot interpolation** (`msi`, `st`): reference spot coordinates are expressed in the target modality's coordinate space; the feature vector at each reference location is a Gaussian-weighted average of the target spots that fall within the reference spot's footprint.
- **Spot aggregation** (`msi`, `st`): the same footprint as spot interpolation, but the target spots inside it are **summed** with equal weight (no kernel, no normalization) instead of averaged — accumulating rather than diluting signal, intended for subcellular-resolution data (e.g. Visium HD).
- **Raman pixel interpolation** (`raman`): the same Gaussian footprint interpolation applied to the pixels of the hyperspectral OME-TIFF — a temporary approach pending a Raman-specific feature extractor.

See the [Registration](../pipeline/registration.md) page for algorithm details.

---

## AnnData

**AnnData** is the standard data container for single-cell and spatial omics data in Python (package: [`anndata`](https://anndata.readthedocs.io)). It stores:

- `.X` — the primary feature matrix (spots × features)
- `.obsm['spatial']` — spatial coordinates, shape $(n_\text{spots}, 2)$
- `.obs` — per-observation (per-spot) metadata as a DataFrame
- `.var` — per-variable (per-feature) metadata as a DataFrame
- `.uns` — unstructured metadata dictionary
- `.layers` — alternative feature matrices (e.g., raw counts alongside normalized counts)

FOCUS writes all omics modalities as AnnData `.h5ad` files at every intermediate stage and inherits the AnnData schema at the final MuData output.

---

## MuData

**MuData** is a multi-modal extension of AnnData (package: [`mudata`](https://mudata.readthedocs.io)) that stores multiple AnnData objects — one per modality — under a single file. Observations are harmonized across modalities so that row $i$ in every modality corresponds to the same spatial location. FOCUS writes its final output as a MuData `.h5mu` file at `{dataset_path}/merged/multimodal_dataset.h5mu`.

---

## OME-TIFF

**OME-TIFF** (Open Microscopy Environment TIFF) is a standardized file format for microscopy images that supports:

- multi-resolution image pyramids (enabling fast pan/zoom at any scale)
- multi-channel images (e.g., DAPI, GFP, RFP channels)
- rich XML metadata describing pixel size, channel names, and acquisition parameters

FOCUS converts all image modalities (microscopy and Raman) to multi-resolution OME-TIFF pyramids during preprocessing, using zlib compression for efficient access. OME-TIFF files can be opened in [QuPath](https://qupath.github.io), [Napari](https://napari.org), [FIJI](https://fiji.sc), and other bioimaging tools.

---

## ion mode

**Ion mode** is a parameter specific to MSI (mass spectrometry imaging) data. In **positive ion mode**, the instrument detects positively charged molecular species (e.g., phosphatidylcholines, sphingomyelins); in **negative ion mode**, it detects negatively charged species (e.g., phosphatidylethanolamines, sulfatides). Together, positive and negative ion mode acquisitions cover different and complementary subsets of the lipidome.

FOCUS supports:

- **Single ion mode** — data in only `pos/` or only `neg/` per sample. The other subfolder may exist and be empty (the GUI creates both); FOCUS decides from the files present, not the folders
- **Dual ion mode** — both `pos/` and `neg/` data per sample; features (m/z values) are identified by mode via `.var['mz_mode']` column (`'pos'` or `'neg'`), allowing the same spot to have measurements from both ion modes

Ion mode is **not** a configuration setting — it is detected per sample from whether each ion mode subfolder holds a complete `.imzML` + `.ibd` pair. Samples within one dataset may therefore differ.

!!! warning "Mixed datasets zero-fill the missing polarity"
    The feature axis spans the union of both ion modes across the whole dataset. For a sample that
    only has one polarity, the other polarity's columns are written as **zeros**, which are not
    distinguishable from genuinely measured zeros. Use `.var['mz_mode']` together with the sample's
    own ion modes to mask those columns before any cross-sample statistics.

---

## spot_size

**`spot_size`** is the physical footprint of a single spot or pixel, expressed as $[\text{width}, \text{height}]$ in micrometers ($\mu\text{m}$). It is the canonical keyword for spot/pixel dimensions throughout FOCUS:

- Stored in `.uns['spot_size']` of every omics AnnData file as a 2-element `[x, y]` value (a `float32` array of shape `(2,)` for ST, a plain list of two floats for MSI). In **merged** files it becomes a `dict` keyed by `sample_id` instead, since samples can differ
- Used during spot/pixel registration (`spot_interpolation`, `spot_aggregation`, `raman_pixel_interpolation`) to set the footprint within which target spots/pixels are combined for each reference spot — a larger `spot_size` includes more neighbors. For `spot_interpolation` and `raman_pixel_interpolation` it also sets the radius of the Gaussian kernel used in the weighted average; `spot_aggregation` uses the footprint only, to select which spots are summed (no kernel)
- Carried forward to the final MuData `.uns['spot_size']`, where it reflects the reference modality's spot dimensions

**Automatic vs. manual specification:**
- **MSI**: Read automatically from instrument metadata (the `pixel size x`/`pixel size y` scan settings in the `.imzML` file), as an integer number of µm; defaults to `[1.0, 1.0]` when absent or below 1 µm. In dual ion mode the positive mode's raster size is used (both modes are expected to share it)
- **Raman**: Read automatically from instrument metadata (`.lif` file headers)
- **Spatial Transcriptomics (ST)**: Read from the input AnnData's `.uns['spot_size']` field; if absent, defaults to `[1.0, 1.0]` µm
- **Microscopy (microscopy_image)**: **Not used** — microscopy is an image modality and does not have discrete spots with a size parameter

If instrument metadata is missing for MSI or Raman, the default value `[1.0, 1.0]` µm is applied.
