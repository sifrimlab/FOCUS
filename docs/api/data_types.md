# Data Schemas

This page documents the canonical data schemas that FOCUS writes at each pipeline stage. Use this reference when integrating FOCUS outputs programmatically, writing custom downstream analyses, or implementing new modality handlers.

!!! note
    All coordinate arrays use physical units of **micrometers (µm)** unless stated otherwise. Sparse matrices use **SciPy CSR** format. All floating-point data are stored as **float32** unless stated otherwise.

---

## 1. Preprocessing outputs

### Image modalities (microscopy, raman) → OME-TIFF outputs

- **Microscopy preprocessing** writes a lossless, tiled OME-TIFF pyramid with `tifffile`: level 0 holds the source pixels (after the optional crop and fill) in the source sample type with every channel, one page per channel or one interleaved page for RGB, 512 x 512 tiles, and the lower levels as SubIFDs. Compression is JPEG 2000 reversible for 8-bit brightfield images and zstd with a predictor otherwise. The OME-XML carries the channel metadata (name, colour, wavelengths, fluorophore, acquisition mode), the objective, `PhysicalSizeX/Y` in µm when known, and the tissue outline as the ROI `tissue` (one polygon per piece, in level-0 pixels). It also holds a FOCUS `MapAnnotation` (namespace `focus`) with string values:

    | Key | Value |
    |---|---|
    | `focus_preprocessing_fingerprint` | SHA-256 fingerprint of the method version, source file, sample, modality, settings, calibration constants and software versions; the cached file is reused only when it matches |
    | `focus_input_signature` | JSON `{"stat", "sha256"}` of the source file; the SHA-256 is reused while the source path, size and modification time are unchanged |
    | `focus_pixel_digest` | SHA-256 digest of the level-0 pages as written; downstream caches use it as the image's identity |
    | `focus_crop_offset` | `"x,y"`: position of the output's origin in the source image, in source pixels (`"0,0"` without tissue cropping) |
    | `focus_method` | Preprocessing method version (`microscopy-3`) |
    | `focus_pixel_size_source` | Where the pixel size was read: `ome`, `aperio`, `imagej`, `tiff_resolution`, `czi`, `nd2`, `configuration` or `none` |
    | `focus_image_type`, `focus_image_type_evidence` | `brightfield` or `fluorescence`, and the evidence it was resolved from; `feature_extraction` rejects `fluorescence` |
    | `focus_z_projection` | The Z rule and the plane used |
    | `focus_source_series` | Index of the chosen series or scene |
    | `focus_background_fill` | `none`, or the fill value written outside the tissue; `feature_extraction` uses it as the background colour |
    | `focus_display` | JSON per-channel display settings: name, colour, window `[low, high]` in native values and default visibility |
    | `focus_superseded_signature` | Present when a regenerated file is pixel-identical to the file it replaced: that file's path, size and modification time (JSON), so alignments made on it stay valid |

    See [OME-XML](../scientific/microscopy_methods.md#73-ome-xml) for the full layout.
- **Raman preprocessing** writes the OME-TIFF pyramid produced by the ASHLAR stitching step (via the external `FOCUS_ASHLAR` environment): `uint8`, one channel per spectral channel in scan order, Adobe-Deflate compressed with a horizontal predictor, with successive 2× levels. Its OME-XML holds the physical pixel size in µm; it carries no channel names and no wavenumber values, so the spectral axis cannot be read back from the file.

In both cases the full-resolution level is at index `0` and higher indices are progressively downsampled, stored as SubIFDs of a single series (`tifffile.TiffFile(...).series[0].levels`). OME-TIFF files can be read with `tifffile.TiffFile`, `bioformats`, QuPath, or Napari.

---

### Omics modalities (msi, st) → AnnData `.h5ad`

#### MSI (lipidomics)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | sparse CSR float32 | $(n_\text{spots},\ n_\text{mz})$ | Ion intensities after normalization |
| `.layers['raw']` | sparse CSR float32 | $(n_\text{spots},\ n_\text{mz})$ | Interpolated intensities *before* normalization |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{spots},\ 2)$ | Physical coordinates in µm $(x, y)$ |
| `.obsm['raster_coordinates']` | int32 ndarray | $(n_\text{spots},\ 2,\ 2)$ | Raster-cell bounding boxes $[[x_\text{tl}, y_\text{tl}], [x_\text{br}, y_\text{br}]]$ in µm |
| `.obs['sample_id']` | categorical | $(n_\text{spots},)$ | Sample identifier (directory name) |
| `.obs['foreground']` | categorical | $(n_\text{spots},)$ | Tissue/background mask stored as categorical boolean values. Always present; all `True` when no background was separated |
| `.obs['cluster']` | categorical | $(n_\text{spots},)$ | Per-sample cluster label, used only for spot colouring in the alignment GUI |
| `.var['mz']` | float32 ndarray | $(n_\text{mz},)$ | Consensus m/z values |
| `.var['mz_mode']` | categorical | $(n_\text{mz},)$ | Ion mode for each m/z (`'pos'` or `'neg'`) |
| `.var['lipid_annotation']` | categorical | $(n_\text{mz},)$ | Lipid annotation string: `"; "`-separated `db_name` hits, or `"Unannotated"` when no match/database is provided |
| `.uns['spot_size']` | list of 2 floats | - | Spot size $[x,\ y]$ in µm, derived from MSI raster metadata. In the **merged** file this is instead a `dict` keyed by `sample_id` |

`.var_names` are the stringified column indices `"0" … "n_mz-1"`, not the m/z values. Read `.var['mz']` for the masses.

!!! note "Dual ion mode"
    When both positive and negative ion mode acquisitions are present, their features are concatenated along `var` (columns), and mode identity is tracked in `.var['mz_mode']`.

!!! note "spot_size"
    For MSI, `spot_size` is propagated from parsed raster metadata. When MSI is the reference modality, it defines the registration footprint of every reference spot and the Gaussian kernel width.

#### Spatial transcriptomics (ST)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | sparse CSR | $(n_\text{spots},\ n_\text{genes})$ | Per sample: raw counts. Merged: normalized float32 (raw counts with `normalization: none`) |
| `.layers['raw']` | sparse CSR | $(n_\text{spots},\ n_\text{genes})$ | Merged file only: raw counts. **Present only when `.X` was normalized** |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{spots},\ 2)$ | Spot coordinates in µm $(x, y)$ |
| `.obs_names` | object | $(n_\text{spots},)$ | Spot names, prefixed `<sample_id>_` |
| `.obs['sample_id']` | categorical | $(n_\text{spots},)$ | Sample identifier |
| `.obs['cluster']` | categorical | $(n_\text{spots},)$ | Per-sample cluster label, used only for spot colouring in the alignment GUI |
| `.obs` QC | float / int | $(n_\text{spots},)$ | `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt`, `total_counts_control`, `pct_counts_control` and their `log1p_` variants |
| `.var['mt']` | bool | $(n_\text{genes},)$ | `True` for mitochondrial genes (case-insensitive `MT-` / `MT.` name prefix) |
| `.var` QC | float / int | $(n_\text{genes},)$ | `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants |
| `.uns['spot_size']` | float32 ndarray | $(2,)$ | Spot diameter $[x,\ y]$ in µm, read from the input AnnData's `.uns['spot_size']` (required). In the **merged** file this is instead a `dict` keyed by `sample_id` |

!!! note "spot_size"
    For ST modalities, `spot_size` is read from the input AnnData file's `.uns['spot_size']` field during preprocessing, in the same units as `.obsm['spatial']` (µm). A missing field raises `ValueError`. When ST is the reference modality, it defines the registration footprint of every reference spot; ensure your input ST AnnData has the correct spot diameter.

---

## 2. Alignment output (reference modality AnnData)

The Alignment stage does not produce a new file for each non-reference modality. Instead, it **adds coordinate keys to the reference modality AnnData**. After alignment, the reference AnnData contains:

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference modality coordinates (unchanged) |
| `.obsm['{non_ref_name}_spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference spots expressed in the non-reference modality's coordinate space (full-resolution pixels for an image modality) |
| `.uns['focus_alignment_inputs']` | dict of str | - | Per non-reference modality, the fingerprint of the two inputs the alignment was made on; see [Alignment caching](../pipeline/alignment.md) |

One `.obsm['{non_ref_name}_spatial']` key is added per non-reference modality that was aligned. For example, if the reference is `"st"` and non-reference modalities are `"msi"` and `"microscopy"`, the aligned reference AnnData will contain `.obsm['spatial']`, `.obsm['msi_spatial']`, and `.obsm['microscopy_spatial']`.

!!! note "Pre-aligned modalities"
    Modalities configured with `alignment_strategy: "pre_aligned"` store passthrough coordinates in `.obsm['{non_ref_name}_spatial']`: a copy of `.obsm['spatial']`, or for a microscopy image `.obsm['spatial']` minus the image's crop offset (`focus_crop_offset`), because pre-aligned coordinates are in the source image's pixel frame and the preprocessed image starts where preprocessing cropped it.

---

## 3. Registration output (all modalities → per-modality AnnData)

Registration produces one AnnData per modality per sample, plus a merged AnnData combining all samples. All registration outputs share the same observation index as the reference modality, so row $i$ in every modality's registered AnnData corresponds to the same reference spot.

Every registered file, whatever the mode, carries:

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.obs['sample_id']` | categorical | $(n_\text{ref},)$ | Sample identifier |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | The reference coordinates in the **target modality's** coordinate frame (`obsm['{target}_spatial']` of the aligned reference), not the reference's own frame |
| `.uns['registration_type']` | str | - | The mode that produced the file |
| `.uns['focus_registration_fingerprint']` | str | - | SHA-256 fingerprint of the inputs and settings the file was computed from; the cached file is reused only when it matches |
| `.uns['focus_registration_geometry']` | dict | - | `transform` (`identity`, `translation`, `homography` or `local_affine`: the alignment map recovered between the two reference frames), `lattice` (`hexagonal`, `square`, `rectangular`, `oblique` or `irregular`), `footprint` (`ellipse` or `rectangle`) and `footprint_angle` (radians). In the merged file this is a `dict` keyed by `sample_id` |

In the merged file, `.obs_names` are rewritten as `{sample_id}_{row_index}`.

Every mode reads the footprint from `.uns['spot_size']` of the **anchor** (aligned reference) AnnData, in the units of the anchor's `.obsm['spatial']`, and evaluates it in that frame: target positions are mapped back through the alignment map. The footprint is an ellipse inscribed in $s_x \times s_y$ on a hexagonal anchor lattice and a rectangle $s_x \times s_y$ along the lattice axes otherwise; a pixel belongs to it when its centre lies inside, while image tokens (`feature_extraction`) and spot targets (`spot_interpolation`) are weighted by their overlap with it. A missing `spot_size` falls back to `[1.0, 1.0]`. See [Registration Methods §1](../scientific/registration_methods.md#1-objective).

### Feature extraction registration (`feature_extraction`)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | float32 ndarray (dense) | $(n_\text{ref},\ n_\text{dims})$ | One Prov-GigaPath embedding per reference spot: the mean of the final-layer image tokens its footprint overlaps, weighted by the overlap (1536-dim). Rows whose footprint holds only background, or lies outside the image, are all-zero |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference coordinates in full-resolution image pixels |
| `.obsm['patch_embedding']` | float32 ndarray | $(n_\text{ref},\ n_\text{dims})$ | Only with `patch_embeddings: true`. The standard Prov-GigaPath embedding of the 224 px patch around each spot, shared by the spots of one patch; zero rows where `.X` is zero |
| `.obsm['patch_spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Only with `patch_embeddings: true`. Centre of each spot's patch in the reference coordinate system (the frame of the MuData's `obsm['spatial']`); NaN without a patch |
| `.obs['patch_id']` | int64 | $(n_\text{ref},)$ | Only with `patch_embeddings: true`. Patch index, unique within `sample_id`; `-1` where `.X` is zero |
| `.uns['focus_registration_geometry']` | dict | - | Also records `encoder` (model id), `encoded_level` (pyramid level encoded), `readout` (`token_mean`), `window_px` and `stride_px` (encoded-level pixels), `patch_embeddings` and, when the pixel size is known, `encoded_um_per_px` and `window_um` |
| `.var_names` | Index | $(n_\text{dims},)$ | Positional strings `"0" … "n_dims-1"`; the embedding has no feature metadata |

No `.layers` are written.

!!! note "Embedding dimensionality"
    The backbone is **Prov-GigaPath** (HuggingFace), which outputs 1536-dimensional embeddings. The width is whatever the model returns and is not hard-coded, so it would change if the backbone were swapped. Swapping the backbone requires a code change and is not exposed as a configuration option.

### Spot interpolation registration (`spot_interpolation`)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | **sparse CSR** float32 | $(n_\text{ref},\ n_\text{features})$ | MSI target: area-weighted mean of the target spots overlapping each anchor footprint. ST target: the raw counts of each target spot shared among the anchors by overlap, normalized once on the merged file. Anchors that no target spot overlaps are all-zero rows |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Anchor coordinates in the target modality's frame (`obsm['{target}_spatial']` of the aligned reference) |
| `.obs['coverage']` | float32 | $(n_\text{ref},)$ | Share of the anchor footprint covered by target spots, in [0, 1] |
| `.obs['focus_area']` | float32 | $(n_\text{ref},)$ | ST target only: covered anchor footprint area, the area of `area_log_normalize` |
| `.var` / `.var_names` | DataFrame / Index | $(n_\text{features},)$ | Copied from the target modality (m/z values or gene names) |
| `.layers['raw']` | sparse CSR float32 | $(n_\text{ref},\ n_\text{features})$ | ST target with a normalization: the apportioned (fractional) counts |
| `.layers['…']` | sparse CSR float32 | $(n_\text{ref},\ n_\text{features})$ | MSI target: every target layer, registered with the same operator |

Every target spot has a footprint from its own `spot_size`; the weights are the overlap areas of anchor and target footprints, estimated on a stratified sample grid of the smaller footprint ([Registration Methods §3](../scientific/registration_methods.md#3-spot-interpolation-spot_interpolation)). The geometry stamp also records `overlap_sampled` and `overlap_points_per_axis`.

### Raman pixel interpolation registration (`raman_pixel_interpolation`)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | float32 ndarray (dense) | $(n_\text{ref},\ n_\text{channels})$ | Gaussian-weighted average of the Raman pixels inside each anchor footprint, using the Gaussian kernel of §1.4 of the registration methods. Anchors with no pixel inside are all-zero |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Anchor coordinates in the Raman image's full-resolution pixel frame; pixel $(\text{col}, \text{row})$ is the point $(\text{col}+0.5, \text{row}+0.5)$ |
| `.var_names` | Index | $(n_\text{channels},)$ | Channel names from the OME-XML, falling back to `Channel_0 … Channel_{C-1}` |

No `.layers` are written.

### Pixel composition registration (`pixel_composition`)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | float32 ndarray (dense) | $(n_\text{ref},\ K)$ | Composition: mean pixel composition vector over the measured pixels inside each anchor footprint, one block per channel group summing to 1; NaN where a group measured no pixel of the anchor |
| `.var` | DataFrame | $(K,)$ | `group` (channel group) and `phenotype` (`endmember 1..p`, or `phenotype 1..k` and `no signal`) |
| `.obsm['channel_mean']` | DataFrame float32 | $(n_\text{ref},\ n_\text{channels})$ | Mean pixel values per channel inside each footprint (uniform weights) |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Anchor coordinates in the image-0 full-resolution pixel frame |
| `.obs['coverage']` | float32 | $(n_\text{ref},)$ | Measured fraction of the anchor footprint, the largest over the images |
| `.uns['pixel_phenotypes']` | dict | | Merged file only. Per group: `model`, `channels`, `phenotypes`, `prototypes` (endmember spectra or phenotype mean intensities), `pixel_share`, `criterion` |

Each sample also has `{modality}_{sample_id}_pixel_phenotypes.ome.tiff` in its registration folder: one uint8 label image per source image (dominant entry per pixel, 0 where nothing was measured or read), with the source placement. See [Registration Methods §5](../scientific/registration_methods.md#5-pixel-composition-pixel_composition).

---

## 4. Final MuData output (`.h5mu`)

The final output is written to `{dataset_path}/merged/multimodal_dataset.h5mu`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `.mod['{modality_name}']` | AnnData | Per-modality AnnData (schema as in Section 3), **except** that `obsm['spatial']` and `uns['spot_size']` are removed (they live at the top level) and `var_names` are namespaced as `{modality}:{name}` |
| `.obsm['spatial']` | float32 ndarray | Shared reference coordinates, shape $(n_\text{obs},\ 2)$ |
| `.obs['sample_id']` | categorical | Shared sample identifiers across all modalities |
| `.obs['spatial_annotation']` | categorical | Region labels transferred from GeoJSON annotations (present only when annotation transfer is enabled) |
| `.uns['spot_size']` | float32 ndarray | Length-2 `[width, height]` in µm, copied verbatim from the reference (anchor) merged AnnData; present only if the reference had it |

!!! note "Observation alignment"
    `.obs_names` in each included `.mod` AnnData are harmonized so that `.mod['{modality_name}'][i]` and `.mod['{other_modality}'][i]` refer to the same reference spot location. A modality is included only if its rows align to the reference. Its observation count must match and its `sample_id` sequence must match the reference element-wise; otherwise it is skipped with a warning. Reference spots that are uncovered (all-zero features) in any modality are dropped from every modality, so `n_obs` may be smaller than the reference spot count. See [Compilation](../pipeline/compilation.md).

---

## 5. Reading FOCUS outputs with Python

```python
import mudata as md
import scanpy as sc

mdata = md.read_h5mu("path/to/merged/multimodal_dataset.h5mu")

# Access individual modalities
st_adata = mdata.mod["st"]
msi_adata = mdata.mod["msi"]

# Shared spatial coordinates (n_spots, 2) in µm
coords = mdata.obsm["spatial"]

# Sample identifiers
sample_ids = mdata.obs["sample_id"]

# Spatial region annotations (if annotations were enabled)
annotations = mdata.obs["spatial_annotation"]

# Downstream analysis with squidpy (feature names are namespaced as {modality}:{name})
import squidpy as sq
sq.pl.spatial_scatter(mdata.mod["st"], color="st:CD3E")

# Access feature embeddings from microscopy registration
microscopy_embeddings = mdata.mod["microscopy"].X  # (n_spots, 1536)

# Concatenate all modalities for joint embedding (e.g., MOFA+)
import numpy as np
joint_matrix = np.hstack([
    mdata.mod["st"].X.toarray(),
    mdata.mod["msi"].X,
    mdata.mod["microscopy"].X,
])
```

!!! tip "Working with sparse matrices"
    MSI and ST `.X` matrices are stored as sparse CSR arrays. Call `.toarray()` to convert to dense NumPy arrays when needed. For large datasets, prefer operating on the sparse representation directly (e.g., with `scipy.sparse` or `scanpy` functions that accept sparse input).
