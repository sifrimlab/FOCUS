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
| `raman_pixel_interpolation` | `raman` | CPU |
| `pixel_composition` | `raman`, `microscopy_image` (**fluorescence only**) | CPU |
| `none` | any (skips registration) | - |

FOCUS therefore has **four** registration modes. They are described below.

### Rules shared by all four modes

- **Inputs.** The aligned reference (anchor) AnnData supplies the positions through `obsm['spatial']` (its own frame) and `obsm['{target_name}_spatial']` (the target's frame), written by the alignment stage; the target modality supplies the features. Samples present in both are processed; the rest are ignored.
- **Missing alignment.** A sample whose anchor lacks `obsm['{target_name}_spatial']` is skipped with a warning. No output file is written for it.
- **Footprint.** Every mode reads the footprint size from the **anchor's** `uns['spot_size']`, in the units of the anchor's `obsm['spatial']` (a single value is applied to both axes). If the key is absent, `[1.0, 1.0]` is used and a warning is logged. The footprint is a disc-like ellipse when the anchor spots sit on a hexagonal lattice (standard Visium) and a rectangle along the lattice axes otherwise.
- **Anchor frame.** The footprint is evaluated in the anchor's own frame: target spots, pixels or image tokens are mapped back through the inverse of the alignment transform, which FOCUS recovers from the two stored coordinate sets. The footprint size is therefore correct whatever the target's units (image pixels, Raman pixels); `spot_interpolation` also compares areas across the two frames and needs both in µm. A pixel or image token belongs to a footprint when its centre lies inside it. `spot_interpolation` also gives every target spot its own footprint and uses the overlap area instead.
- **Uncovered anchors.** Any anchor with nothing inside its footprint, or that cannot be placed in the target frame, keeps an all-zero row, which the [compilation](compilation.md) coverage filter drops later.
- **Outputs.** One file per sample plus a merged file, each stamped with `uns['registration_type']`, `uns['focus_registration_fingerprint']` and `uns['focus_registration_geometry']`, and carrying `obs['sample_id']` and `obsm['spatial']` (the anchor coordinates in the target's frame). Merged files rewrite `obs_names` as `{sample_id}_{row_index}`.

The geometry is detailed in [Registration Methods §1](../scientific/registration_methods.md#1-objective).

---

## `feature_extraction`

Used for microscopy images. Each reference spot is described by a deep-learning embedding of the image area inside its footprint and, optionally, by the embedding of the 224 px image patch around it.

!!! warning "H&E-stained brightfield images only"
    The encoder is **Prov-GigaPath**, a foundation model pretrained on brightfield tiles from H&E-stained whole-slide images ([model card](https://huggingface.co/prov-gigapath/prov-gigapath)). Its embeddings describe H&E morphology, so `feature_extraction` is only appropriate when the modality is an **H&E-stained histological section imaged in brightfield RGB**.

    `feature_extraction` applies to brightfield images only. An image recorded as fluorescence is rejected: at configuration time when the modality sets `image_type: "fluorescence"`, and at registration time from the `focus_image_type` that preprocessing recorded in the image (for example when `image_type: auto` resolved to fluorescence). FOCUS performs no check on the stain of a brightfield image: it is cut into windows, normalized and forwarded to the model, and a full `(N_ref, 1536)` matrix comes back without an error or a warning. For IHC with other chromogens, other histological stains, or any brightfield acquisition that is not H&E, those numbers do not describe the tissue and must not be interpreted as morphology. Fluorescence images register with `pixel_composition`.

    For such modalities set `"registration_type": "none"`. The modality is still preprocessed and aligned, and its OME-TIFF stays available under `alignment/`; it is only left out of the registered outputs and the final MuData.

!!! note "Channel handling before encoding"
    Windows are cut as 3-channel RGB. A single-channel image is **replicated to RGB** by `ensure_hwc3` and encoded as if it were a grayscale brightfield slide. A 4-channel image has its 4th channel dropped. Neither conversion brings the input any closer to the model's training domain.

**Algorithm**

1. Choose the encoding scale. Prov-GigaPath was trained on tiles at 0.5 µm per pixel, so FOCUS reads the stored pyramid level of the preprocessed image whose pixel size is closest to 0.5 µm, without resampling it: a 40x scan (0.25 µm) is encoded from its half-resolution level, a 20x scan from full resolution. The pixel size comes from the image's OME metadata, written by preprocessing from the source file; with an MSI reference it can also be derived from the alignment. When it is unknown, the full-resolution level is used and a warning is logged.
2. Map every reference spot's footprint into the image (see the shared rules above).
3. Encode the image in **windows** of 224 px, the encoder's input size, on a fixed grid with a 112 px step (half a window), so neighbouring windows overlap by half. Each patch token (a 16 × 16 px block the encoder embeds) is taken from the one window it is most central in, which keeps at least 56 px of surrounding tissue as context around it.
4. Each spot's embedding is the **mean of the final-layer patch tokens its footprint overlaps**, each weighted by the fraction of the token that lies inside the footprint. A spot much smaller than one token reads the token that contains its centre; a large footprint simply reads tokens from several windows.
5. A token is **background** when **≥99 % of its pixels match the background colour** (white `[1,1,1]` or black `[0,0,0]`); background tokens are never read. The colour is the fill that preprocessing recorded in the image (`focus_background_fill`: 0 is black, any other value white) when `remove_background` was enabled, and the configured `background_color` otherwise. A spot whose footprint holds only background, or lies outside the image, keeps an **all-zero embedding**, which the [compilation](compilation.md) coverage filter later drops.
6. Encode with **Prov-GigaPath** (`hf_hub:prov-gigapath/prov-gigapath`, loaded via `timm`, run under `torch.inference_mode()`), producing a **1536-dimensional** embedding per spot. Input windows are normalized with ImageNet statistics (μ = (0.485, 0.456, 0.406), σ = (0.229, 0.224, 0.225)); inference runs on the GPU when CUDA is available and otherwise on CPU. On GPU the batch size is chosen automatically per window size from the free VRAM and is reduced and retried if an out-of-memory error occurs.

With `patch_embeddings: true`, each spot also gets its **patch**: the window that reads the token holding the spot's centre. The patch embedding is that window's standard Prov-GigaPath embedding (about 112 × 112 µm of tissue at 0.5 µm/px), shared by every spot linked to it. It is coarser than the spot embedding but much less noisy, which matters when the spots are small (for example 10–20 µm MSI pixels cover only one to three tokens). Spots with an all-zero embedding have no patch.

When the model is loaded, FOCUS checks that it returns one token per 16 px block of a 224 px window, and stops with an error otherwise. No normalization is applied to the output embeddings.

**Requirements:** an H&E-stained brightfield image (see the warning above), a `huggingface_token` in the config (for the model download), and for reasonable runtime an NVIDIA GPU with CUDA.

**Output AnnData:** `X` = `(N_ref, 1536)` dense spot embeddings; `obsm['spatial']` = the reference coordinates in full-resolution image pixels; `obs['sample_id']`; `uns['focus_registration_geometry']` also records the encoded pyramid level and its pixel size, the window and step sizes, and whether patch embeddings were written. With `patch_embeddings: true` there are also `obsm['patch_embedding']` = `(N_ref, 1536)` patch embeddings, `obsm['patch_spatial']` = each patch's centre in the reference coordinate system (NaN for spots without a patch), and `obs['patch_id']` = the patch index, unique within a sample (`-1` for spots without a patch). There is no `var` metadata (`var_names` are positional strings) and no `layers`.

**Configuration**

```json
{
  "registration_type": "feature_extraction",
  "registration_settings": {
    "background_color": "white",
    "patch_embeddings": false,
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `background_color` | string | `"white"` | `"white"` or `"black"`: the color counted toward the ≥99 % background test for an image that preprocessing did not fill. A filled image uses its recorded `focus_background_fill` instead. |
| `patch_embeddings` | bool | `false` | Also store, for every spot, the embedding of its 224 px patch (`obsm['patch_embedding']`), the patch centre in the reference frame (`obsm['patch_spatial']`) and the patch index (`obs['patch_id']`). Adds about as much data as `X`. |
| `force_recomputing` | bool | `false` | Recompute even if the cached output matches its inputs. |

The window size is fixed by the encoder; there is no patch size setting.

---

## `spot_interpolation`

Used for spot-based omics modalities (MSI, ST). One method covers every combination of spot sizes: reference spots larger, equal or smaller than the target spots, small weak target spots on a large signal-rich reference spot, and the reverse.

Every reference spot has a footprint (its `spot_size` around its centre, mapped into the target frame) and every target spot has one too (its own `spot_size` around its centre). The overlap area of the two decides how much of the target spot belongs to the reference spot:

- A reference spot inside one target spot takes that spot.
- A reference spot over many target spots takes all of them in proportion to their overlap.
- A target spot split among several small reference spots is shared by area.
- Target spots with gaps between them leave part of the reference footprint uncovered. The covered share is stored in `obs['coverage']`.

What is done with the overlap depends on what the target measures:

| Target | Reduction | Property |
|---|---|---|
| Intensities (MSI) | Area-weighted mean over the covered area | A constant field stays constant whatever the spot sizes. The value does not grow with the number of pixels in the footprint. |
| Counts (ST) | Each target spot gives the share of its counts that lies in the reference spot | Counts are conserved where reference spots tile the target. Weak small spots add up on a large reference spot with no loss. |

For an ST target the fractional counts go to `layers['raw']`, and the merged registered file is then normalized once with the target's method (`uns['focus_normalization']`). `obs['focus_area']` holds the covered footprint area, which `area_log_normalize` uses. A reference spot that no target spot overlaps keeps an **all-zero row** and is dropped later by the [compilation](compilation.md) coverage filter. Any `layers` of an MSI target are registered with the same operator.

!!! warning "Both coordinate systems in µm"
    Footprint areas are compared in one unit, so the reference and target coordinates and their `spot_size` values must be in µm. Coordinates in image pixels (Xenium cell centroids in pixels, for example) make the reference footprints look far smaller than they are. A map between the two frames whose scale is outside 0.5 to 2 target units per reference unit logs a warning. Convert the coordinates to µm before preprocessing.

**Output AnnData:** `X` = `(N_ref, D)` registered features as sparse CSR float32; `obs['sample_id']`; `obs['coverage']`; `obs['focus_area']` and `layers['raw']` for a normalized ST target; `obsm['spatial']` = anchor coordinates in the target's frame; the target's `var` is propagated.

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

The footprints come from the `spot_size` of the reference and of the target; there are no manual kernel parameters. A target without a valid `spot_size` uses the median spacing of its spots, with a warning.

!!! note "`spot_aggregation` was removed"
    Summing the target spots inside a footprint is now what `spot_interpolation` does for count targets, with overlap shares in place of whole spots. A config that sets `registration_type: "spot_aggregation"` fails validation and names `spot_interpolation`.

---

## `raman_pixel_interpolation`

Used for Raman. Raman preprocessing produces a **hyperspectral OME-TIFF** with one image per acquisition (each with one channel per Raman shift and its placement in image 0), not a spot table, so this engine adapts the interpolation to pixel data.

**Algorithm**

1. Treat each Raman **pixel as a spot**: its position is the pixel centre `(col + 0.5, row + 0.5)` in the full-resolution level (the frame the alignment step writes), and its feature vector is the pixel's spectral intensities across channels.
2. For every image, map the footprints through the alignment and the inverse of the image's placement (`focus_placement`: an affine map `a,b,tx,c,d,ty` holding the rotation, scale and shift of the acquisition, or the shift and scale `x0,y0,sx,sy` of files written before `raman-3`) and read only the pixels inside the union of their bounding boxes. Channel names come from the OME-XML (`<wavenumber> cm-1`); outputs written before `raman-2` carry none and are named `Channel_0` to `Channel_{C-1}`.
3. Run a **Gaussian footprint average** (weights `exp(−d² / (2σ²))`, `σ = √(sx·sy) / 2`, normalized to sum to 1) over the measured (finite) pixels whose centres lie inside the footprint. The footprint is the anchor's `spot_size` in the anchor's units, mapped through the alignment, so it covers the same tissue area whatever the Raman pixel size.
4. Apply the image's spot-level processing to each averaged spectrum: `raman-3` images store linear intensities and record the preprocessing settings `remove_offset` (`focus_spot_offset: min`, subtract the spectrum's minimum) and `normalization` (`focus_spot_normalization: area`, divide by the spectrum's mean over the channels). Averaging first keeps the per-pixel noise from biasing the minimum and the mean. Older files carry no such record and are used as stored.

A spot without a measured pixel of an image in its footprint gets NaN for that image's channels (an ROI acquisition that does not cover it); when an image is out of reach of every spot, a warning is logged. Spots with a NaN feature are dropped at compilation like any uncovered spot.

**Output AnnData:** `X` = `(N_ref, C)` dense interpolated spectra after the spot-level processing, the images' channels concatenated; `obsm['spatial']` = anchor coordinates in the Raman image-0 pixel frame; `obs['sample_id']`; `var` indexed by channel name (with the acquisition name appended when a wavenumber is measured by several acquisitions), columns `wavenumber` and `acquisition`. No `layers`.

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

## `pixel_composition`

Used for Raman and for multichannel fluorescence microscopy. Each anchor spot is described by the **composition of its pixels**: the fraction of the footprint occupied by each pixel phenotype of a dataset-level codebook. Pixels are close to one chemical or marker state; a spot is a mixture of states, which a mean spectrum does not resolve.

**Algorithm**

1. Group the images by channel signature (channel names and wavenumbers). Images with different channels never share a codebook; a group with fewer than 3 channels only gets channel means.
2. Draw up to 200,000 samples per group from the anchor footprints of every sample, and fit one codebook per group for the whole dataset:
    - **Raman**: linear spectral unmixing at the scale of the spots. The codebook draws spectra averaged over spot-sized blocks that lie at least half inside the footprints, with the image's spot-level offset removal and normalization; with area normalization, the nearly empty blocks (lowest 5 % of signal) are left out. The number of endmembers is the Gavish-Donoho rank of the blocks plus one; the endmembers are the archetypes of the blocks (convex combinations of real spectra that enclose them), near-duplicates merged, so spot compositions span the simplex. The activity log reports how closely the spot spectra are reconstructed from their composition.
    - **Fluorescence**: Pixie pixel clustering on measured pixels whose centres lie inside the footprints. Gaussian smoothing (σ = 2 px), per-pixel normalization to the total signal, 99.9th percentile scaling, a 10 x 10 self-organizing map and consensus hierarchical metaclustering; the number of phenotypes (at most 20) is the elbow of the within-cluster sum of squares (the FlowSOM criterion; in Pixie the user chooses it). Every pixel gets its phenotype, or `no signal` when its channels sum to 0.
3. Read the footprint region in tiles and average over the measured pixels whose centres lie inside each footprint:
    - **Raman**: the mean spectrum, with the spot-level offset removal and normalization, gets its fully constrained (non-negative, sum-to-one) abundances. Under the linear mixing model this is the mean abundance of the pixels, without the bias the constraints give single noisy pixels.
    - **Fluorescence**: the mean of the pixels' one-hot phenotype vectors.

The methods, the published sources and the FOCUS adaptations are in [Registration Methods §5](../scientific/registration_methods.md#5-pixel-composition-pixel_composition).

**Output AnnData:** `X` = `(N_ref, K)` dense composition, one block per channel group summing to 1 (`endmember 1..p` for Raman, `phenotype 1..k` and `no signal` for fluorescence), NaN where a group measured no pixel of the spot; `var` columns `group` and `phenotype`; `obsm['channel_mean']` = DataFrame of the mean pixel values per channel (uniform weights; linear intensities for `raman-3` Raman files, without offset removal or normalization); `obs['coverage']` = measured fraction of the footprint; `obsm['spatial']`; `obs['sample_id']`. The merged file stores `uns['pixel_phenotypes']` with, per group, the model, the prototype spectra or marker profiles, the share of each phenotype among the drawn bins or pixels and the criterion that set their number. Each sample also gets `{modality}_{sample_id}_pixel_phenotypes.ome.tiff`, a uint8 label map of every image (dominant phenotype per pixel, 0 where nothing was measured or read) with the source placement, for inspection in an image viewer. For Raman a pixel's label is its dominant endmember after offset removal and normalization taken from a copy of the image smoothed at σ = 2 µm; the label maps do not enter `X`.

**Configuration**

```json
{
  "registration_type": "pixel_composition",
  "registration_settings": {
    "force_recomputing": false
  }
}
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `force_recomputing` | bool | `false` | Refit the codebooks and recompute every sample, even if the cached outputs match their inputs. |

The codebooks are cached next to the merged file with the fingerprint of every sample's inputs, including the placement and spot-level processing of every image; adding, removing or changing a sample refits them and recomputes all samples.

---

## Caching

- **Per sample:** every registered output carries a fingerprint (`uns['focus_registration_fingerprint']`) of everything it was computed from: the registration mode and method version, the reference coordinates in both frames, the reference `spot_size`, the identity of the target input (the pixel digest of a preprocessed image, the preprocessing fingerprint of an AnnData, otherwise the file's size and modification time; a spot target is read from its merged preprocessed file, so the merged fingerprint identifies it) and the mode's settings. For `raman_pixel_interpolation` the settings include the placement and spot-level processing of every image, which the pixel digest does not cover; for `pixel_composition` they include the algorithm marker and the codebook fingerprint, which covers the same. A cached file is reused only when its fingerprint equals the one computed from the current inputs; a file without a fingerprint, or from a different mode, is recomputed. A re-alignment that moves the reference spots, a changed `spot_size` or a re-preprocessed target therefore recomputes the registration.
- **Merged:** the merged file is reused only when its fingerprint, built from the per-sample fingerprints in sample order, still matches.
- Outputs are written to a temporary file and renamed into place, so an interrupted run leaves no partial file.
- `force_recomputing: true` bypasses both caches for that modality. Forcing the modality's alignment (directly or through the preprocessing cascade) also forces its registration.

---

## Next steps

- [MuData Compilation](compilation.md): assembles the per-modality registered outputs into the final `.h5mu`.
- [Configuration Reference](../configuration/config_fields.md): full `registration_type` and `registration_settings` reference.
- [Alignment](alignment.md): the preceding stage that produces the `obsm['{target_name}_spatial']` coordinates registration relies on.
