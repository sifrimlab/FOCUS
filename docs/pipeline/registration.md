# Registration Stage

## Overview

Registration maps the **feature content** of each non-reference modality onto the **reference (anchor) spots**, so that every modality ends up indexed by the same reference observations. The reference modality is **not** registered. It defines the row index that all registered targets are aligned to.

For each non-reference modality, the mode is chosen by its `registration_type`. The spot coordinates used as the registration target come from the alignment stage, which wrote `obsm['{target_name}_spatial']` (the reference spots expressed in that modality's coordinate frame) onto the reference AnnData. A modality with `registration_type: "none"` is skipped.

!!! abstract "Scientific background"
    For the kernels, footprint geometry and per-mode output semantics, see
    [Registration Methods](../scientific/registration_methods.md).

Registration runs per sample and then merges the per-sample results:

```text
# per sample
{dataset_path}/{sample_id}/registration/{modality}_{sample_id}_processed_aligned_registered.h5ad
# merged across samples
{dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad
```

---

## Selecting a mode

`registration_type` is set per modality and is **validated against the modality type**. An incompatible pairing raises a `ValueError` before processing starts.

| `registration_type` | Compatible modality types | Hardware |
|---|---|---|
| `feature_extraction` | `microscopy_image` (**H&E brightfield only**, see below) | GPU (CUDA) |
| `spot_interpolation` | `msi`, `st` | CPU |
| `spot_aggregation` | `msi`, `st` | CPU |
| `raman_pixel_interpolation` | `raman` | CPU |
| `none` | any (skips registration) | - |

FOCUS therefore has **four** registration modes. They are described below.

### Rules shared by all four modes

- **Inputs.** The aligned reference (anchor) AnnData supplies the positions through `obsm['{target_name}_spatial']`, written by the alignment stage; the target modality supplies the features. Samples present in both are processed; the rest are ignored.
- **Missing alignment.** A sample whose anchor lacks `obsm['{target_name}_spatial']` is logged as an error and skipped. No output file is written for it.
- **Footprint size.** The three footprint modes read `spot_size` from the **anchor's** `uns['spot_size']` (a single value is applied to both axes). If the key is absent, `[1.0, 1.0]` is used and a warning is logged.
- **Uncovered anchors.** Any anchor with nothing inside its footprint keeps an all-zero row, which the [compilation](compilation.md) coverage filter drops later.
- **Outputs.** One file per sample plus a merged file, each stamped with `uns['registration_type']` and carrying `obs['sample_id']` and `obsm['spatial']`. Merged files rewrite `obs_names` as `{sample_id}_{row_index}`.

---

## `feature_extraction`

Used for microscopy images. Each reference spot is described by a deep-learning embedding of the image patch centered on it.

!!! warning "H&E-stained brightfield images only"
    The encoder is **Prov-GigaPath**, a foundation model pretrained on brightfield tiles from H&E-stained whole-slide images ([model card](https://huggingface.co/prov-gigapath/prov-gigapath)). Its embeddings describe H&E morphology, so `feature_extraction` is only appropriate when the modality is an **H&E-stained histological section imaged in brightfield RGB**.

    FOCUS performs no check on the stain, the imaging mode or the channel content. Any microscopy image is patched, normalized and forwarded to the model, and a full `(N_ref, 1536)` matrix comes back without an error or a warning. For immunofluorescence, IHC with other chromogens, other histological stains, or any acquisition that is not brightfield RGB, those numbers do not describe the tissue and must not be interpreted as morphology.

    For such modalities set `"registration_type": "none"`. The modality is still preprocessed and aligned, and its OME-TIFF stays available under `alignment/`; it is only left out of the registered outputs and the final MuData.

!!! note "Channel handling before patching"
    Patches are cut as 3-channel RGB. A single-channel image is **replicated to RGB** by `ensure_hwc3` and encoded as if it were a grayscale brightfield slide, so a fluorescence acquisition saved as one channel runs to completion instead of failing. A 4-channel image has its 4th channel dropped. Neither conversion brings the input any closer to the model's training domain.

**Algorithm**

1. Read the aligned reference coordinates `obsm['{target_name}_spatial']` (the patch centers, in image pixel space).
2. For each center, extract a `patch_size`×`patch_size` patch, clamping the patch origin to the image bounds and zero-padding only if an edge patch would be smaller.
3. Mark a patch as **background** if **≥99 % of its pixels match the configured `background_color`** (white `[1,1,1]` or black `[0,0,0]`). Background patches are not sent to the model.
4. Encode the non-background patches with **Prov-GigaPath** (`hf_hub:prov-gigapath/prov-gigapath`, loaded via `timm`, run under `torch.inference_mode()`), producing a **1536-dimensional** embedding per patch. Input patches are normalized with ImageNet statistics (μ = (0.485, 0.456, 0.406), σ = (0.229, 0.224, 0.225)); inference runs on the GPU when CUDA is available and otherwise on CPU. On GPU the batch size is chosen automatically from the free VRAM (bounded between 8 and 512 patches) and is reduced and retried if an out-of-memory error occurs; on CPU a fixed batch of 32 is used.
5. Scatter the embeddings back into a full `(N_ref, 1536)` matrix; **background patches keep an all-zero embedding** so the row count stays aligned to the reference. (These all-zero rows are what the [compilation](compilation.md) coverage filter later drops.)

No normalization is applied to the output embeddings.

The image is read at its **full-resolution** pyramid level; integer pixel data is rescaled by the dtype maximum to `[0, 1]`. Patch origins are `trunc(centre − patch_size/2)` clamped to `[0, W − patch_size]` and `[0, H − patch_size]`, so a patch never runs off the image and an anchor closer than half a patch to a border yields a patch centre shifted inwards.

**Requirements:** an H&E-stained brightfield RGB image (see the warning above), an NVIDIA GPU with CUDA, and a `huggingface_token` in the config (for the model download).

**Output AnnData:** `X` = `(N_ref, 1536)` dense embeddings; `obsm['spatial']` = the patch centres actually used (clamped, in image pixels); `obs['sample_id']`. There is no `var` metadata (`var_names` are positional strings) and no `layers`.

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
| `background_color` | string | `"white"` | `"white"` or `"black"`: the color counted toward the ≥99 % background test. |
| `force_recomputing` | bool | `false` | Recompute even if a valid cache exists. |

---

## `spot_interpolation`

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is a Gaussian-weighted average of the target spots that fall within the reference spot's footprint.

**Algorithm** for each anchor spot at `(cx, cy)` (in the target's frame) with spot size `(sx, sy)`:

1. Query the target spots within a circular pre-neighborhood of radius `r = √((sx/2)² + (sy/2)²)` using a `cKDTree`.
2. Keep only those inside the axis-aligned rectangle `|dx| ≤ sx/2` and `|dy| ≤ sy/2`.
3. Weight each kept target by a Gaussian of its distance, `w = exp(−(dx² + dy²) / (2σ²))`, with bandwidth `σ = √(sx·sy) / 2`, and normalize the weights to sum to 1.
4. Take the weighted average of the target feature vectors.

This is a **footprint average, not a fixed-k nearest-neighbor** scheme. If no target spot falls inside the footprint, the reference row is left as an **all-zero vector**. Any `layers` on the target are interpolated with the same kernel.

**Output AnnData:** `X` = `(N_ref, D)` **dense** interpolated features; `obsm['spatial']` = anchor coordinates in the target's frame; `obs['sample_id']`; the target's `var` is propagated; each target layer appears as a layer of the same shape.

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

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is the **plain sum** of the target spots that fall within the reference spot's footprint. The footprint is the same as [`spot_interpolation`](#spot_interpolation), but the values are **summed instead of averaged**.

Use it for **subcellular-resolution** spot modalities (e.g. **Visium HD**), where the values to carry onto one reference spot are the accumulated signal of the many native spots it covers.

**Algorithm** for each anchor spot at `(cx, cy)` (in the target's frame) with spot size `(sx, sy)`:

1. Query the target spots within a circular pre-neighborhood of radius `r = √((sx/2)² + (sy/2)²)` using a `cKDTree`.
2. Keep only those inside the axis-aligned rectangle `|dx| ≤ sx/2` and `|dy| ≤ sy/2`.
3. **Sum** the feature vectors of the kept targets, each with equal weight.

Internally this is built as a sparse `(N_ref, N_target)` 0/1 membership matrix `A` (`A[i,j] = 1` iff target `j` is inside anchor `i`'s footprint) and the aggregation is the sparse product `A @ X`. The computation is **sparse-preserving**: the target matrix is not densified, which matters for high-resolution inputs with many spots.

Key differences from `spot_interpolation`:

- **Sum, not weighted average.** There is no Gaussian kernel and no weighting; every in-footprint target contributes equally.
- **No normalization is applied.** The summed values are kept as-is; they are not divided by the number of contributing spots. `.X` and every `layer` are aggregated the same way.
- Footprints may **overlap**, so a single target spot can contribute to several reference spots.
- The output `X` stays **sparse (CSR)**, unlike `spot_interpolation`, which returns a dense matrix.

If no target spot falls inside the footprint, the reference row is left as an **all-zero vector** (dropped later by the [compilation](compilation.md) coverage filter).

**Output AnnData:** `X` = `(N_ref, D)` sparse CSR summed features; `obsm['spatial']` = anchor coordinates in the target's frame; `obs['sample_id']`; the target's `var` is propagated; any target `layers` are summed with the same membership.

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

Used for Raman. Raman preprocessing produces a **hyperspectral OME-TIFF** (an ASHLAR-stitched image with one channel per Raman shift), not a spot table, so this engine adapts the interpolation to pixel data.

**Algorithm**

1. Treat each Raman **pixel as a spot**: its position is the pixel grid coordinate `(col, row) = (x, y)` (the same convention the alignment step used), and its feature vector is the pixel's spectral intensities across channels.
2. Compute a **bounding box** around the anchor spots (extended by half a spot plus a 2-pixel margin, clamped to the image) and load only that sub-region of the full-resolution level. Channel names come from the OME-XML metadata; ASHLAR writes none, so in practice the names are `Channel_0 … Channel_{C-1}`.
3. Run the **same Gaussian footprint interpolation** as [`spot_interpolation`](#spot_interpolation), using the loaded pixels as the target spots. The footprint is the anchor's `spot_size` interpreted in **Raman pixels**.

When the bounding box falls entirely outside the image, meaning the anchor spots do not overlap the Raman mosaic at all, a warning is logged and every row is written as a zero vector.

**Output AnnData:** `X` = `(N_ref, C)` dense interpolated spectra; `obsm['spatial']` = anchor coordinates in the Raman pixel frame; `obs['sample_id']`; `var` indexed by channel name. No `layers`.

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

- **Per sample:** every registered output is stamped with `uns['registration_type']`. A cached file is reused only when **both** its observation count matches the anchor's **and** its stamp matches the modality's current `registration_type`; otherwise it is recomputed. A missing stamp (a file written by an older FOCUS version) or a stamp from a different mode, e.g. after switching a modality from `spot_interpolation` to `spot_aggregation`, is therefore treated as stale and recomputed.
- **Merged:** the merged file is reused only when every per-sample file was cached and the active sample set is unchanged.
- `force_recomputing: true` bypasses both caches for that modality.

---

## Next steps

- [MuData Compilation](compilation.md): assembles the per-modality registered outputs into the final `.h5mu`.
- [Configuration Reference](../configuration/config_fields.md): full `registration_type` and `registration_settings` reference.
- [Alignment](alignment.md): the preceding stage that produces the `obsm['{target_name}_spatial']` coordinates registration relies on.
