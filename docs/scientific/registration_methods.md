# Registration Methods

## 1. Motivation

After alignment, each reference spot $i$ has a known position $\mathbf{r}_i^{(T)}$ in the coordinate frame of every target modality $T$. Registration is the process of **extracting a feature representation at that position from $T$** — thereby constructing a feature matrix whose rows are indexed by the reference spot grid and whose columns are the features of modality $T$.

This operation is what makes cross-modality comparison possible: every row of the final MuData corresponds to the same physical tissue location across all modalities.

FOCUS implements two fundamentally different strategies depending on whether the target modality is a continuous image or a discrete spot measurement.

---

## 2. Feature Extraction Registration (`feature_extraction`)

**Applicable to:** `microscopy_image` only.  
**Hardware requirement:** NVIDIA GPU with CUDA.

### 2.1 Algorithm overview

For each reference spot at position $\mathbf{r}_i = (r_x, r_y)$ (in image pixel coordinates, as stored in `obsm['{image_name}_spatial']`):

1. **Patch extraction.** A square patch of side length $p$ pixels (default $p = 224$) is extracted from the full-resolution OME-TIFF, centred at $(r_x, r_y)$:

$$\text{patch}_i = \text{img}\!\left[r_y - \tfrac{p}{2} : r_y + \tfrac{p}{2},\; r_x - \tfrac{p}{2} : r_x + \tfrac{p}{2},\; :\right]$$

2. **Border handling.** If the centre is within $p/2$ pixels of an image boundary, the top-left corner is clamped to the nearest valid position (i.e., the centre shifts slightly). Patches that land outside the image extent are zero-padded to $p \times p$.

3. **Background detection.** A patch is classified as background if $\geq 99\%$ of its pixels match the background colour (white or black, as configured). Background patches are skipped during encoding but receive an all-zero embedding vector in the output, preserving the observation count.

4. **Patch normalisation.** Non-background patches are normalised with ImageNet statistics before passing through the encoder:

$$\hat{x}_{chw} = \frac{x_{chw} - \mu_c}{\sigma_c}, \qquad \boldsymbol{\mu} = (0.485, 0.456, 0.406),\quad \boldsymbol{\sigma} = (0.229, 0.224, 0.225)$$

5. **Encoding.** Patches are forwarded through **Prov-GigaPath** in batches of 32:

$$\mathbf{e}_i = f_\theta(\hat{x}_i) \in \mathbb{R}^{1536}$$

6. **Output.** The result is an AnnData with $\mathbf{X} \in \mathbb{R}^{N_\text{ref} \times 1536}$.

### 2.2 Prov-GigaPath model

Prov-GigaPath (Ma et al., 2024, *Nature*) is a vision transformer (ViT-g/14) pre-trained on 1.3 billion 256 × 256 µm tissue image tiles from over 170,000 whole-slide images spanning 31 major tissue types. It uses DINOv2 self-supervised training and achieves state-of-the-art performance on a wide range of computational pathology benchmarks.

The model is loaded via the `timm` library directly from the HuggingFace Hub (`prov-gigapath/prov-gigapath`) and requires a valid HuggingFace access token. A CUDA-capable GPU is required for practical throughput; the model is loaded in inference mode with `torch.inference_mode()`.

**Reference:** Xu et al. (2024). "A whole-slide foundation model for digital pathology from real-world data." *Nature*, 630, 181–188.

!!! note "Embedding dimension"
    The Prov-GigaPath patch encoder outputs 1536-dimensional vectors. This is reflected in the `n_vars = 1536` of the registered AnnData.

### 2.3 Min-max rescaling

When `min_max_rescale: true` (default), after all per-sample embeddings are concatenated into the merged AnnData, global min-max normalisation is applied across the full dataset using `sklearn.preprocessing.MinMaxScaler`:

$$\tilde{e}_{i,d} = \frac{e_{i,d} - \min_j e_{j,d}}{\max_j e_{j,d} - \min_j e_{j,d}}$$

This ensures that embedding dimensions are on a comparable scale across samples.

### 2.4 Registration settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `patch_size` | `224` | Patch side length in pixels; must match the model's expected input |
| `background_color` | `"white"` | Background colour for empty-patch detection (`"white"` or `"black"`) |
| `min_max_rescale` | `true` | Apply global min-max normalisation after merging all samples |
| `force_recomputing` | `false` | Reprocess even if a valid cached file exists |

---

## 3. Spot Interpolation Registration (`spot_interpolation`)

**Applicable to:** `msi`, `raman`, `st`.  
**Hardware requirement:** CPU only, no GPU required.

### 3.1 Problem statement

After alignment, each reference spot $i$ has a position $\mathbf{r}_i = (r_x, r_y)$ expressed in the target modality's coordinate system. The target modality contains $M$ spots at positions $\{\mathbf{t}_j\}_{j=1}^M$, each with a feature vector $\mathbf{f}_j \in \mathbb{R}^D$. Because the two spot grids are generally non-overlapping and non-coincident — they come from different instruments with different spatial resolutions — a reference spot typically does not coincide with any target spot.

The goal is to estimate the feature vector $\tilde{\mathbf{f}}_i$ that the target modality would have measured at position $\mathbf{r}_i$, given the measurements at the surrounding target spots.

### 3.2 Candidate search

The search neighbourhood for reference spot $i$ is a rectangle aligned with the coordinate axes:

$$\mathcal{N}_i = \left\{j \;\middle|\; |r_x - t_{j,x}| \leq \frac{s_x}{2} \;\text{ and }\; |r_y - t_{j,y}| \leq \frac{s_y}{2}\right\}$$

where $(s_x, s_y)$ is the reference spot size in µm (stored in `uns['spot_size']`). The rectangle captures all target spots whose centres fall within the area of the reference spot.

In practice, candidates are first identified using a `scipy.spatial.cKDTree` ball query with radius $\sqrt{(s_x/2)^2 + (s_y/2)^2}$ (the diagonal of the rectangle) for efficiency, then filtered to the exact rectangle.

### 3.3 Gaussian kernel weights

For each candidate target spot $j \in \mathcal{N}_i$, a Gaussian weight is computed based on its Euclidean distance from the reference spot centre:

$$w_{ij} = \exp\!\left(-\frac{(r_x - t_{j,x})^2 + (r_y - t_{j,y})^2}{2\sigma^2}\right)$$

where the bandwidth $\sigma$ is set proportional to the geometric mean of the spot dimensions:

$$\sigma = \frac{\sqrt{s_x \cdot s_y}}{2}$$

This makes the kernel scale-invariant: for a 50 µm × 50 µm spot, $\sigma = 25\,\mu\text{m}$; for a 100 µm × 50 µm spot, $\sigma \approx 35.4\,\mu\text{m}$.

### 3.4 Weighted interpolation

The interpolated feature vector at reference spot $i$ is the normalised Gaussian-weighted sum:

$$\tilde{\mathbf{f}}_i = \frac{\displaystyle\sum_{j \in \mathcal{N}_i} w_{ij}\, \mathbf{f}_j}{\displaystyle\sum_{j \in \mathcal{N}_i} w_{ij}}$$

If $\mathcal{N}_i = \emptyset$ (no target spots within the search rectangle), then $\tilde{\mathbf{f}}_i = \mathbf{0}$.

### 3.5 Optional min-max rescaling

When `min_max_rescale: true` (default), after all per-sample interpolated matrices are concatenated, global min-max normalisation is applied:

$$\tilde{f}_{i,d}^\text{scaled} = \frac{\tilde{f}_{i,d} - \min_j \tilde{f}_{j,d}}{\max_j \tilde{f}_{j,d} - \min_j \tilde{f}_{j,d}}$$

Applied globally across all samples, this corrects for inter-sample intensity differences and places all features on $[0, 1]$.

### 3.6 Registration settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_max_rescale` | `true` | Apply global min-max normalisation after merging all samples |
| `force_recomputing` | `false` | Reprocess even if a valid cached file exists |

---

## 4. Output Schema

Both registration strategies produce per-sample AnnData files that are merged into a single cross-sample AnnData. The output schema is identical regardless of strategy:

| Attribute | Shape | Description |
|-----------|-------|-------------|
| `.X` | $(N_\text{ref}, D)$ | Feature matrix at reference spot locations |
| `.obsm['spatial']` | $(N_\text{ref}, 2)$ | Reference spot coordinates in the target modality's space |
| `.obs['sample_id']` | $(N_\text{ref},)$ | Sample identifier string |
| `.var_names` | $(D,)$ | Feature names (embedding index for image; m/z values or gene names for omics) |

The merged file for modality $T$ is written to:

```
{dataset_path}/merged/registration/{T}_merged_processed_aligned_registered.h5ad
```

### Cache invalidation

The cache is considered valid if the registered file exists and its observation count equals the number of reference spots in the aligned anchor file. A mismatch (e.g., from rerunning preprocessing with a different spot filter) triggers automatic recomputation.

---

## 5. Modality Compatibility Matrix

| Modality type | `feature_extraction` | `spot_interpolation` | `none` |
|---------------|:--------------------:|:--------------------:|:------:|
| `microscopy_image` | ✅ | ❌ | ✅ |
| `msi` | ❌ | ✅ | ✅ |
| `raman` | ❌ | ✅ | ✅ |
| `st` | ❌ | ✅ | ✅ |

!!! warning "Reference modality"
    The reference modality is never registered — it defines the observation index and its feature matrix is included in the MuData as-is (after annotation transfer, if applicable).
