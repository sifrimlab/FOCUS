# Registration Stage

## Overview

Registration maps the **feature content** of each non-reference modality onto the **reference (anchor) spots**, so that every modality ends up indexed by the same reference observations. The reference modality is **not** registered — it defines the row index that all registered targets are aligned to.

For each non-reference modality, the mode is chosen by its `registration_type`. The spot coordinates used as the registration target come from the alignment stage, which wrote `obsm['{modality}_spatial']` (the reference spots expressed in that modality's coordinate frame) onto the reference AnnData. A modality with `registration_type: "none"` is skipped.

Registration runs per sample and then merges the per-sample results:

```text
# per sample
{dataset_path}/{sample_id}/registration/{modality}_{sample_id}_processed_aligned_registered.h5ad
# merged across samples
{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad
```

---

## Selecting a mode

`registration_type` is set per modality and is **validated against the modality type** — an incompatible pairing raises a `ValueError` before processing starts.

| `registration_type` | Compatible modality types | Hardware |
|---|---|---|
| `feature_extraction` | `microscopy_image` | GPU (CUDA) |
| `spot_interpolation` | `msi`, `st` | CPU |
| `spot_aggregation` | `msi`, `st` | CPU |
| `raman_pixel_interpolation` | `raman` | CPU |
| `none` | any (skips registration) | — |

FOCUS therefore has **four** registration modes. They are described below.

---

## `feature_extraction`

Used for microscopy images. Each reference spot is described by a deep-learning embedding of the image patch centered on it.

**Algorithm**

1. Read the aligned reference coordinates `obsm['{modality}_spatial']` (the patch centers, in image pixel space).
2. For each center, extract a `patch_size`×`patch_size` patch, clamping the patch origin to the image bounds and zero-padding only if an edge patch would be smaller.
3. Mark a patch as **background** if **≥99 % of its pixels match the configured `background_color`** (white `[1,1,1]` or black `[0,0,0]`). Background patches are not sent to the model.
4. Encode the non-background patches with **Prov-GigaPath** (`hf_hub:prov-gigapath/prov-gigapath`, loaded via `timm`, run under `torch.inference_mode()`), producing a **1536-dimensional** embedding per patch. Input patches are normalized with ImageNet statistics (μ = (0.485, 0.456, 0.406), σ = (0.229, 0.224, 0.225)); inference runs on the GPU when CUDA is available and otherwise on CPU. On GPU the batch size is chosen automatically from the free VRAM (bounded between 8 and 512 patches) and is reduced and retried if an out-of-memory error occurs; on CPU a fixed batch of 32 is used.
5. Scatter the embeddings back into a full `(N_ref, 1536)` matrix; **background patches keep an all-zero embedding** so the row count stays aligned to the reference. (These all-zero rows are what the [compilation](compilation.md) coverage filter later drops.)

No normalization is applied to the output embeddings.

**Requirements:** an NVIDIA GPU with CUDA, and a `huggingface_token` in the config (for the model download).

**Output AnnData:** `X` = `(N_ref, 1536)` embeddings; `obsm['spatial']` = anchor centers; `obs['sample_id']`.

**Configuration**

```json
{
  "registration_type": "feature_extraction",
  "registration_settings": {
    "patch_size": 224,
    "background_color": "white",
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `patch_size` | int | `224` | Patch side length in pixels. Prov-GigaPath expects 224. |
| `background_color` | string | `"white"` | `"white"` or `"black"` — the color counted toward the ≥99 % background test. |
| `force_recomputing` | bool | `false` | Recompute even if a valid cache exists. |

---

## `spot_interpolation`

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is a Gaussian-weighted average of the target spots that fall within the reference spot's footprint.

**Algorithm** — for each anchor spot at `(cx, cy)` (in the target's frame) with spot size `(sx, sy)`:

1. Query the target spots within a circular pre-neighborhood of radius `r = √((sx/2)² + (sy/2)²)` using a `cKDTree`.
2. Keep only those inside the axis-aligned rectangle `|dx| ≤ sx/2` and `|dy| ≤ sy/2`.
3. Weight each kept target by a Gaussian of its distance, `w = exp(−(dx² + dy²) / (2σ²))`, with bandwidth `σ = √(sx·sy) / 2`, and normalize the weights to sum to 1.
4. Take the weighted average of the target feature vectors.

This is a **footprint average, not a fixed-k nearest-neighbor** scheme. If no target spot falls inside the footprint, the reference row is left as an **all-zero vector**. Any `layers` on the target are interpolated with the same kernel.

**Output AnnData:** `X` = `(N_ref, D)` interpolated features; `obsm['spatial']` = anchor coordinates; `obs['sample_id']`; the target's `var` is propagated.

**Configuration**

```json
{
  "registration_type": "spot_interpolation",
  "registration_settings": {
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force_recomputing` | bool | `false` | Recompute even if a valid cache exists. |

The interpolation footprint and bandwidth are derived automatically from the anchor `spot_size`; there are no manual kernel parameters.

---

## `spot_aggregation`

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is the **plain sum** of the target spots that fall within the reference spot's footprint — the same footprint as [`spot_interpolation`](#spot_interpolation), but **summed instead of averaged**.

This is intended for **subcellular-resolution** spot modalities (e.g. **Visium HD**), where each native spot carries very little signal: averaging dilutes it, whereas summing **accumulates** the total signal coming from the area a reference spot covers.

**Algorithm** — for each anchor spot at `(cx, cy)` (in the target's frame) with spot size `(sx, sy)`:

1. Query the target spots within a circular pre-neighborhood of radius `r = √((sx/2)² + (sy/2)²)` using a `cKDTree`.
2. Keep only those inside the axis-aligned rectangle `|dx| ≤ sx/2` and `|dy| ≤ sy/2`.
3. **Sum** the feature vectors of the kept targets, each with equal weight.

Internally this is built as a sparse `(N_ref, N_target)` 0/1 membership matrix `A` (`A[i,j] = 1` iff target `j` is inside anchor `i`'s footprint) and the aggregation is the sparse product `A @ X`. The computation is **sparse-preserving** — the target matrix is not densified — which matters for high-resolution inputs with many spots.

Key differences from `spot_interpolation`:

- **Sum, not weighted average.** There is no Gaussian kernel and no weighting; every in-footprint target contributes equally.
- **No normalization is applied.** The summed values are kept as-is and are deliberately **not** divided by the number of contributing spots (footprint occupancy) — dividing would reduce the result back to an average. `.X` and every `layer` are aggregated the same way.
- Footprints may **overlap**, so a single target spot can contribute to several reference spots.

If no target spot falls inside the footprint, the reference row is left as an **all-zero vector** (dropped later by the [compilation](compilation.md) coverage filter).

**Output AnnData:** `X` = `(N_ref, D)` summed features; `obsm['spatial']` = anchor coordinates; `obs['sample_id']`; the target's `var` is propagated; any target `layers` are summed with the same membership.

**Configuration**

```json
{
  "registration_type": "spot_aggregation",
  "registration_settings": {
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force_recomputing` | bool | `false` | Recompute even if a valid cache exists. |

The aggregation footprint is derived automatically from the anchor `spot_size`; there are no manual parameters.

---

## `raman_pixel_interpolation`

Used for Raman. Raman preprocessing produces a **hyperspectral OME-TIFF** (an ASHLAR-stitched image with one channel per Raman shift), not a spot table, so this dedicated engine adapts the interpolation to pixel data.

!!! warning "Temporary solution"
    `raman_pixel_interpolation` is a **stopgap**. To the author's knowledge there is currently **no feature-extraction model tailored to Raman hyperspectral imaging** — unlike microscopy, which is served well by a general-purpose pathology vision model (Prov-GigaPath). Until such a model exists, FOCUS registers Raman by Gaussian-averaging the spectral pixels inside each reference spot's footprint. When a suitable Raman feature extractor becomes available, FOCUS intends to add a dedicated `feature_extraction` path for Raman, analogous to microscopy.

**Algorithm**

1. Treat each Raman **pixel as a spot**: its position is the pixel grid coordinate `(col, row) = (x, y)` (the same convention the alignment step used), and its feature vector is the pixel's spectral intensities across channels.
2. Compute an **adaptive bounding box** around the anchor spots (extended by half a spot plus a 2-pixel margin) so the engine loads only the relevant sub-region of the OME-TIFF instead of the whole image. Channel names are read from the OME-XML metadata (falling back to `Channel_N`).
3. Run the **same Gaussian footprint interpolation** as [`spot_interpolation`](#spot_interpolation), using the loaded pixels as the target spots.

**Output AnnData:** `X` = `(N_ref, C)` interpolated spectra; `obsm['spatial']` = anchor coordinates; `obs['sample_id']`; `var` indexed by channel name.

**Configuration**

```json
{
  "registration_type": "raman_pixel_interpolation",
  "registration_settings": {
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force_recomputing` | bool | `false` | Recompute even if a valid cache exists. |

---

## Caching

- **Per sample:** every registered output is stamped with `uns['registration_type']`. A cached file is reused only when **both** its observation count matches the anchor's **and** its stamp matches the modality's current `registration_type`; otherwise it is recomputed. A missing stamp (a file written by an older FOCUS version) or a stamp from a different mode — e.g. after switching a modality from `spot_interpolation` to `spot_aggregation` — is therefore treated as stale and recomputed.
- **Merged:** the merged file is reused only when every per-sample file was cached and the active sample set is unchanged.
- `force_recomputing: true` bypasses both caches for that modality.

---

## Next steps

- [MuData Compilation](compilation.md) — assembles the per-modality registered outputs into the final `.h5mu`.
- [Configuration Reference](../configuration/config_fields.md) — full `registration_type` and `registration_settings` reference.
- [Alignment](alignment.md) — the preceding stage that produces the `obsm['{modality}_spatial']` coordinates registration relies on.
