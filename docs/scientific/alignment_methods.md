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

This matrix is the direct input for [registration](registration_methods.md). When the annotation
modality is not the reference, it is also the input for
[annotation transfer](annotation_transfer.md).

---

## 2. Implemented strategies

### `manual`

Interactive browser-based direct mapping (`DirectMappingAligner` + `DirectMappingAlignmentGUI`).

- The GUI displays the moving **reference** layer over the fixed **target** layer.
- The reference layer is transformed as a whole: translation, rotation, scaling, horizontal/vertical
  flip, corner dragging (one corner moves, the other three stay) and edge dragging (the two corners of
  that edge move together).
- The GUI keeps a `gl-matrix` `mat3` per layer. Translation, rotation, scaling and flips compose onto
  the moving layer's matrix; a corner or edge drag replaces it with the **homography** that maps the
  layer's four original corners onto their dragged positions, solved by DLT with \(H_{33}=1\)
  (`gui_src/alignment/src/utils/matrix.ts::computeHomography`).
- On confirmation the GUI posts `transform_matrix`, the column-major serialization of

  \[
  M = M_\text{reference}^{-1}\, M_\text{target}
  \]

  which maps moving-layer coordinates into the fixed layer's frame
  (`gui_src/alignment/src/utils/export.ts`).
- `DirectMappingAligner._parse_alignment_result` reshapes it column-first, applies it to the full
  \(N_R\times2\) coordinate array in homogeneous coordinates and divides by \(w\), so the persisted
  mapping is projective and covers every spot, not only the ones drawn in the browser. The per-spot
  `spots` list in the same payload is a fallback used only when no matrix is present; the
  `corner_pixels` payload of the image→image case carries no matrix and is consumed directly.
- No matrix is fitted from user-placed landmarks.

!!! note "Internal naming"
    In `DirectMappingAligner` the constructor argument named `reference_modality` is given the pipeline's **non-reference** modality (the fixed frame), and `target_modality` is given the pipeline's **reference** modality (the moving layer). The orchestrator deliberately swaps them (`_run_alignment`), so the class-internal vocabulary is inverted relative to the pipeline terms used throughout this page. This page uses pipeline terms: *reference* = moving, *target* = fixed.

### `pre_aligned`

No GUI. The pipeline copies the reference's native `obsm['spatial']` unchanged into the pair-specific
aligned key `obsm['{target_name}_spatial']`.

This is valid only when those coordinates are already expressed in the target frame.

Current config validation additionally enforces:

- reference modality is spot-based (`msi` or `st`)
- at most one non-reference modality uses `pre_aligned`

---

## 3. Data pathways by modality pair

What the GUI returns depends on the **reference (moving)** modality type; whether the result is stored as coordinates or a crop depends on the **target (fixed)** modality type. The pathways, written as (reference type → target type), are:

- **spot → spot** and **spot → image**: the GUI returns the transform mapping the reference spots into
  the target frame; the mapped coordinates are stored as `obsm['{target_name}_spatial']` on the
  reference's aligned AnnData. This covers the normal configurations, including a spot reference
  (`msi`/`st`) aligned against a microscopy or Raman image.
- **image → image**: the GUI returns the reference image's four corners mapped into the target frame
  (`corner_pixels`). `_save_image_to_image` takes their bounding box, clamps it to the target image,
  and writes that crop of the **target** image as the OME-TIFF output.
- **image → spot**: **not supported.** Configuration validation (Step 9c in `utils.py::parse_config`)
  rejects an image-based reference whenever a spot-based non-reference modality is present, so the
  pipeline stops before preprocessing. The corresponding branch in
  `DirectMappingAligner.align_dataset` remains as a defensive guard: it logs an error and returns no
  aligned output.

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

In code the factors are stored row-major as `[H₀/H_L, W₀/W_L]` (y-scale first), and applied as
`x *= factors[1]`, `y *= factors[0]`. For spot payloads, coordinates are already in physical space
and scaling factors are 1.

The factors are those of the **fixed target** layer, which is the frame \(M\) maps into (§2), so the
two steps compose: \(M\) lands the reference in the target's displayed-level frame, and the factors
lift that to the target's full resolution.

---

## 4b. Native spot display and compact transport

Every spot is displayed at its original center and footprint. There is no observation-count
threshold for display pooling and no majority-vote label grid. The acquisition lattice inferred
from `.obsm['spatial']` ([ST §3.6.1](st_methods.md#361-clustering-units)) sets the axes of the
square footprints when its axes are approximately orthogonal and its steps match `spot_size`.
Other layouts retain generic rectangular footprints.

The `FSP1` binary payload is little-endian: a 16-byte header (magic, observation count,
version 1, reserved) followed by coordinate float32 pairs, uint32 class indices, uint32
original row IDs, and uint8 foreground flags. Thus a native observation costs 17 payload
bytes. Delivery may be spatially reordered for culling, without changing centers or row IDs.
The browser keeps typed-array storage behind an array-compatible view and draws bounded
GPU meshes. Off-screen meshes are culled; no observation is removed from analytical outputs.

Metadata maps each internal class index to the original saved cluster label, which the filter
chips display. Canvas marks and filter chips take their color from one function of layer and
class index. The fixed layer uses a warm palette and the moving layer a cool palette, so the two
unrelated partitions never share a color. Past the 16 fixed colors of a palette, colors continue
in the same hue band with golden-ratio hue steps and alternating lightness, without cycling.
Unassigned observations are neutral gray in both layers. Large categorical sets can still be
perceptually difficult to distinguish.

The full coordinate array stays on the backend. The existing exported matrix is applied to
all original coordinates, independently of class/foreground filtering or rendering detail.
Binary spot exports omit the redundant per-spot JSON list. The alignment matrix calculations
are unchanged and covered by golden tests.

For ST pairs, cache validity also checks the preprocessing input fingerprint. Aligned files
refresh counts, labels and metadata from preprocessing instead of reloading stale analytical
data. Other pair coordinates are preserved only when observation identity and native centers
still agree.

---

## 5. GUI image representation details

For image modalities, display payload construction (`_image_to_rgb_uint8`) is:

- dynamic range normalization to uint8
- channel arrangement to HWC
- 1-channel → replicated RGB
- 2-channel → zero-padded third channel
- 3-channel → unchanged
- 4+ channels → NMF reduction to 3 components

For the 4+-channel case the pixel-by-channel matrix \(V\in\mathbb{R}_{\ge0}^{P\times C}\) is factorized
as \(V \approx WH\) with \(W\in\mathbb{R}_{\ge0}^{P\times3}\), \(H\in\mathbb{R}_{\ge0}^{3\times C}\) by
non-negative matrix factorization (`init='nndsvda'`, fixed seed), minimizing
\(\lVert V-WH\rVert_F^2\); the three \(W\) components are mapped to RGB. This conversion is for
visualization only; alignment outputs are stored as coordinates/crops.

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

When an image-based reference is aligned against an image target, the **target** image is cropped to
the region covered by the reference and saved as a per-sample OME-TIFF (zlib), carrying the target
modality's name:

```text
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.ome.tiff
```

No merged file is produced for this pathway. `DirectMappingAligner._aligned_output_path` is the single
source of truth for both output names and is used by the writers and by every cache check.

---

## 7. Caching semantics

Alignment is skipped when the expected aligned outputs are already present. Cache validity is checked
per sample:

- spot outputs: the aligned `.h5ad` exists **and** contains `obsm['{target_name}_spatial']` (read with
  `h5py`, without loading the AnnData);
- image outputs: the aligned `.ome.tiff` exists.

`_compute_force_flags` in `orchestrator.py` re-runs a pair when any of the following is true:
`alignment_force_recomputing` on the non-reference modality, `processing_settings.force_recomputing`
on the reference modality, or `processing_settings.force_recomputing` on that non-reference modality.
The same disjunction is OR-ed into that modality's registration force flag.
