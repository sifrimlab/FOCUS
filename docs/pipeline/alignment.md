# Alignment Stage

## Overview

The alignment stage maps the reference modality's spot positions into the coordinate system of every non-reference (target) modality. After alignment, each reference spot has a known location in each target modality's space, which the registration stage uses to extract features at those locations.

Alignment is the only pipeline stage that requires human input: you align the reference modality to each target modality in an interactive browser GUI.

!!! abstract "Scientific background"
    For a rigorous description of the coordinate mathematics, scale recovery, and direct-mapping approach, see [Alignment Methods](../scientific/alignment_methods.md).

---

## What is the reference modality?

One modality in the configuration is designated the **reference** (via `reference_modality` in `config.yaml`). Its spot grid defines the canonical observation index that all other modalities are mapped to. The reference modality must be spot-based (`msi` or `st`) for MuData compilation to work.

Every other modality is a **target**. The alignment stage produces, for each target, the reference spots expressed in that target's coordinate frame.

---

## Stage Workflow

Alignment runs once per non-reference modality, and within each modality once per sample. The pipeline iterates as follows:

```
for each non-reference modality T:
    for each sample present in both reference and T:
        if cached alignment exists → skip
        else → launch GUI for this (sample, T) pair
    save results and build merged aligned file
```

Samples that are present in the reference but not in target $T$ (or vice versa) are silently skipped for that pair.

---

## Supported alignment directions

The reference is the moving layer and the target is the fixed frame. The combinations FOCUS can align depend on the modality types of the two:

| Reference (moving) | Target (fixed) | Result |
|--------------------|----------------|--------|
| spot (`msi`/`st`) | spot (`msi`/`st`) | `obsm['{target}_spatial']` written to the reference's aligned `.h5ad` |
| spot (`msi`/`st`) | image (`microscopy_image`/`raman`) | `obsm['{target}_spatial']` written to the reference's aligned `.h5ad` |
| image | image | target cropped to the region the reference covers, saved as OME-TIFF |
| image | spot | **not supported**, rejected during configuration validation |

In normal use the reference is spot-based (required for MuData compilation), so the first two rows are the common cases, including the typical MSI/ST reference aligned against a microscopy or Raman image.

!!! warning "An image reference cannot be aligned to a spot modality"
    Configuration validation rejects an image-based `reference_modality` when any non-reference modality is spot-based, before any stage runs:

    ```
    Reference modality 'microscopy' has image-based type 'microscopy_image', which cannot be
    aligned to the spot-based modality/modalities ['msi']. …
    ```

    Set `reference_modality` to an `msi` or `st` modality. An image reference is accepted only when every other modality is also image-based, and then it supports alignment only. Registration reads the aligned reference as AnnData, so its non-reference modalities need `registration_type: none`.

---

## Alignment Strategies

### `manual` (default)

The interactive alignment GUI is launched in the browser. Transform the reference modality until it overlaps the fixed target modality, then click **Confirm Alignment**; the GUI advances to the next sample. The reference layer is transformed as a whole, through translation, rotation, scaling, horizontal/vertical flip, and corner or edge dragging. Individual spots cannot be moved one at a time.

The transform is recorded as a 3×3 matrix, not as a list of moved points. The GUI holds one matrix per layer and posts their combination, `inverse(reference_layer) · target_layer`, as a column-major `gl-matrix` `mat3`. Dragging a corner or an edge recomputes the moving layer's matrix as a **homography** fitted to the four corner correspondences, so the mapping is projective rather than affine. FOCUS applies that matrix to every spot of the full dataset in homogeneous coordinates and divides through by `w`.

**Use when:** the two modalities were acquired on different instruments or at different times, i.e., they have independent coordinate systems. This is the typical case for MSI + microscopy, Raman + MSI, or any cross-instrument combination.

### `pre_aligned`

No GUI is launched. The pipeline copies `obsm['spatial']` from the **reference** AnnData into `obsm['{target_name}_spatial']` in the same reference AnnData, recording that the reference spots are already expressed in the target's coordinate frame. This is appropriate when the reference modality is spot-based and its spot coordinates are already in the target modality's coordinate system. One example is Visium ST spots whose coordinates are already in H&E microscopy image pixel coordinates.

**Use when:** the reference modality is spot-based (`msi` or `st`) and its `obsm['spatial']` coordinates are already expressed in one target modality's coordinate frame.

**Limitation:** only one target modality can use `pre_aligned`, since the reference spots can be expressed in only one coordinate system at a time. If there are two or more targets, all but one must use `manual` alignment.

!!! warning
    `pre_aligned` constrains the **reference** modality, not the target. The reference must be spot-based (`msi` or `st`), because its `obsm['spatial']` coordinates are the ones reused. The **target may be any modality type** (`msi`, `st`, `microscopy_image`, or `raman`), as long as the reference spots are already expressed in that target's coordinate frame.

---

## The Alignment GUI

### Launching

The GUI starts when the pipeline reaches a modality pair that requires manual alignment. Open `http://localhost:8000` in any modern browser. The main FOCUS pipeline GUI (port 5050) displays a prompt indicating that alignment is waiting.

One GUI session runs per non-reference modality, each covering that pair's samples in sequence and all served on port 8000. A session shuts down about 2 seconds after its last sample is confirmed (60 seconds when the alignment thread reported an error, so the error screen stays readable), and the pipeline resumes at that point. Closing the browser tab does not advance the pipeline.

### Interface layout

The window is split into a display viewport (80% of the width) and a control panel (20%).

**Display viewport**
- Both modalities are drawn in the same viewport, the reference layer on top of the target layer
- The reference layer moves; the target layer is fixed and defines the coordinate space
- For image modalities, the lowest pyramid level of the OME-TIFF is displayed
- For spot modalities, spots are coloured by their `obs['cluster']` label
- Above 100,000 spots the display is coarsened: spots are aggregated onto a uniform grid of at most 100,000 bins and one marker per occupied bin is drawn at the bin centre, sized to the grid pitch. This affects the display only. The confirmed transform is applied to every original spot

**Control panel**, top to bottom:

| Control | Effect |
|---------|--------|
| **Aligner** / **Camera** | Aligner: the pointer manipulates the reference layer. Camera: the pointer pans and zooms the view without changing the transform |
| **Flip Horizontal**, **Flip Vertical** | Mirror the reference layer |
| **Scale** −/+, **Reset** | Scale the reference layer |
| **Rotation °** −/+, **Reset** | Rotate the reference layer |
| **Reset Distortion** | Undo corner and edge dragging, keeping the rest of the transform |
| **Reset Transform** | Return the reference layer to its initial position |
| **Opacity** | Opacity of the reference layer (0.7 at start) |
| **Spot Classes** (All / None) | Show or hide individual cluster labels; available for each spot-based layer |
| **Foreground** (All / FG / BG) | Restrict a spot layer to foreground or background spots; available for each spot-based layer |
| **View Zoom** −/+, **Reset** | Zoom the viewport |
| **Confirm Alignment** | Save the transform and load the next sample |

Each layer has its own panel section headed by that modality's name and type.

### Pointer controls in Aligner mode

| Gesture | Effect |
|---------|--------|
| Drag inside the frame | Translate the reference layer |
| Drag a corner handle | Move that corner alone, warping the layer (this is what makes the transform projective) |
| Drag an edge handle | Move the two corners of that edge together |
| Drag just outside a corner | Rotate the reference layer about its centre (the image centre, or the bounding-box centre of the spots) |
| Mouse wheel | Scale the reference layer about the pointer |

In Camera mode the mouse wheel is view zoom and the transform is untouched.

## Output

### Per-sample aligned files

For a **spot-based reference** (`msi`, `st`), which is the normal case, alignment produces one accumulating AnnData per sample, named after the reference modality:

```
{dataset_path}/{sample_id}/alignment/{ref_name}_{sample_id}_processed_aligned.h5ad
```

This file is built from the reference's preprocessed AnnData, so its own `obsm['spatial']` is preserved. For each target it is aligned against, a new key `obsm['{target_name}_spatial']` is added, containing the reference spot coordinates expressed in that target modality's space.

For an **image-based reference** aligned against an image target, the **target** image is cropped to the region the reference image covers and written as an OME-TIFF (zlib-compressed), named after the target modality:

```
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.ome.tiff
```

No merged file is produced in this case.

### Merged aligned file

After all per-sample alignments are complete, the per-sample AnnData files are concatenated:

```
{dataset_path}/merged/alignment/{ref_name}_merged_processed_aligned.h5ad
```

This merged file is the input to the registration stage.

### obsm key accumulation

If the reference is aligned against multiple targets sequentially, all `obsm` keys accumulate in the same reference AnnData. For example, aligning reference `st` against both `msi` and `microscopy` produces an `st` aligned AnnData that contains both `obsm['msi_spatial']` and `obsm['microscopy_spatial']`.

---

## Config Fields Relevant to Alignment

```yaml
reference_modality: st          # name of the reference modality
perform_alignment: true         # set to false to skip this stage entirely

modalities:
  - name: msi
    type: msi
    alignment_strategy: manual  # or pre_aligned
    alignment_force_recomputing: false  # set to true to redo this pair's alignment
    ...
  - name: microscopy
    type: microscopy_image
    alignment_strategy: manual
    alignment_force_recomputing: false
    ...
```

| Field | Scope | Default | Description |
|-------|-------|---------|-------------|
| `perform_alignment` | global | `true` | Whether to run the alignment stage |
| `alignment_strategy` | per modality | `manual` | `manual` or `pre_aligned` |
| `alignment_force_recomputing` | per modality | `false` | Re-run even if cached output exists for this pair |

---

## Skipping Alignment

Set `perform_alignment: false` to run preprocessing only. No aligned coordinate files are produced. `perform_registration` must then be `false` as well: the combination `perform_registration: true` with `perform_alignment: false` is rejected during configuration validation.

Transferring spatial annotations from a non-reference modality also requires `perform_alignment: true`.

---

## Caching and Re-running

Before launching the GUI, FOCUS checks each sample of the pair for an existing aligned output. A sample counts as aligned when:

- **spot reference**: the aligned `.h5ad` exists **and** already contains `obsm['{target_name}_spatial']`;
- **image reference**: the aligned `.ome.tiff` exists.

If every sample of the pair passes, the GUI is skipped. When the per-sample files are present but the merged file is missing, FOCUS rebuilds the merged file without opening the GUI.

A pair is re-aligned when **any** of these is `true`:

- `alignment_force_recomputing` on that non-reference modality;
- `processing_settings.force_recomputing` on the **reference** modality;
- `processing_settings.force_recomputing` on that **non-reference** modality.

The same condition also forces that modality's registration, so re-running preprocessing with `force_recomputing: true` re-opens the alignment GUI and recomputes registration for the affected pairs.

To redo one alignment without touching preprocessing, set `alignment_force_recomputing: true` on that modality entry, or delete its per-sample aligned file(s).
