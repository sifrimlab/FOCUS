# Alignment Stage

## Overview

The alignment stage maps the reference modality's spot positions into the coordinate system of every non-reference (target) modality. After alignment, each reference spot has a known location in each target modality's space, enabling the subsequent registration stage to extract features at those locations.

Alignment is the only pipeline stage that requires human input: the user visually aligns the reference modality to each target modality through an interactive browser GUI using drag controls. This design choice makes FOCUS robust to heterogeneous tissue appearance and instrument-specific coordinate distortions that defeat automated feature-detection methods.

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
| image | image | reference cropped to the overlapping region, saved as OME-TIFF |
| image | spot | **not implemented** |

In normal use the reference is spot-based (required for MuData compilation), so the first two rows are the common cases — including the typical MSI/ST reference aligned against a microscopy or Raman image. An image-based reference paired with a spot target is the only combination that is not implemented.

---

## Alignment Strategies

### `manual` (default)

The interactive alignment GUI is launched in the browser. The user transforms the reference modality (as a whole) to align it visually with the fixed target modality, then clicks **Confirm**. The GUI advances automatically to the next sample. The reference layer can be translated, rotated, scaled, flipped (horizontally or vertically), and freely distorted by dragging individual corners (a perspective-style warp). The layer is always transformed as a whole — individual spots cannot be moved one at a time — but because corners can be dragged independently the warp is non-uniform across the field. Since the mapping is free-form rather than a fixed parametric transform, FOCUS reads the resulting mapped coordinates back directly; it does not fit a rigid or affine matrix.

**Use when:** the two modalities were acquired on different instruments or at different times, i.e., they have independent coordinate systems. This is the typical case for MSI + microscopy, Raman + MSI, or any cross-instrument combination.

### `pre_aligned`

No GUI is launched. The pipeline copies `obsm['spatial']` from the **reference** AnnData into `obsm['{target_name}_spatial']` in the same reference AnnData, recording that the reference spots are already expressed in the target's coordinate frame. This is appropriate when the reference modality is spot-based and its spot coordinates are already in the target modality's coordinate system — for example, Visium ST spots whose coordinates are already in H&E microscopy image pixel coordinates.

**Use when:** the reference modality is spot-based (`msi` or `st`) and its `obsm['spatial']` coordinates are already expressed in one target modality's coordinate frame.

**Limitation:** only one target modality can use `pre_aligned`, since the reference spots can be expressed in only one coordinate system at a time. If there are two or more targets, all but one must use `manual` alignment.

!!! warning
    `pre_aligned` constrains the **reference** modality, not the target. The reference must be spot-based (`msi` or `st`), because its `obsm['spatial']` coordinates are the ones reused. The **target may be any modality type** (`msi`, `st`, `microscopy_image`, or `raman`), as long as the reference spots are already expressed in that target's coordinate frame.

---

## The Alignment GUI

### Launching

The GUI starts automatically when the pipeline reaches a sample that requires manual alignment. Open `http://localhost:8000` in any modern browser. The main FOCUS pipeline GUI (port 5050) will display a prompt indicating that alignment is waiting.

The GUI shuts down automatically once all samples for the current modality pair are confirmed.

### Interface layout

The alignment GUI is organized into two main sections:

**Left Section: Modality Display**
- Shows both the reference and target modalities overlaid, one on top of the other
- The reference modality is overlaid on top of the target modality
- Reference can be moved (via transformation controls); target is fixed and defines the coordinate space
- For image modalities, the lowest-resolution pyramid level is loaded for responsive rendering
- For spot modalities, spots are colour‑coded by Leiden cluster to help identify tissue regions

**Right Panel: Control Tools**
- Switch between **Camera Control** (pan/zoom the view) and **Transformation Control** (translate, rotate, scale, flip, or corner-distort the reference)
- In Camera mode the mouse moves the point‑of‑view without affecting the transformation
- In Transformation mode the mouse drags translate the reference and the mouse wheel changes the scale; rotation, horizontal/vertical flip, and per-corner distortion are applied through the panel controls
- Reset the transformation to its original state
- Show/hide specific spot clusters (for spot‑based modalities)
- Confirm alignment button to save the transform

## Output

### Per-sample aligned files

The aligned outputs are written on the **reference** modality, named after it. The file type depends on the reference modality type.

For a **spot-based reference** (`msi`, `st`) — the normal case — alignment produces one accumulating AnnData per sample:

```
{dataset_path}/{sample_id}/alignment/{ref_name}_{sample_id}_processed_aligned.h5ad
```

This file is built from the reference's preprocessed AnnData, so its own `obsm['spatial']` is preserved. For each target it is aligned against, a new key `obsm['{target_name}_spatial']` is added, containing the reference spot coordinates expressed in that target modality's space.

For an **image-based reference** aligned against an image target (the rare image→image case), the reference is cropped to the overlapping region and saved as a new OME-TIFF instead:

```
{dataset_path}/{sample_id}/alignment/{ref_name}_{sample_id}_processed_aligned.ome.tiff
```

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

Set `perform_alignment: false` to skip the alignment stage entirely. Skipping alignment means that no aligned coordinate files are produced, so the subsequent registration stage will not execute because it depends on those aligned coordinates. You should only skip alignment if the registration stage can run without them, for example when all non‑reference modalities already share a common coordinate system or when you are performing only preprocessing.

---

## Caching and Re-running

FOCUS checks whether the expected `obsm` key is present in the aligned file before launching the GUI. If all samples for a given modality pair are already aligned, the GUI is skipped and the pipeline proceeds immediately.

To redo specific alignments, either:
- Delete the per-sample aligned file(s) for the affected modality and sample(s), or
- Set `alignment_force_recomputing: true` on the specific modality entry to redo that pair's alignment.
