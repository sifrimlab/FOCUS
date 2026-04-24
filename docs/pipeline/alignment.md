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

The interactive alignment GUI is launched in the browser. The user drags the reference modality or its individual spots to align them visually with the target modality, then clicks **Confirm**. The GUI advances automatically to the next sample.

**Use when:** the two modalities were acquired on different instruments or at different times, i.e., they have independent coordinate systems. This is the typical case for MSI + microscopy, Raman + MSI, or any cross-instrument combination.

### `pre_aligned`

No GUI is launched. The pipeline copies `obsm['spatial']` from the target AnnData directly into `obsm['{ref_name}_spatial']`, recording that the two modalities share a coordinate system.

**Use when:** the target modality's coordinates are already expressed in the same frame as the reference — for example, a 10x Visium dataset where the ST spots and the H&E image are co-registered by the instrument software.

!!! warning
    `pre_aligned` is only valid for spot-type targets (`msi`, `st`). Using it for image modalities will produce incorrect registration results.

---

## The Alignment GUI

### Launching

The GUI starts automatically when the pipeline reaches a sample that requires manual alignment. Open `http://localhost:8000` in any modern browser. The main FOCUS pipeline GUI (port 5050) will display a prompt indicating that alignment is waiting.

The GUI shuts down automatically once all samples for the current modality pair are confirmed.

### Interface layout

The alignment GUI is organized into three main sections:

**Left Panel: Modality Display**
- Shows both the reference and target modalities overlaid together
- The reference modality is overlaid on top of the target modality
- Reference can be moved (via transformation controls); target is fixed and defines the coordinate space
- For image modalities, the lowest-resolution pyramid level is loaded for responsive rendering
- For spot modalities, spots are colour-coded by Leiden cluster to help identify tissue regions

**Center: Control Mode Selector**
- Switch between **Camera Control** (pan/zoom to inspect) and **Alignment Control** (transform reference)

**Right Panel: Control Tools**
- Transformation controls: translation (drag), rotation (around centroid), scale (scroll)
- Show/hide specific spot clusters (for spot-based modalities)
- Fine-tune transformation parameters
- Reset alignment to original state
- Confirm alignment button to save the transform

### Alignment modes

In all modes, the reference modality (left panel) is moved to align with the target modality (right panel, fixed). Transformations are applied only to the reference.

**Image-to-Image** (reference is `microscopy_image` or `raman`, target is `microscopy_image` or `raman`)
: Use translation, rotation, and scaling controls to overlay the reference image onto the target image. The target image defines the coordinate space. After confirmation, the reference image is cropped and registered to match the target's bounds.

**Spot-to-Image** (reference is `msi` or `st`, target is `microscopy_image` or `raman`)
: Use transformation controls to position the reference spots (as a group) to match the target image anatomy. The target image defines the coordinate space, and anatomical features (vessels, tissue boundaries, cell clusters) serve as visual guides. After confirmation, each reference spot's position in the target image coordinate frame is recorded.

**Spot-to-Spot** (reference is `msi` or `st`, target is `msi` or `st`)
: Use transformation controls to overlay the reference spot grid onto the target spot grid. The target spots define the coordinate space. After confirmation, each reference spot's position in the target spot coordinate frame is recorded.

### Alignment tips

- Start with the most recognisable tissue structures (large vessels, tissue boundaries, fold edges) to establish the gross alignment.
- Distribute your adjustments across the full tissue section — do not cluster corrections in one region.
- Zoom in for fine-grained adjustment around challenging areas.
- After repositioning, review the overall distribution before clicking **Confirm**.

### Submit flow

Click **Confirm** to record the current alignment for the displayed sample. The GUI immediately loads the next sample. Once all samples are processed, the GUI closes and the pipeline resumes saving results.

---

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

Set `perform_alignment: false` to skip the stage entirely. In this case, the registration stage will not receive aligned coordinate files and will fall back to using `obsm['spatial']` from the preprocessed files. Only do this if all non-reference modalities are pre-aligned (share the reference coordinate system), or if you are only running preprocessing.

---

## Caching and Re-running

FOCUS checks whether the expected `obsm` key is present in the aligned file before launching the GUI. If all samples for a given modality pair are already aligned, the GUI is skipped and the pipeline proceeds immediately.

To redo specific alignments, either:
- Delete the per-sample aligned file(s) for the affected modality and sample(s), or
- Set `alignment_force_recomputing: true` in the config to redo all alignments.
