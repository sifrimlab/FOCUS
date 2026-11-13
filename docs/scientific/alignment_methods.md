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
- `focus.alignment.result.parse_alignment_result` reshapes it column-first, applies it to the full
  \(N_R\times2\) coordinate array in homogeneous coordinates and divides by \(w\), so the persisted
  mapping is projective and covers every spot, not only the ones drawn in the browser. The per-spot
  `spots` list in the same payload is a fallback used only when no matrix is present; the
  `corner_pixels` payload of the image→image case carries no matrix and is consumed directly.
- When the moving layer is spot-based, the matrix can be refined with landmarks and excluded
  regions (§2a). A matrix-only confirmation is decoded exactly as described above.

!!! note "Internal naming"
    In `DirectMappingAligner` the constructor argument named `reference_modality` is given the pipeline's **non-reference** modality (the fixed frame), and `target_modality` is given the pipeline's **reference** modality (the moving layer). The orchestrator deliberately swaps them (`_run_alignment`), so the class-internal vocabulary is inverted relative to the pipeline terms used throughout this page. This page uses pipeline terms: *reference* = moving, *target* = fixed.

### 2a. Landmark warp (consecutive sections)

A single matrix cannot map tissue from **different sections** (e.g. ST on one cut, MSI on the
next): the sections deform unevenly, and forcing the corners to match maps a gland in one onto
stroma in the other. For a spot moving layer the tool therefore adds two instruments.

**Landmarks.** The operator pins a structure on the moving layer and drags it onto its
counterpart on the fixed layer. A landmark stores its moving-native position \(\mathbf{s}_k\)
and its fixed-layer display position \(\mathbf{d}_k\). With \(M\) the pair matrix above, the warp is

\[
W(\mathbf{x}) = R\big(M\mathbf{x}\big)
\]

where the residual map \(R\) is fitted from \(M\mathbf{s}_k \to \mathbf{d}_k\):

- 0 landmarks: identity (the matrix alone);
- otherwise a thin-plate spline with an affine part, fitted to the landmarks **and to the
  outline anchors** \(\mathbf{o}_j\): eight points on the moving layer's outline (its bounding
  box corners and edge midpoints, moving-native) held where the matrix puts them,
  \(M\mathbf{o}_j \to M\mathbf{o}_j\). An anchor within 15 % of the outline's diagonal of a landmark
  is released, so a landmark near the border can still move it. The anchors make each landmark
  bend the tissue around it while the overall placement stays with \(M\) (the Aligner tool);
  without them one landmark would translate the whole layer and two would rotate and scale it.
  (Warps stored without anchors fall back to translation / similarity for 1 / 2 landmarks.)
  In coordinates \(\mathbf{u}\) centred on the fit points
  and divided by their RMS spread,
  \(R(\mathbf{u}) = \mathbf{a}_0 + A\mathbf{u} + \sum_k \mathbf{w}_k\,\phi(\lVert\mathbf{u}-\mathbf{u}_k\rVert)\)
  with \(\phi(r)=r^2\log r\), solved from
  \(\begin{bmatrix}K+\lambda I & P\\ P^\top & 0\end{bmatrix}\begin{bmatrix}W\\ \mathbf{a}\end{bmatrix}=\begin{bmatrix}V\\0\end{bmatrix}\).
  The smoothing \(\lambda\) (the "stiffness", default \(10^{-3}\)) is unit-free thanks to the
  normalisation; \(\lambda=0\) interpolates every landmark exactly.

Landmarks stay attached to the tissue: a later change of \(M\) refits \(R\), so the pins (and the
outline) hold while the rest of the layer follows \(M\). The anchors travel in the confirm payload
(`landmarks.anchors`) and in the stored warp, so the backend fits exactly the same map.

Quality control in the browser: each landmark's leave-one-out error (its distance from where the
other landmarks place it, in fixed display pixels) is listed, and a pin well above the typical
error (more than 2.5 × the median and 2 px, with 4+ pins) is flagged. The sign of
\(\det \nabla R\) is tested on a 48 × 48 grid over the moving layer; folded cells (the warp turns
tissue inside out, typically from crossed pins) are shaded, and confirming then takes a second
click. The backend repeats the fold test on every spot and warns.

**Excluded regions.** Tears, folds and missing pieces cannot be modelled by any smooth map. The
operator lassoes such tissue on either layer; a moving-layer polygon is stored in moving-native
coordinates, a fixed-layer polygon in fixed display coordinates. A spot inside a moving-layer
polygon, or whose warped position falls inside a fixed-layer polygon, is stored as NaN and is
therefore unplaced in registration.

**Same maths on both sides.** `gui_src/alignment/src/utils/warp.ts` draws the warp live;
`focus.spatial.thin_plate` and `focus.spatial.warp.LandmarkWarp` apply it to every spot. The
parity fixture `gui_src/alignment/tests/fixtures/warp_parity.json` is checked by both test suites.
The confirmed payload adds `landmarks` (`src`, `dst`), `smoothing` and `exclusions` to
`transform_matrix` only when they are used, so a matrix-only confirmation is unchanged.

**Inverse.** \(W^{-1}\) (needed by §2b) starts from the matrix inverse of a residual map fitted in
the reverse direction and is refined by Newton steps on \(W\) with its analytic Jacobian
\(\nabla R(M\mathbf{x})\,\nabla M(\mathbf{x})\), to \(10^{-6}\) display pixels; non-converging points
(inside a fold) are NaN.

**Downstream.** Registration recovers the map from the stored correspondences
(`fit_anchor_to_target`). A warped alignment is not one homography, so the per-spot local affine
map is used; with a stored non-rigid warp this is the expected path and is logged without a
warning. On a smooth warp the local affine reproduces it to well below 1 % of a spot.

### 2b. Guide modality (`alignment_guide`)

The modality used for the interactive step can differ from the reference. A spot-based target
\(T\) aligned manually may set `alignment_guide` to another non-reference modality \(G\) that is
itself aligned directly to the reference \(R\) (manually or `pre_aligned`). Typical use: ST is
the reference and its Visium H&E is pre-aligned; the MSI comes from the next section. Matching
MSI against H&E morphology is much easier than matching it against ST spots.

The tool then shows \(T\)'s spots (moving) over \(G\) (fixed); landmarks and exclusions are
available as in §2a. With \(W_{TG}\) the confirmed warp and \(\mathbf{R}^{(G)}\) the reference
spots in \(G\)'s full-resolution frame (from the reference → guide pair),

\[
\mathbf{R}^{(T)} = W_{TG}^{-1}\big(\mathbf{R}^{(G)}\big)
\]

is stored as `obsm['{T}_spatial']` on the reference's aligned file, like any other pair, so
registration and annotation transfer are unaffected. Guides are aligned first. When the reference
→ guide pair is pre-aligned (Visium on its own H&E) it adds no operator error, and the composed
error is that of the step the operator can actually judge.

Validation (`focus.config.guides`): the guide must be a declared non-reference modality, not the
modality itself, and not itself guided; the guided modality must be spot-based (`msi`, `st`) and
`manual`. Guide chains (e.g. MSI → its own H&E → the ST H&E) are not supported yet: an image →
image pair does not produce a transform.

### `pre_aligned`

No GUI. The pipeline copies the reference's native `obsm['spatial']` into the pair-specific
aligned key `obsm['{target_name}_spatial']`. For a microscopy target the coordinates are taken to be
in the source image's pixel frame, and the crop offset recorded by
[preprocessing](microscopy_methods.md#53-crop-and-fill-opt-in) is subtracted, so they land
on the preprocessed image's pixels. A microscopy image without a recorded offset (preprocessed before
the offset was stored and not regenerated, which happens only when preprocessing is skipped, e.g. by
`scripts/align_only.py`) is used with offset 0 and a warning.

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

When the fixed target modality is an image, GUI interaction runs on its display level (the largest stored OME-TIFF pyramid level within 9 MP and 256 MiB of native samples over all channels), and the mapped coordinates (which are expressed in the target's coordinate space) are rescaled back to full resolution before persistence. If

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
from `.obsm['spatial']` ([Alignment Clustering §2](spatial_clustering.md#2-clustering-units)) sets the axes of the
square footprints when its axes are approximately orthogonal and its steps match `spot_size`.
On a hexagonal lattice (classified as in [Registration Methods](registration_methods.md)) each
spot is drawn as the hexagonal Voronoi cell of the lattice: flats facing the six neighbours,
`spot_size` across the flats. It circumscribes the disc used as the registration footprint and
tiles the lattice when `spot_size` equals the pitch. An axis-aligned square on a hexagonal lattice
overlaps the adjacent rows (0.87 pitch apart) and offsets every other row by half a spot.
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

**Clusters at a runtime resolution and feature images.**
- **Classes.** A spot modality clustered with a merge tree
  ([Alignment Clustering §5.1](spatial_clustering.md#51-merge-tree-and-runtime-resolution)) is sent
  with its fine clusters as classes. The metadata adds `cluster_tree` (the merges as pairs of fine
  labels, and the default level).
- **Merging in the browser.** The browser merges the classes to the level set on the layer's
  slider (2 to all fine clusters, starting at the default) and colours the groups by decreasing size.
- **Feature images.** These ion or gene maps
  ([§5.2](spatial_clustering.md#52-feature-images)) travel in an FSP **version 2** payload. The
  header's last field holds their number m, and m uint8 values per spot follow the foreground flags
  (0 = no value). The metadata adds their labels (`feature_images`).
- **Feature view.** It colours each spot by one image (magma, 32 levels) instead of its cluster.
  Every spot has a value: the clustering mask (background, spots the clustering left out) does not
  apply, so tissue removed by a failed background detection is still visible. Only a spot with no
  stored value (aggregated clustering units) is neutral grey. The class filter is hidden there.
- **Older files.** Files written without a tree are sent as before (FSP version 1, saved labels).

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

A microscopy image that carries display settings (`focus_display`, written by
[microscopy preprocessing](microscopy_methods.md#6-display-rendering) since `microscopy-3`) is rendered
from them: each shown channel is scaled to \([0, 1]\) by its window, multiplied by its colour and
added, then clipped; an RGB image uses its three windows directly. Multichannel images expose one
toggle per channel. Every other image (Raman, microscopy written before `microscopy-3`) keeps the
legacy display payload construction (`_image_to_rgb_uint8`):

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

Each manually confirmed spot pair also stores its warp as a JSON string at
`uns['focus_alignment_warps'][target_name]`: the matrix (row-major), the landmarks, the smoothing,
the exclusions, the fixed-layer display → full-resolution scale and a version number. When the pair
is aligned again, the browser starts from it (route `GET /initial_state`, re-expressed for the
current display level). A guided pair (§2b) stores its \(T \to G\) warp under \(T\), with the guide's
name and the signatures of \(T\) and \(G\).

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

FOCUS design choice: a spot output also records, in `uns['focus_alignment_inputs'][target_name]`, the
SHA-256 fingerprint of the identities of both inputs. A preprocessed microscopy image is identified by
its level-0 pixel digest, an MSI or ST AnnData by its preprocessing fingerprint, any other file by its
path, size and modification time; a `pre_aligned` image pair also includes the crop offset. The record
is compared with the current inputs for pairs involving ST, for `pre_aligned` pairs, and for pairs whose
target is a digested microscopy image against a fingerprinted reference; a mismatch re-runs the pair.
A manual alignment whose record was made on a file that preprocessing later replaced with a
pixel-identical image (the replaced file's signature is kept in the image as
`focus_superseded_signature`, see [microscopy preprocessing](microscopy_methods.md#8-cache-key-and-regeneration))
is accepted, and its record is re-written for the current image.

A guided pair records the identities of \(T\) and \(G\) and the reference → guide record. While the
stored guided warp was made on the current \(T\) and \(G\), the alignment tool is not reopened; if
only the reference → guide alignment changed, \(\mathbf{R}^{(T)}\) is re-composed from the stored warp.

`_compute_force_flags` in `orchestrator.py` re-runs a pair when any of the following is true:
`alignment_force_recomputing` on the non-reference modality, `processing_settings.force_recomputing`
on the reference modality, or `processing_settings.force_recomputing` on that non-reference modality.
The same disjunction is OR-ed into that modality's registration force flag.
