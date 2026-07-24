# Registration Methods

## 1. Objective

Given aligned reference coordinates in target space, registration builds a target-modality feature matrix indexed by reference observations.

For target modality \(T\), output has shape \(N_R \times D_T\), where rows correspond one-to-one with reference observations.

---

## 2. Feature Extraction (`feature_extraction`)

Applicable modality type: `microscopy_image`.

### 2.1 Method

For each aligned reference location \((x_i, y_i)\) in image coordinates:

1. Extract square patch of side `patch_size` (default 224 px), centered on \((x_i,y_i)\).
2. Clamp the top-left origin to image bounds: \(x_0=\max(0,\min(x_0, W-\texttt{patch\_size}))\)
   (and analogously \(y_0\)).
3. Zero-pad only if edge extraction still yields a smaller patch.
4. Detect background-only patches: a patch is flagged background when at least 99 % of its pixels
   match the configured background color, where a pixel matches iff
   `np.isclose(pixel, bg_color, atol=1e-3)` holds across all channels (i.e. count of matching
   pixels \(\ge 0.99\cdot\) patch area).
5. Encode non-background patches with Prov-GigaPath.
6. Insert zero vectors for background-only patches so the row count remains \(N_R\).

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

On GPU the batch size is chosen automatically from the available VRAM (an empirical per-patch cost is probed, then the batch is sized to 80 % of free memory, clamped to 8–512 patches and rounded to a multiple of 8), and is halved and retried on a CUDA out-of-memory error. On CPU a fixed batch of 32 is used.

### 2.3 Outputs

Per-sample AnnData:

- `.X`: embeddings, shape `(N_ref, 1536)`
- `.obsm['spatial']`: anchor centers used for extraction
- `.obs['sample_id']`

### 2.4 Parameters reflected in code path

- `patch_size` (default 224)
- `background_color` (`white`/`black`)
- `force_recomputing` (default false)

---

## 3. Spot Interpolation (`spot_interpolation`)

Applicable modality types: `msi`, `st`.

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

### 3.4 Parameters

- `force_recomputing` (default false)

---

## 4. Spot Aggregation (`spot_aggregation`)

Applicable modality types: `msi`, `st`.

Same footprint geometry as §3, but the kept target points are **summed with equal weight** rather than Gaussian-averaged. Intended for subcellular-resolution spot modalities (e.g. Visium HD), where per-spot signal is low and averaging dilutes it; summing accumulates the signal under each reference footprint.

### 4.1 Geometric setup

Identical to §3.1: for each anchor coordinate \(r_i=(r_x,r_y)\) with anchor spot size \((s_x,s_y)\), query target points within radius \(r=\sqrt{(s_x/2)^2+(s_y/2)^2}\) via `cKDTree.query_ball_point`, then keep those inside the axis-aligned rectangle \(|t_{j,x}-r_x|\le s_x/2,\ |t_{j,y}-r_y|\le s_y/2\).

### 4.2 Aggregation

Equal-weight sum over the footprint:
\[
\hat{f}(r_i)=\sum_{j\in\mathcal{N}_i} f(t_j)
\]

There is no kernel and no normalization: weights are implicitly 1, and the sum is **not** divided by \(|\mathcal{N}_i|\) (footprint occupancy) — that division is exactly what distinguishes it from the average in §3.2. Equivalently, with the sparse \(N_R\times N_T\) membership matrix \(A\) where \(A_{ij}=1\) iff \(t_j\in\mathcal{N}_i\),
\[
\hat{F}=A\,F,
\]
evaluated as a sparse product (the target matrix \(F\) is not densified). Footprints may overlap, so one target may contribute to several reference rows. If \(\mathcal{N}_i=\varnothing\), row \(i\) is left at zeros.

`.X` and every `.layers` entry are aggregated with the same membership matrix \(A\).

### 4.3 Output semantics

Per-sample output AnnData has:

- `.X`: summed target features at anchor rows, shape \((N_R, D_T)\)
- `.obsm['spatial']`: anchor coordinates in target frame
- target `.var` and `.var_names` propagated when available
- any target `.layers` summed identically

### 4.4 Parameters

- `force_recomputing` (default false)

---

## 5. Raman Pixel Interpolation (`raman_pixel_interpolation`)

Applicable modality type: `raman`.

Raman preprocessing produces a hyperspectral OME-TIFF (one channel per Raman shift), not a spot table. This engine adapts the interpolation of §3 to pixel data.

### 5.1 Geometric setup

1. Each Raman pixel at grid position \((\text{col},\text{row})\) is treated as a target point with coordinate \((x,y)=(\text{col},\text{row})\) and feature vector equal to its spectral intensities across channels.
2. An adaptive pixel bounding box is computed around the anchor spots (extended by half a spot size plus a 2-pixel margin) so only the relevant sub-region of the OME-TIFF is loaded. Channel names are taken from the OME-XML metadata (fallback `Channel_N`).

### 5.2 Kernel and interpolation

Identical to §3.2: the same `SpotInterpolationRegistration._interpolate_features` Gaussian kernel is reused, with the loaded pixels as targets. Anchor rows with no pixel in the footprint are left at zeros.

### 5.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated spectra, shape \((N_R, C)\)
- `.obsm['spatial']`: anchor coordinates in the Raman pixel frame
- `.var`: indexed by spectral channel name

### 5.4 Status

This is a **temporary** approach. No feature-extraction model specific to Raman hyperspectral imaging is known to exist (unlike microscopy, served by Prov-GigaPath), so Gaussian pixel interpolation is used as a stopgap. A dedicated `feature_extraction` path for Raman is intended once a suitable model is available.

### 5.5 Parameters

- `force_recomputing` (default false)

---

## 6. Merge and cache behavior

All registration engines:

- stamp each output with `uns['registration_type']`
- validate the per-sample cache by checking **both** `n_obs` against the anchor row count **and** the stamp against the modality's current `registration_type`; a missing or mismatched stamp is treated as stale
- recompute on mismatch (so switching a modality's `registration_type` invalidates the previous mode's cache)
- merge per-sample files into

```text
{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad
```

---

## 7. Compatibility matrix

| Modality type | `feature_extraction` | `spot_interpolation` | `spot_aggregation` | `raman_pixel_interpolation` | `none` |
|---|---:|---:|---:|---:|---:|
| `microscopy_image` | yes | no | no | no | yes |
| `msi` | no | yes | yes | no | yes |
| `raman` | no | no | no | yes | yes |
| `st` | no | yes | yes | no | yes |

Compatibility is enforced during config validation; an incompatible `registration_type`/modality pairing raises a `ValueError`. The reference modality itself is not registered; it defines the row index used by all registered targets.
