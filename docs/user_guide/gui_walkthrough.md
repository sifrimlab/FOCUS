# GUI Walkthrough

The FOCUS GUI is a web application served locally by Flask. It guides you through configuring and running the full pipeline without writing any code or JSON. This page walks through every stage of the GUI from launch to completion.

---

## Launching the GUI

Activate the FOCUS environment and run `focus` with no arguments:

```bash
conda activate FOCUS
focus
```

FOCUS starts a local web server and prints the address:

```
FOCUS GUI started. Open http://localhost:5050 in your browser.
```

Your browser may open automatically. If it does not, navigate to [http://localhost:5050](http://localhost:5050) manually.

!!! note "Port 5050 must be free"
    If another process is already using port 5050, the server will fail to start. Stop the conflicting process, or use the container launcher with a custom port mapping if needed.

---

## Stage 1: Setup

The first screen asks where your data lives.

1. **Dataset path** — Use the filesystem browser in the GUI to navigate to your `dataset_path` directory (the folder that contains your sample subdirectories), or paste the absolute path directly into the text field.
2. **Config** — Choose one of:
    - **Create new config** — Start fresh. FOCUS will scan the directory and auto-discover sample IDs from the names of the first-level subdirectories.
    - **Load existing config** — If a `focus_config.json` already exists in the directory (from a previous run or a manual edit), load it to resume or modify it.

!!! tip "Auto-discovery of sample IDs"
    FOCUS infers sample identifiers from the names of the subdirectories directly under `dataset_path`. Review the list of discovered samples on this screen to confirm the directory structure is correct before proceeding.

---

## Stage 2: Configuration

The configuration stage is divided into panels. Work through them top to bottom. Changes are saved in real-time as you make them.

### 1. Modality Definitions

Add each modality that is present in your dataset:

- **Name** — The modality identifier. Must exactly match the subdirectory names inside your sample folders (case-sensitive).
- **Type** — Select from the dropdown: `microscopy_image`, `msi`, `raman`, or `st`.

Add as many modalities as your dataset contains. Use the remove button to delete entries you added by mistake.

### 2. Reference Modality

Select which of the declared modalities defines the master coordinate system. All other modalities will be aligned and registered onto this coordinate space.

### 3. Processing Settings

Each modality has its own processing settings panel that appears after you define the modality type. The GUI shows the most commonly adjusted parameters with their defaults pre-filled. Hover over any field label to see a description.

Key defaults to review:

| Modality | Parameter | Default |
|----------|-----------|---------|
| `st` | `min_count_per_spot` | `null` (disabled) |
| `st` | `total_counts_normalize` | `false` |
| `st` | `log1p_transform` | `false` |
| `msi` | `mass_tolerance` | `10` ppm |
| `msi` | `intensity_normalization` | `"none"` |
| `microscopy_image` | `gamma` | `0.45` |

### 4. Alignment Settings

For each non-reference modality, select the alignment strategy:

- **Manual** (default) — Interactive visual alignment via the alignment GUI. Requires the alignment GUI (see Stage 3 below).
- **Pre-aligned** — Skip the alignment GUI for this modality; assume the reference modality's coordinates are already expressed in the target modality's coordinate frame.

!!! info "When to use Pre-aligned"
    **Pre-aligned is applicable when:**
    - The reference modality is spot-based (`st` or `msi`) — this is the constraint FOCUS enforces
    - The reference spots' coordinates are **already expressed in the target modality's coordinate frame** (most commonly an image's pixel coordinates, e.g. `microscopy_image`)
    
    **Example:** Your reference is a spatial transcriptomics (ST) dataset with spot coordinates that are already in the pixel frame of an H&E microscopy image (not in micrometers). In this case, you can select `alignment_strategy: "pre_aligned"` for the H&E target, and FOCUS will skip the manual alignment step, using the existing coordinates directly for registration.
    
    **If your spot coordinates are in micrometers or physical units**, you must use **Manual** alignment to establish the correspondence with the target modality's coordinate system.

### 5. Registration Settings

For each non-reference modality, select the registration type and fill in any additional settings:

- **None** — Align only; exclude from the final MuData.
- **Spot interpolation** — Gaussian-weighted spot interpolation (CPU). For `msi` and `st`.
- **Raman pixel interpolation** — the same Gaussian footprint interpolation applied to the hyperspectral OME-TIFF pixels (CPU). For `raman`.
- **Feature extraction** — Prov-GigaPath patch embeddings (GPU required). Only available for `microscopy_image`.

If any modality uses **Feature extraction**, a HuggingFace token field will appear at the top of the configuration panel.

### 6. Spatial Annotations (Optional)

If your samples include GeoJSON annotation files, expand this panel and fill in:

- **Annotation modality** — The `name` of the modality whose directory contains the `.geojson` files.
- **File type** — Select `geojson`.

Leave this section collapsed if you do not have annotation files.

### 7. Review Configuration

The configuration is automatically saved to `focus_config.json` in `<dataset_path>` every time you make a change. You can review the JSON configuration at any time by opening the file in a text editor, or you can proceed directly to running the pipeline.

---

## Stage 3: Running the Pipeline

Click **Start Processing** to run FOCUS with the current configuration. A live log panel streams output from the pipeline process in real time. Progress bars show the current stage and per-sample progress.

### Normal Progression

The pipeline advances automatically through:

1. Preprocessing (all modalities, all samples)
2. Alignment (pauses for user interaction — see below)
3. Registration (all non-reference modalities, all samples)
4. Compilation (merge into `multimodal_dataset.h5mu`)

### Alignment Stage: Visual Overlay

When the pipeline reaches the alignment stage, processing pauses and a banner appears in the main GUI:

> **Manual alignment required.** Click the button below to open the alignment tool.

**Step 1: Open the alignment GUI**

Click the **Open Alignment Tool** button in the banner. This opens the alignment GUI in a new browser tab at `localhost:8000`. You do not need to manually navigate to this URL—the button opens it automatically. The button remains in the banner while alignment is in progress.

**Step 2: Perform alignment**

The alignment GUI displays the reference and target modalities side by side in the left section, with transformation controls in the right panel.

**For each sample and each non-reference modality:**

1. **Switch to Alignment Control Mode**: Select "Alignment Control" from the center controls to enable transformation tools.

2. **Move the Reference Modality**: The left panel shows the reference modality overlaid on the target modality (right panel, fixed). Use the transformation controls to align them:
   - **Translate**: Click and drag the reference modality to move it across the target
   - **Rotate**: Use the rotation control to rotate the reference around its centroid
   - **Scale**: Scroll the mouse wheel to scale the reference relative to the target
   - **Flip**: Mirror the reference horizontally or vertically
   - **Corner distortion**: Drag an individual corner (the others stay fixed) to apply a free-form, perspective-style warp

3. **Fine-Tune the Alignment**:
   - Use the Camera Control mode to zoom and pan for detailed inspection
   - Show/hide spot clusters (for spot-based modalities) to verify correspondence
   - Reset the alignment at any time to start over

4. **Verify Coverage**: Ensure the alignment is accurate across the entire tissue area, not just in one region.

5. **Confirm**: Click **Confirm Alignment** in the right control panel to save the transform and advance to the next sample or modality.

**Step 3: Complete alignment**

Once all samples and modalities have been submitted, a **green message** appears in the alignment tab indicating that alignment is complete. You can now close the alignment tab. The main GUI automatically resumes processing—no action is needed.

!!! tip "Getting accurate alignment"
    Use anatomically distinctive features as visual references: tissue edges, blood vessels, branching structures, distinctive cell clusters, or staining artefacts. Make adjustments distributed across the full tissue area rather than clustering them in one region. This distributed approach produces more accurate transforms than concentrated adjustments.

---

## Stage 4: Complete

When the full pipeline finishes, the GUI displays a completion summary organized by category:

- **Preprocessing**: Output files from each modality's preprocessing step
- **Alignment**: Aligned output files (if alignment was performed)
- **Registration**: Registered feature matrices (if registration was performed)
- **Final Output**: The merged MuData file (if applicable) and other final results

The summary includes all output files created during processing.

### Action buttons

**Start New Run**
: Returns to the dataset path selection screen (Stage 1) to configure and run a new pipeline on the same or different dataset.

**Delete Temporary Files**
: Removes per-sample output files from each processing stage (preprocessing, alignment, registration). This reduces disk storage while preserving the final merged results. Useful for cleanup after confirming outputs are correct and acceptable quality.

The pipeline can be re-run from the same config at any time. With `force_recomputing: false` (the default), stages whose outputs already exist will be skipped, making re-runs fast when only a subset of settings have changed.

---

## Tips and Troubleshooting

!!! tip "Running GUI and CLI together"
    After configuring FOCUS in the GUI, the config is automatically saved as `focus_config.json`. You can then run the same config non-interactively from the command line. This is useful for reprocessing on an HPC node without a display, provided you use `alignment_strategy: "pre_aligned"` or already have aligned outputs.

!!! tip "Using alternative config file names"
    The GUI auto-saves to `focus_config.json`. If you want to keep multiple configurations, manually copy `focus_config.json` to different names (e.g., `focus_config_st_only.json`, `focus_config_full.json`) and then load the desired config from the Setup screen using `--config` flag in CLI mode.

!!! warning "Do not run two FOCUS processes on the same dataset_path simultaneously"
    Concurrent writes to the same output files will corrupt results. Run one FOCUS process at a time per dataset.
