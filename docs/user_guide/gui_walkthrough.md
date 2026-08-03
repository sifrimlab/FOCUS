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

The first screen asks for the location of your data.

1. **Dataset path**: Use the filesystem browser in the GUI to navigate to your `dataset_path` directory (the folder that contains your sample subdirectories), or paste the absolute path directly into the text field.
2. **Config**: Choose one of:
    - **Create new config**: Start fresh. FOCUS scans the directory and auto-discovers sample IDs from the names of the first-level subdirectories.
    - **Load existing config**: If a `focus_config.json` already exists in the directory (from a previous run or a manual edit), load it to resume or modify it.

!!! tip "Auto-discovery of sample IDs"
    FOCUS infers sample identifiers from the names of the subdirectories directly under `dataset_path`. Review the list of discovered samples on this screen to confirm the directory structure is correct before proceeding.

---

## Stage 2: Configuration

The configuration stage is divided into panels. Work through them top to bottom. Changes are saved in real-time as you make them.

### 1. Modality Definitions

Add each modality that is present in your dataset:

- **Name**: The modality identifier. Must exactly match the subdirectory names inside your sample folders (case-sensitive).
- **Type**: Select from the dropdown: `microscopy_image`, `msi`, `raman`, or `st`.

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

- **Manual** (default): Interactive visual alignment via the alignment GUI. Requires the alignment GUI (see Stage 3 below).
- **Pre-aligned**: Skip the alignment GUI for this modality; assume the reference modality's coordinates are already expressed in the target modality's coordinate frame.

!!! info "When to use Pre-aligned"
    **Pre-aligned is applicable when:**
    - The reference modality is spot-based (`st` or `msi`). This is the constraint FOCUS enforces
    - The reference spots' coordinates are **already expressed in the target modality's coordinate frame** (most commonly an image's pixel coordinates, e.g. `microscopy_image`)
    
    **Example:** Your reference is a spatial transcriptomics (ST) dataset with spot coordinates that are already in the pixel frame of an H&E microscopy image (not in micrometers). In this case, you can select `alignment_strategy: "pre_aligned"` for the H&E target, and FOCUS will skip the manual alignment step, using the existing coordinates directly for registration.
    
    **If your spot coordinates are in micrometers or physical units**, you must use **Manual** alignment to establish the correspondence with the target modality's coordinate system.

### 5. Registration Settings

For each non-reference modality, select the registration type and fill in any additional settings:

- **None**: Align only; exclude from the final MuData.
- **Spot interpolation**: Gaussian-weighted spot interpolation (CPU). For `msi` and `st`.
- **Raman pixel interpolation**: the same Gaussian footprint interpolation applied to the hyperspectral OME-TIFF pixels (CPU). For `raman`.
- **Feature extraction**: Prov-GigaPath patch embeddings (GPU required). Only available for `microscopy_image`, and only appropriate when that image is an H&E-stained brightfield RGB section, which is what the model was pretrained on. For fluorescence, IHC or other stains, pick **None** instead: the GUI offers Feature extraction for every microscopy modality and nothing downstream checks the stain.

If any modality uses **Feature extraction**, a HuggingFace token field will appear at the top of the configuration panel.

### 6. Spatial Annotations (Optional)

If your samples include GeoJSON annotation files, expand this panel and fill in:

- **Annotation modality**: The `name` of the modality whose directory contains the `.geojson` files.
- **File type**: Select `geojson`.

Leave this section collapsed if you do not have annotation files.

### 7. Review Configuration

The configuration is automatically saved to `focus_config.json` in `<dataset_path>` every time you make a change. You can review the JSON configuration at any time by opening the file in a text editor, or you can proceed directly to running the pipeline.

---

## Stage 3: Running the Pipeline

Click **Start Processing** to run FOCUS with the current configuration. A live log panel streams output from the pipeline process in real time. Progress bars show the current stage and per-sample progress.

### Normal Progression

The pipeline advances automatically through:

1. Preprocessing (all modalities, all samples)
2. Alignment (pauses for user interaction, see below)
3. Registration (all non-reference modalities, all samples)
4. Compilation (merge into `multimodal_dataset.h5mu`)

### Alignment Stage: Visual Overlay

When the pipeline reaches the alignment stage, processing pauses and a banner appears in the main GUI:

> **Manual alignment required.** Click the button below to open the alignment tool.

**Step 1: Open the alignment GUI**

Click the **Open Alignment Tool** button in the banner. This opens the alignment GUI in a new browser tab at `localhost:8000`. You do not need to manually navigate to this URL. The button opens it automatically. The button remains in the banner while alignment is in progress. The banner covers one non-reference modality at a time, and reappears for the next one.

**Step 2: Perform alignment**

The alignment GUI draws both modalities overlaid in one viewport, with the control panel on the right. The reference modality is the layer on top and the one that moves; the target modality is fixed.

**For each sample of the modality being aligned:**

1. **Stay in Aligner mode**: the **Aligner** button (selected at start) makes the pointer act on the reference layer. **Camera** switches the pointer to panning and zooming the view.

2. **Move the reference modality** with the pointer:
   - **Translate**: drag inside the reference layer's frame
   - **Rotate**: drag just outside a corner, and the layer turns about its centre
   - **Scale**: the mouse wheel, which scales about the pointer
   - **Warp**: drag a corner handle to move that corner alone, or an edge handle to move its two corners together

   The panel adds **Flip Horizontal** / **Flip Vertical**, and **Scale** and **Rotation °** steppers if you prefer numeric control.

3. **Fine-tune**:
   - **Opacity** changes how strongly the reference layer covers the target
   - **Spot Classes** shows or hides individual clusters, and **Foreground** restricts a spot layer to foreground or background spots. Each spot layer has its own set
   - **View Zoom** inspects details without touching the transform
   - **Reset Distortion** undoes corner and edge drags only; **Reset Transform** returns the layer to its starting position

4. **Verify Coverage**: check the alignment across the entire tissue area, not just in one region.

5. **Confirm**: click **Confirm Alignment** at the bottom of the panel to save the transform and load the next sample.

**Step 3: Complete alignment**

Once the last sample of that modality is confirmed, the alignment tab shows a completion message and the pipeline resumes on its own. Closing the tab does not advance the pipeline. If the dataset has more than one non-reference modality, the banner reappears for the next one and **Open Alignment Tool** opens a fresh session at the same address.

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
