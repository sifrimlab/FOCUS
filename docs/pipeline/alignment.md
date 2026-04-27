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

## Alignment Strategies

### `manual` (default)

The interactive alignment GUI is launched in the browser. The user drags the reference modality (as a whole) to align it visually with the target modality, then clicks **Confirm**. The GUI advances automatically to the next sample. Note: the transformation is rigid, so the reference modality can be translated, rotated, or scaled as a whole, but individual spots cannot be moved independently.

**Use when:** the two modalities were acquired on different instruments or at different times, i.e., they have independent coordinate systems. This is the typical case for MSI + microscopy, Raman + MSI, or any cross-instrument combination.

### `pre_aligned`

No GUI is launched. The pipeline copies `obsm['spatial']` from the **reference** AnnData into `obsm['{target_name}_spatial']` in the same reference AnnData, recording that the reference spots are already expressed in the target's coordinate frame. This is appropriate when the reference modality is spot-based and its spot coordinates are already in the target modality's coordinate system — for example, Visium ST spots whose coordinates are already in H&E microscopy image pixel coordinates.

**Use when:** the reference modality is spot-based (`msi` or `st`) and its `obsm['spatial']` coordinates are already expressed in one target modality's coordinate frame.

**Limitation:** only one target modality can use `pre_aligned`, since the reference spots can be expressed in only one coordinate system at a time. If there are two or more targets, all but one must use `manual` alignment.

!!! warning
    Pre-Alignment can only be used for spot-based modalities; it is not supported for image-based modalities.

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
- Switch between **Camera Control** (pan/zoom the view) and **Transformation Control** (translate, rotate, or scale the reference)
- In Camera mode the mouse moves the point‑of‑view without affecting the transformation
- In Transformation mode the mouse drags translate the reference; the mouse wheel changes the scaling component
- Reset the transformation to its original state
- Show/hide specific spot clusters (for spot‑based modalities)
- Confirm alignment button to save the transform

## Output

### Per-sample aligned files

For **spot targets** (`msi`, `st`): the per-sample target AnnData is updated with a new obsm key:

```
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.h5ad
```

The key `obsm['{ref_name}_spatial']` contains the reference spot coordinates expressed in the target modality's space. The target's own `obsm['spatial']` is preserved unchanged.

For **image targets** (`microscopy_image`, `raman`): the reference image is cropped to the user-defined bounding box and saved as a new OME-TIFF:

```
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.ome.tiff
```

### Merged aligned file

After all per-sample alignments are complete, the per-sample AnnData files are concatenated:

```
{dataset_path}/merged/alignment/{target_name}_merged_processed_aligned.h5ad
```

This merged file is the input to the registration stage.

### obsm key accumulation

If the reference is aligned against multiple targets sequentially, all `obsm` keys are accumulated in the same target AnnData. For example, aligning reference `st` against both `msi` and `microscopy` produces a `st` aligned AnnData that contains both `obsm['msi_spatial']` and `obsm['microscopy_spatial']`.

---

## Config Fields Relevant to Alignment

```yaml
reference_modality: st          # name of the reference modality
perform_alignment: true         # set to false to skip this stage entirely
alignment_force_recomputing: false  # set to true to redo all alignments

modalities:
  - name: msi
    type: msi
    alignment_strategy: manual  # or pre_aligned
    ...
  - name: microscopy
    type: microscopy_image
    alignment_strategy: manual
    ...
```

| Field | Scope | Default | Description |
|-------|-------|---------|-------------|
| `perform_alignment` | global | `true` | Whether to run the alignment stage |
| `alignment_force_recomputing` | global | `false` | Re-run even if cached output exists |
| `alignment_strategy` | per modality | `manual` | `manual` or `pre_aligned` |

---

## Skipping Alignment

Set `perform_alignment: false` to skip the alignment stage entirely. Skipping alignment means that no aligned coordinate files are produced, so the subsequent registration stage will not execute because it depends on those aligned coordinates. You should only skip alignment if the registration stage can run without them, for example when all non‑reference modalities already share a common coordinate system or when you are performing only preprocessing.

---

## Caching and Re-running

FOCUS checks whether the expected `obsm` key is present in the aligned file before launching the GUI. If all samples for a given modality pair are already aligned, the GUI is skipped and the pipeline proceeds immediately.

To redo specific alignments, either:
- Delete the per-sample aligned file(s) for the affected modality and sample(s), or
- Set `alignment_force_recomputing: true` in the config to redo all alignments.
