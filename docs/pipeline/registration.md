# Registration Stage

## Overview

Registration maps the **feature content** of each non-reference modality onto the **reference (anchor) spots**, so that every modality ends up indexed by the same reference observations. The reference modality is **not** registered. It defines the row index that all registered targets are aligned to.

For each non-reference modality, the mode is chosen by its `registration_type`. The spot coordinates used as the registration target come from the alignment stage, which wrote `obsm['{target_name}_spatial']` (the reference spots expressed in that modality's coordinate frame) onto the reference AnnData, next to the reference's own `obsm['spatial']`. A modality with `registration_type: "none"` is skipped.

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
| `feature_extraction` | `microscopy_image` (**H&E brightfield only**, see below) | CUDA GPU recommended; CPU fallback |
| `spot_interpolation` | `msi`, `st` | CPU |
| `spot_aggregation` | `msi`, `st` | CPU |
| `raman_pixel_interpolation` | `raman` | CPU |
| `none` | any (skips registration) | - |

FOCUS therefore has **four** registration modes. They are described below.

### Rules shared by all four modes

- **Inputs.** The aligned reference (anchor) AnnData supplies the positions through `obsm['spatial']` (its own frame) and `obsm['{target_name}_spatial']` (the target's frame), written by the alignment stage; the target modality supplies the features. Samples present in both are processed; the rest are ignored.
- **Missing alignment.** A sample whose anchor lacks `obsm['{target_name}_spatial']` is skipped with a warning. No output file is written for it.
- **Footprint.** Every mode reads the footprint size from the **anchor's** `uns['spot_size']`, in the units of the anchor's `obsm['spatial']` (a single value is applied to both axes). If the key is absent, `[1.0, 1.0]` is used and a warning is logged. The footprint is a disc-like ellipse when the anchor spots sit on a hexagonal lattice (standard Visium) and a rectangle along the lattice axes otherwise.
- **Anchor frame.** The footprint is evaluated in the anchor's own frame: target spots, pixels or image tokens are mapped back through the inverse of the alignment transform, which FOCUS recovers from the two stored coordinate sets. The footprint size is therefore correct whatever the target's units (µm, image pixels, Raman pixels). A target element belongs to a footprint when its centre lies inside it.
- **Uncovered anchors.** Any anchor with nothing inside its footprint, or that cannot be placed in the target frame, keeps an all-zero row, which the [compilation](compilation.md) coverage filter drops later.
- **Outputs.** One file per sample plus a merged file, each stamped with `uns['registration_type']`, `uns['focus_registration_fingerprint']` and `uns['focus_registration_geometry']`, and carrying `obs['sample_id']` and `obsm['spatial']` (the anchor coordinates in the target's frame). Merged files rewrite `obs_names` as `{sample_id}_{row_index}`.

The geometry is detailed in [Registration Methods §1](../scientific/registration_methods.md#1-objective).

---

## `feature_extraction`

Used for microscopy images. Each reference spot is described by a deep-learning embedding of exactly the image area inside its footprint.

!!! warning "H&E-stained brightfield images only"
    The encoder is **Prov-GigaPath**, a foundation model pretrained on brightfield tiles from H&E-stained whole-slide images ([model card](https://huggingface.co/prov-gigapath/prov-gigapath)). Its embeddings describe H&E morphology, so `feature_extraction` is only appropriate when the modality is an **H&E-stained histological section imaged in brightfield RGB**.

    FOCUS performs no check on the stain, the imaging mode or the channel content. Any microscopy image is cut into windows, normalized and forwarded to the model, and a full `(N_ref, 1536)` matrix comes back without an error or a warning. For immunofluorescence, IHC with other chromogens, other histological stains, or any acquisition that is not brightfield RGB, those numbers do not describe the tissue and must not be interpreted as morphology.

    For such modalities set `"registration_type": "none"`. The modality is still preprocessed and aligned, and its OME-TIFF stays available under `alignment/`; it is only left out of the registered outputs and the final MuData.

!!! note "Channel handling before encoding"
    Windows are cut as 3-channel RGB. A single-channel image is **replicated to RGB** by `ensure_hwc3` and encoded as if it were a grayscale brightfield slide, so a fluorescence acquisition saved as one channel runs to completion instead of failing. A 4-channel image has its 4th channel dropped. Neither conversion brings the input any closer to the model's training domain.

**Algorithm**

1. Choose the encoding scale. Prov-GigaPath was trained on tiles at 0.5 µm per pixel, so FOCUS reads the stored pyramid level of the preprocessed image whose pixel size is closest to 0.5 µm, without resampling it: a 40x scan (0.25 µm) is encoded from its half-resolution level, a 20x scan from full resolution. The pixel size comes from the image's OME metadata, written by preprocessing from the source file; with an MSI reference it can also be derived from the alignment. When it is unknown, the full-resolution level is used and a warning is logged.
2. Map every reference spot's footprint into the image (see the shared rules above).
3. Cut **windows** of 224 px, the encoder's input size, around the footprints, with at least 56 px of tissue around each footprint. Spots that fall in the same window share one forward pass. A footprint too large for a 224 px window gets a larger window (up to 1024 px), with interpolated position embeddings.
4. For each spot, add a **readout query** to its window: a copy of the encoder's class token whose attention is restricted to the window's patch tokens (the square pixel blocks the encoder embeds) whose centres lie inside the footprint. The window's own tokens attend to each other exactly as in a normal forward pass, so they keep the context of the surrounding tissue, while each spot's embedding reads only its footprint. A spot smaller than one token reads the token that contains its centre.
5. A token is **background** when **≥99 % of its pixels match the configured `background_color`** (white `[1,1,1]` or black `[0,0,0]`); background tokens are never read. A spot whose footprint holds only background, or lies outside the image, keeps an **all-zero embedding**, which the [compilation](compilation.md) coverage filter later drops.
6. Encode with **Prov-GigaPath** (`hf_hub:prov-gigapath/prov-gigapath`, loaded via `timm`, run under `torch.inference_mode()`), producing a **1536-dimensional** embedding per spot. Input windows are normalized with ImageNet statistics (μ = (0.485, 0.456, 0.406), σ = (0.229, 0.224, 0.225)); inference runs on the GPU when CUDA is available and otherwise on CPU. On GPU the batch size is chosen automatically per window size from the free VRAM and is reduced and retried if an out-of-memory error occurs.

When the model is loaded, FOCUS checks that a readout covering a whole window reproduces the encoder's standard embedding of that window, and stops with an error otherwise. No normalization is applied to the output embeddings.

**Requirements:** an H&E-stained brightfield RGB image (see the warning above), a `huggingface_token` in the config (for the model download), and for reasonable runtime an NVIDIA GPU with CUDA.

**Output AnnData:** `X` = `(N_ref, 1536)` dense embeddings; `obsm['spatial']` = the reference coordinates in full-resolution image pixels; `obs['sample_id']`; `uns['focus_registration_geometry']` also records the encoded pyramid level and its pixel size. There is no `var` metadata (`var_names` are positional strings) and no `layers`.

**Configuration**

```json
{
  "registration_type": "feature_extraction",
  "registration_settings": {
    "background_color": "white",
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `background_color` | string | `"white"` | `"white"` or `"black"`: the color counted toward the ≥99 % background test. Set it to the preprocessing `background_color`. |
| `force_recomputing` | bool | `false` | Recompute even if the cached output matches its inputs. |

The window size is fixed by the encoder; there is no patch size setting.

---

## `spot_interpolation`

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is a Gaussian-weighted average of the target spots that fall within the reference spot's footprint.

**Algorithm** for each anchor spot with spot size `(sx, sy)`:

1. Find the target spots whose centres, mapped into the anchor's frame, lie inside the anchor's footprint (candidates come from a `cKDTree` search in the target's frame).
2. Weight each of them by a Gaussian of its anchor-frame distance `d` to the anchor centre, `w = exp(−d² / (2σ²))`, with bandwidth `σ = √(sx·sy) / 2`, and normalize the weights to sum to 1.
3. Take the weighted average of the target feature vectors.

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
| `force_recomputing` | bool | `false` | Recompute even if the cached output matches its inputs. |

The interpolation footprint and bandwidth are derived automatically from the anchor `spot_size`; there are no manual kernel parameters.

---

## `spot_aggregation`

Used for spot-based omics modalities (MSI, ST). Each reference spot's feature vector is the **plain sum** of the target spots that fall within the reference spot's footprint. The footprint is the same as [`spot_interpolation`](#spot_interpolation), but the values are **summed instead of averaged**.

Use it for **subcellular-resolution** spot modalities (e.g. **Visium HD**), where the values to carry onto one reference spot are the accumulated signal of the many native spots it covers.

**Algorithm** for each anchor spot:

1. Find the target spots whose centres, mapped into the anchor's frame, lie inside the anchor's footprint, as in `spot_interpolation`.
2. **Sum** their feature vectors, each with equal weight.

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
| `force_recomputing` | bool | `false` | Recompute even if the cached output matches its inputs. |

The aggregation footprint is derived automatically from the anchor `spot_size`; there are no manual parameters.

---

## `raman_pixel_interpolation`

Used for Raman. Raman preprocessing produces a **hyperspectral OME-TIFF** (an ASHLAR-stitched image with one channel per Raman shift), not a spot table, so this engine adapts the interpolation to pixel data.

**Algorithm**

1. Treat each Raman **pixel as a spot**: its position is the pixel centre `(col + 0.5, row + 0.5)` in the full-resolution level (the frame the alignment step writes), and its feature vector is the pixel's spectral intensities across channels.
2. Map the footprints into the Raman image and keep only the pixels inside the union of their bounding boxes. Channel names come from the OME-XML metadata; ASHLAR writes none, so in practice the names are `Channel_0 … Channel_{C-1}`.
3. Run the **same Gaussian footprint interpolation** as [`spot_interpolation`](#spot_interpolation), using those pixels as the target spots. The footprint is the anchor's `spot_size` in the anchor's units, mapped through the alignment, so it covers the same tissue area whatever the Raman pixel size.

When no footprint reaches the image, meaning the anchor spots do not overlap the Raman mosaic at all, a warning is logged and every row is written as a zero vector.

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
| `force_recomputing` | bool | `false` | Recompute even if the cached output matches its inputs. |

---

## Caching

- **Per sample:** every registered output carries a fingerprint (`uns['focus_registration_fingerprint']`) of everything it was computed from: the registration mode and method version, the reference coordinates in both frames, the reference `spot_size`, the identity of the target input (the pixel digest of a preprocessed image, the preprocessing fingerprint of an AnnData, otherwise the file's size and modification time) and the mode's settings. A cached file is reused only when its fingerprint equals the one computed from the current inputs; a file without a fingerprint, or from a different mode, is recomputed. A re-alignment that moves the reference spots, a changed `spot_size` or a re-preprocessed target therefore recomputes the registration.
- **Merged:** the merged file is reused only when its fingerprint, built from the per-sample fingerprints in sample order, still matches.
- Outputs are written to a temporary file and renamed into place, so an interrupted run leaves no partial file.
- `force_recomputing: true` bypasses both caches for that modality. Forcing the modality's alignment (directly or through the preprocessing cascade) also forces its registration.

---

## Next steps

- [MuData Compilation](compilation.md): assembles the per-modality registered outputs into the final `.h5mu`.
- [Configuration Reference](../configuration/config_fields.md): full `registration_type` and `registration_settings` reference.
- [Alignment](alignment.md): the preceding stage that produces the `obsm['{target_name}_spatial']` coordinates registration relies on.
