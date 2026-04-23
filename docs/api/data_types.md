# Data Schemas

This page documents the canonical data schemas that FOCUS writes at each pipeline stage. Use this reference when integrating FOCUS outputs programmatically, writing custom downstream analyses, or implementing new modality handlers.

!!! note
    All coordinate arrays use physical units of **micrometers (µm)** unless stated otherwise. Sparse matrices use **SciPy CSR** format. All floating-point data are stored as **float32** unless stated otherwise.

---

## 1. Preprocessing outputs

### Image modalities (microscopy, raman) → OME-TIFF pyramid

Preprocessed image modalities are written as multi-resolution OME-TIFF files using `tifffile`. Key properties:

- **Dtype**: `float32` (per-channel pixel values)
- **Pyramid levels**: typically 4–6 levels; each level is downsampled by a factor of 2
- **Compression**: `zlib` (lossless)
- **Tiling**: 512 × 512 pixel tiles for efficient random access
- **Metadata**: OME-XML header embedded in the TIFF, containing pixel size (µm/px), channel names, and acquisition timestamp where available

The full-resolution level is at index `0`; higher indices are progressively downsampled. OME-TIFF files can be read with `tifffile.TiffFile`, `bioformats`, QuPath, or Napari.

---

### Omics modalities (msi, st) → AnnData `.h5ad`

#### MSI (lipidomics)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | sparse CSR float32 | $(n_\text{spots},\ n_\text{mz})$ | Ion intensities after normalization |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{spots},\ 2)$ | Physical coordinates in µm $(x, y)$ |
| `.obs['sample_id']` | categorical | $(n_\text{spots},)$ | Sample identifier (directory name) |
| `.obs['ion_mode']` | categorical | $(n_\text{spots},)$ | `'pos'` or `'neg'`; present only in dual-mode datasets |
| `.var_names` | Index | $(n_\text{mz},)$ | Consensus m/z values (float, formatted as strings) |
| `.uns['spot_size']` | float32 ndarray | $(2,)$ | Spot diameter $[x,\ y]$ in µm |

!!! note "Dual ion mode"
    When both positive and negative ion mode acquisitions are present, spots from both modes are concatenated along `obs`. The two sub-populations are identified by `.obs['ion_mode']`.

#### Spatial transcriptomics (ST)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | sparse CSR float32 | $(n_\text{spots},\ n_\text{genes})$ | Normalized expression counts (log1p or scran) |
| `.layers['raw']` | sparse CSR float32 | $(n_\text{spots},\ n_\text{genes})$ | Raw counts before normalization |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{spots},\ 2)$ | Spot coordinates in µm $(x, y)$ |
| `.obs['sample_id']` | categorical | $(n_\text{spots},)$ | Sample identifier |
| `.obs['leiden']` | categorical | $(n_\text{spots},)$ | Leiden cluster label (computed during preprocessing) |
| `.uns['spot_size']` | float32 ndarray | $(2,)$ | Spot diameter $[x,\ y]$ in µm |
| `.var['mt']` | bool | $(n_\text{genes},)$ | `True` for mitochondrial genes (prefix `MT-` / `mt-`) |

---

## 2. Alignment output (reference modality AnnData)

The Alignment stage does not produce a new file for each non-reference modality. Instead, it **adds coordinate keys to the reference modality AnnData**. After alignment, the reference AnnData contains:

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference modality coordinates (unchanged) |
| `.obsm['{non_ref_name}_spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference spots expressed in the non-reference modality's coordinate space |

One `.obsm['{non_ref_name}_spatial']` key is added per non-reference modality that was aligned. For example, if the reference is `"microscopy"` and non-reference modalities are `"msi"` and `"st"`, the aligned reference AnnData will contain `.obsm['spatial']`, `.obsm['msi_spatial']`, and `.obsm['st_spatial']`.

!!! note "Pre-aligned modalities"
    Modalities configured with `alignment_strategy: "pre_aligned"` do not add an extra key; registration for these modalities falls back to `.obsm['spatial']`.

---

## 3. Registration output (all modalities → per-modality AnnData)

Registration produces one AnnData per modality per sample, plus a merged AnnData combining all samples. All registration outputs share the same observation index as the reference modality, so row $i$ in every modality's registered AnnData corresponds to the same reference spot.

### Feature extraction registration (image modalities)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | float32 ndarray | $(n_\text{ref},\ n_\text{dims})$ | Patch embeddings (default: 1536-dim for Prov-GigaPath) |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference (anchor) spot coordinates in µm |
| `.obs['sample_id']` | categorical | $(n_\text{ref},)$ | Sample identifier |

!!! note "Embedding dimensionality"
    The default backbone is **Prov-GigaPath** (HuggingFace), which outputs 1536-dimensional embeddings. The embedding dimension can change if a different model is configured.

### Spot interpolation registration (omics modalities)

| Attribute | Type | Shape | Description |
|-----------|------|-------|-------------|
| `.X` | float32 ndarray | $(n_\text{ref},\ n_\text{features})$ | Gaussian-weighted interpolated features |
| `.obsm['spatial']` | float32 ndarray | $(n_\text{ref},\ 2)$ | Reference (anchor) spot coordinates in µm |
| `.obs['sample_id']` | categorical | $(n_\text{ref},)$ | Sample identifier |
| `.var_names` | Index | $(n_\text{features},)$ | Inherited from the target modality (m/z values or gene names) |

The interpolation kernel has a standard deviation proportional to `spot_size` from `.uns['spot_size']` of the target modality AnnData.

---

## 4. Final MuData output (`.h5mu`)

The final output is written to `{dataset_path}/merged/multimodal_dataset.h5mu`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `.mod['{modality_name}']` | AnnData | Registered AnnData for each modality (schema as in Section 3) |
| `.obsm['spatial']` | float32 ndarray | Shared reference coordinates, shape $(n_\text{ref\_total},\ 2)$ |
| `.obs['sample_id']` | categorical | Shared sample identifiers across all modalities |
| `.obs['spatial_annotation']` | categorical | Region labels transferred from GeoJSON annotations (if enabled; `NaN` if no annotation) |
| `.uns['spot_size']` | float32 ndarray | Spot size from the reference modality, shape $(2,)$ |

!!! note "Observation alignment"
    `.obs_names` in each `.mod` AnnData are harmonized so that `.mod['{modality_name}'][i]` and `.mod['{other_modality}'][i]` always refer to the same reference spot location. Modalities with missing data at a given spot receive `NaN` in `.X`.

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

# Downstream analysis with squidpy
import squidpy as sq
sq.pl.spatial_scatter(mdata.mod["st"], color="leiden")

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
    MSI and ST `.X` matrices are stored as sparse CSR arrays. Call `.toarray()` or `.toarray()` to convert to dense NumPy arrays when needed. For large datasets, prefer operating on the sparse representation directly (e.g., with `scipy.sparse` or `scanpy` functions that accept sparse input).
