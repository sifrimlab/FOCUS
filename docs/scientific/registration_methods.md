# Registration Methods

## 1. Objective

Given aligned reference coordinates in target space, registration builds a target-modality feature matrix indexed by reference observations.

For target modality \(T\), output has shape \(N_R \times D_T\), where rows correspond one-to-one with reference observations.

The input is the `obsm['{target_name}_spatial']` matrix written by the
[alignment stage](alignment_methods.md). This page calls those coordinates the **anchors** and uses
the term interchangeably with *reference observations* throughout: they are the reference spots
expressed in the target modality's frame, and each one is the location at which that modality's
features are evaluated.

---

## 2. Feature extraction (`feature_extraction`)

Applicable modality type: `microscopy_image`, restricted by the encoder's training domain to
**H&E-stained brightfield RGB sections** (see §2.0).

### 2.0 Applicability

The encoder is Prov-GigaPath, pretrained on brightfield tiles from H&E-stained whole-slide images
([model card](https://huggingface.co/prov-gigapath/prov-gigapath)). The embeddings it produces are a
representation of H&E morphology, and nothing in the FOCUS code path narrows or adapts that domain:
patches are cut, ImageNet-normalized (§2.2) and forwarded to the model regardless of stain, imaging
mode or channel semantics, and the resulting \(N_R \times 1536\) matrix is written out with no
diagnostic. Applying it to immunofluorescence, IHC with other chromogens, other histological stains,
or any non-brightfield-RGB acquisition therefore yields well-formed embeddings of an input the model
was never trained on.

Channel coercion happens before patching (`ensure_hwc3`): a 1-channel image is replicated to RGB, a
4-channel image loses its 4th channel. A single-channel fluorescence image is consequently encoded as
a grayscale brightfield slide rather than rejected.

The intended configuration for those modalities is `registration_type: "none"`: preprocessing and
alignment still run and their outputs are kept, only the registration matrix (and hence the MuData
modality) is not produced.

### 2.1 Method

The image is read at its full-resolution pyramid level; integer dtypes are rescaled by their dtype
maximum so the encoder receives \([0,1]\) data. For each aligned reference location \((x_i, y_i)\) in
image coordinates:

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

On GPU the batch size is chosen automatically from the available VRAM (an empirical per-patch cost is probed, then the batch is sized to 80 % of free memory, clamped to 8 to 512 patches and rounded to a multiple of 8), and is halved and retried on a CUDA out-of-memory error. On CPU a fixed batch of 32 is used.

### 2.3 Outputs

Per-sample AnnData:

- `.X`: embeddings, dense float32, shape `(N_ref, 1536)`
- `.obsm['spatial']`: the patch centres actually used, \(\bigl(x_0 + \lfloor\texttt{patch\_size}/2\rfloor,\
  y_0 + \lfloor\texttt{patch\_size}/2\rfloor\bigr)\) after the clamping in step 2. These differ from
  the requested anchor coordinates whenever a spot sits closer than half a patch to an image border
- `.obs['sample_id']`
- no `.var` metadata (`var_names` are positional strings) and no `.layers`

### 2.4 Parameters reflected in code path

- `patch_size` (default 224)
- `background_color` (`white`/`black`)
- `force_recomputing` (default `false`)

---

## 3. Spot interpolation (`spot_interpolation`)

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

- `.X`: interpolated target features at anchor rows, dense float32
- `.obsm['spatial']`: anchor coordinates in target frame
- target `.var` and `.var_names` propagated
- every target `.layers` entry interpolated with the same kernel

### 3.4 Parameters

- `force_recomputing` (default `false`)

---

## 4. Spot aggregation (`spot_aggregation`)

Applicable modality types: `msi`, `st`.

Same footprint geometry as §3, but the kept target points are **summed with equal weight** rather than Gaussian-averaged. Applies to subcellular-resolution spot modalities (e.g. Visium HD), where one reference footprint covers many native spots.

### 4.1 Geometric setup

Identical to §3.1: for each anchor coordinate \(r_i=(r_x,r_y)\) with anchor spot size \((s_x,s_y)\), query target points within radius \(r=\sqrt{(s_x/2)^2+(s_y/2)^2}\) via `cKDTree.query_ball_point`, then keep those inside the axis-aligned rectangle \(|t_{j,x}-r_x|\le s_x/2,\ |t_{j,y}-r_y|\le s_y/2\).

### 4.2 Aggregation

Equal-weight sum over the footprint:
\[
\hat{f}(r_i)=\sum_{j\in\mathcal{N}_i} f(t_j)
\]

There is no kernel and no normalization: weights are implicitly 1, and the sum is **not** divided by \(|\mathcal{N}_i|\) (footprint occupancy). Equivalently, with the sparse \(N_R\times N_T\) membership matrix \(A\) where \(A_{ij}=1\) iff \(t_j\in\mathcal{N}_i\),
\[
\hat{F}=A\,F,
\]
evaluated as a sparse product (the target matrix \(F\) is not densified). Footprints may overlap, so one target may contribute to several reference rows. If \(\mathcal{N}_i=\varnothing\), row \(i\) is left at zeros.

`.X` and every `.layers` entry are aggregated with the same membership matrix \(A\).

### 4.3 Output semantics

Per-sample output AnnData has:

- `.X`: summed target features at anchor rows, shape \((N_R, D_T)\), kept **sparse (CSR)**, unlike
  §3, which densifies
- `.obsm['spatial']`: anchor coordinates in target frame
- target `.var` and `.var_names` propagated
- any target `.layers` summed identically, also sparse

### 4.4 Parameters

- `force_recomputing` (default `false`)

---

## 5. Raman pixel interpolation (`raman_pixel_interpolation`)

Applicable modality type: `raman`.

[Raman preprocessing](raman_methods.md#10-outputs) produces a hyperspectral OME-TIFF (one channel per Raman shift), not a spot table. This engine adapts the interpolation of §3 to pixel data.

### 5.1 Geometric setup

1. Each Raman pixel at grid position \((\text{col},\text{row})\) is treated as a target point with coordinate \((x,y)=(\text{col},\text{row})\) and feature vector equal to its spectral intensities across channels.
2. An adaptive pixel bounding box is computed around the anchor spots (extended by half a spot size plus a 2-pixel margin) so only the relevant sub-region of the OME-TIFF is loaded. Channel names are taken from the OME-XML metadata (fallback `Channel_N`).

### 5.2 Kernel and interpolation

Identical to §3.2: the same `SpotInterpolationRegistration._interpolate_features` Gaussian kernel is reused, with the loaded pixels as targets. Anchor rows with no pixel in the footprint are left at zeros.

If the bounding box lies entirely outside the image, a warning is logged and the output is an all-zero
\((N_R, C)\) matrix.

### 5.3 Output semantics

Per-sample output AnnData has:

- `.X`: interpolated spectra, dense float32, shape \((N_R, C)\)
- `.obsm['spatial']`: anchor coordinates in the Raman pixel frame
- `.var`: indexed by spectral channel name, read from the OME-XML. ASHLAR writes no channel names,
  so in practice `Channel_0 … Channel_{C-1}`
- no `.layers`

### 5.4 Parameters

- `force_recomputing` (default `false`)

---

## 6. Merge and cache behavior

All registration engines:

- process the samples present in both the anchor and the target mapping, and log an error and skip
  any sample whose anchor lacks `obsm['{target_name}_spatial']`
- stamp each output with `uns['registration_type']`
- validate the per-sample cache by checking **both** `n_obs` against the anchor row count **and** the stamp against the modality's current `registration_type`; a missing or mismatched stamp is treated as stale
- recompute on mismatch (so switching a modality's `registration_type` invalidates the previous mode's cache)
- reuse the merged file only when every per-sample file came from a valid cache **and** the merged
  file's `sample_id` set equals the active one; otherwise it is rebuilt with
  `anndata.concat(merge='same')`, `obs_names` rewritten as `{sample_id}_{row_index}`, and
  `uns['registration_type']` re-stamped
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
