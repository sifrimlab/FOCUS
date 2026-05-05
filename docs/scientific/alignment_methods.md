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

- The GUI displays the moving layer over a fixed layer.
- Users confirm final mapped coordinates.
- No affine matrix is fit from landmarks; the confirmed mapped coordinates are used directly.

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

FOCUS supports the following alignment pathways:

- **IMAGE -> IMAGE** (`microscopy_image`/`raman` to image): GUI returns corner coordinates used for cropping.
- **IMAGE -> SPOT**: GUI returns mapped spot coordinates.
- **SPOT -> SPOT**: GUI returns mapped spot coordinates.

`SPOT -> IMAGE` in the current implementation is not executed in `_run_alignment` (the branch is marked not implemented in `DirectMappingAligner.align_dataset`).

---

## 4. Coordinate scaling

For image payloads, GUI interaction runs on the lowest OME-TIFF pyramid level. If

- \((H_0, W_0)\): full-resolution dimensions
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

### Spot-target alignment outputs

Per sample:

```text
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.h5ad
```

Contains target-native `obsm['spatial']` plus newly added pair key:

```python
obsm[f"{reference_name}_spatial"]
```

In orchestrated pipeline usage, this key corresponds to reference coordinates expressed in non-reference space.

Merged:

```text
{dataset_path}/merged/alignment/{target_name}_merged_processed_aligned.h5ad
```

### Image-target alignment outputs

Per sample cropped OME-TIFF:

```text
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.ome.tiff
```

---

## 7. Caching semantics

Alignment is skipped when expected aligned outputs are already present, unless `alignment_force_recomputing` (or pair force conditions) is enabled.

For spot outputs, cache validity requires presence of the expected `obsm` key in the aligned file.
