# Alignment Methods

## 1. Objective

Alignment estimates where each **reference observation** lies in each non-reference modality coordinate system.

For a target modality \(T\), the aligned coordinates are stored as:

\[
\mathbf{R}^{(T)} \in \mathbb{R}^{N_R\times 2}, \qquad
\mathbf{R}^{(T)}_i = (x_i^{(T)}, y_i^{(T)})
\]

and written to the reference AnnData as:

```python
obsm[f"{target_name}_spatial"]
```

This matrix is the direct input for registration.

---

## 2. Implemented strategies

### `manual`

Interactive browser-based direct mapping (`DirectMappingAligner` + `DirectMappingAlignmentGUI`).

- The GUI displays the moving **reference** layer over the fixed **target** layer.
- The user transforms the reference layer as a whole: translation, rotation, scaling, horizontal/vertical flip, and free per-corner distortion (dragging one corner while the others stay fixed). The corner distortion makes the mapping a free-form / projective warp rather than a similarity or affine transform.
- Users confirm the final mapped coordinates.
- No parametric matrix (rigid or affine) is fit from landmarks; because the warp is free-form, the confirmed mapped coordinates are persisted directly.

!!! note "Internal naming"
    In `DirectMappingAligner` the constructor argument named `reference_modality` is given the pipeline's **non-reference** modality (the fixed frame), and `target_modality` is given the pipeline's **reference** modality (the moving layer). The orchestrator deliberately swaps them (`_run_alignment`), so the class-internal vocabulary is inverted relative to the pipeline terms used throughout this page. This page uses pipeline terms: *reference* = moving, *target* = fixed.

### `pre_aligned`

No GUI. The pipeline copies reference coordinates to the pair-specific aligned key:

\[
\texttt{obsm['\{target\}_spatial']} \leftarrow \texttt{obsm['spatial']}
\]

This is valid only when those coordinates are already expressed in the target frame.

Current config validation additionally enforces:

- reference modality is spot-based (`msi` or `st`)
- at most one non-reference modality uses `pre_aligned`

---

## 3. Data pathways by modality pair

What the GUI returns depends on the **reference (moving)** modality type; whether the result is stored as coordinates or a crop depends on the **target (fixed)** modality type. The pathways, written as (reference type → target type), are:

- **spot → spot** and **spot → image**: the GUI returns the mapped reference-spot coordinates; they are stored as `obsm['{target_name}_spatial']` on the reference's aligned AnnData. This covers the normal configurations, including a spot reference (`msi`/`st`) aligned against a microscopy or Raman image.
- **image → image**: the GUI returns the reference image's corner coordinates, which are used to crop the reference to the overlapping region (OME-TIFF output).
- **image → spot**: **not implemented** — this is the branch guarded in `DirectMappingAligner.align_dataset` (`is_ref_spot and is_target_image` in the class's internal, inverted vocabulary). It corresponds to an image-based reference paired with a spot target, which is an atypical configuration since the reference must be spot-based for MuData compilation.

---

## 4. Coordinate scaling

When the fixed target modality is an image, GUI interaction runs on its lowest OME-TIFF pyramid level, and the mapped coordinates (which are expressed in the target's coordinate space) are rescaled back to full resolution before persistence. If

- \((H_0, W_0)\): full-resolution dimensions of the target image
- \((H_L, W_L)\): displayed-level dimensions

then scale factors are:

\[
s_x = \frac{W_0}{W_L}, \qquad s_y = \frac{H_0}{H_L}
\]

Mapped GUI coordinates are rescaled component-wise before persistence:

\[
x_\text{full}=x_\text{gui}\,s_x,\qquad y_\text{full}=y_\text{gui}\,s_y
\]

For spot payloads, coordinates are already in physical space and scaling factors are 1.

---

## 5. GUI image representation details

For image modalities, display payload construction (`_image_to_rgb_uint8`) is:

- dynamic range normalization to uint8
- channel arrangement to HWC
- 1-channel -> replicated RGB
- 2-channel -> zero-padded third channel
- 3-channel -> unchanged
- 4+ channels -> NMF reduction to 3 components

This conversion is for visualization only; alignment outputs are stored as coordinates/crops.

---

## 6. Output artifacts

### Spot-reference alignment outputs

The aligned file is written on the **reference** modality and named after it. For a spot-based reference (the normal case), per sample:

```text
{dataset_path}/{sample_id}/alignment/{reference_name}_{sample_id}_processed_aligned.h5ad
```

It is built from the reference's preprocessed AnnData, so it contains the reference-native `obsm['spatial']` plus, for each target it was aligned against, the pair key:

```python
obsm[f"{target_name}_spatial"]
```

This key holds the reference coordinates expressed in that target (non-reference) modality's space. Multiple targets accumulate as multiple keys on the same reference AnnData.

Merged:

```text
{dataset_path}/merged/alignment/{reference_name}_merged_processed_aligned.h5ad
```

### Image-reference alignment outputs

When an image-based reference is aligned against an image target, the reference is cropped to the overlapping region and saved as a per-sample OME-TIFF:

```text
{dataset_path}/{sample_id}/alignment/{reference_name}_{sample_id}_processed_aligned.ome.tiff
```

---

## 7. Caching semantics

Alignment is skipped when expected aligned outputs are already present, unless `alignment_force_recomputing` is set to `true` on that modality's config entry.

For spot outputs, cache validity requires presence of the expected `obsm` key in the aligned file.
