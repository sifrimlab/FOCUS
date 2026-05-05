# Registration Methods

## 1. Objective

Given aligned reference coordinates in target space, registration builds a target-modality feature matrix indexed by reference observations.

For target modality \(T\), output has shape \(N_R \times D_T\), where rows correspond one-to-one with reference observations.

---

## 2. Feature Extraction (`feature_extraction`)

Applicable modality type: `microscopy_image`.

### 2.1 Method

For each aligned reference location \((x_i, y_i)\) in image coordinates:

1. Extract square patch of side `patch_size` (default 224 px).
2. Clamp top-left patch origin to image bounds.
3. Zero-pad only if edge extraction yields smaller patch.
4. Detect background-only patches (>=99% pixels near configured background color).
5. Encode non-background patches with Prov-GigaPath.
6. Insert zero vectors for background-only patches so row count remains \(N_R\).

This preserves strict alignment between reference rows and registered rows.

### 2.2 Encoder normalization and model

Input tensor normalization:

\[
\hat{x}_{c} = \frac{x_c-\mu_c}{\sigma_c}
\]

with ImageNet statistics

\[
\mu=(0.485,0.456,0.406),\quad \sigma=(0.229,0.224,0.225)
\]

Model: `hf_hub:prov-gigapath/prov-gigapath` via `timm`, run in `torch.inference_mode()`.

Batch size is fixed to 32 in current implementation.

### 2.3 Outputs

Per-sample AnnData:

- `.X`: embeddings, shape `(N_ref, 1536)`
- `.obsm['spatial']`: anchor centers used for extraction
- `.obs['sample_id']`

### 2.4 Optional scaling

If `min_max_rescale: true`, merged matrix is transformed per feature dimension with `MinMaxScaler`:

\[
\tilde{x}_{i,d}=\frac{x_{i,d}-\min_j x_{j,d}}{\max_j x_{j,d}-\min_j x_{j,d}}
\]

### 2.5 Parameters reflected in code path

- `patch_size` (default 224)
- `background_color` (`white`/`black`)
- `min_max_rescale` (default true)
- `force_recomputing` (default false)

---

## 3. Spot Interpolation (`spot_interpolation`)

Applicable modality types: `msi`, `st`, `raman`.

### 3.1 Geometric setup

For each anchor coordinate \(r_i=(r_x,r_y)\), with anchor spot size \((s_x,s_y)\):

1. Query target points in a circular pre-neighborhood with radius
\[
r=\sqrt{(s_x/2)^2+(s_y/2)^2}
\]
using `cKDTree.query_ball_point`.

2. Keep only points inside axis-aligned rectangle:
\[
|t_{j,x}-r_x|\le s_x/2,\qquad |t_{j,y}-r_y|\le s_y/2
\]

### 3.2 Kernel and interpolation

Bandwidth:
\[
\sigma=\frac{\sqrt{s_x s_y}}{2}
\]

Weights:
\[
w_{ij}=\exp\!\left(-\frac{\|t_j-r_i\|^2}{2\sigma^2}\right)
\]

Normalized weighted average:
\[
\hat{f}(r_i)=\frac{\sum_{j\in\mathcal{N}_i} w_{ij} f(t_j)}{\sum_{j\in\mathcal{N}_i} w_{ij}}
\]

If \(\mathcal{N}_i=\varnothing\), row \(i\) is left at zeros.

### 3.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated target features at anchor rows
- `.obsm['spatial']`: anchor coordinates in target frame
- target `.var` and `.var_names` propagated when available

### 3.4 Optional scaling and parameters

- `min_max_rescale` (default true, merged-level)
- `force_recomputing` (default false)

---

## 4. Merge and cache behavior

Both registration engines:

- validate per-sample cache by checking `n_obs` against anchor row count
- recompute on mismatch
- merge per-sample files into

```text
{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad
```

---

## 5. Compatibility matrix

| Modality type | `feature_extraction` | `spot_interpolation` | `none` |
|---|---:|---:|---:|
| `microscopy_image` | yes | no | yes |
| `msi` | no | yes | yes |
| `raman` | no | yes | yes |
| `st` | no | yes | yes |

Reference modality itself is not registered; it defines the row index used by all registered targets.
