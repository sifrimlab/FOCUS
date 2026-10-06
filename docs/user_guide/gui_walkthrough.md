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
2. **Samples**: FOCUS auto-discovers sample IDs from the names of the first-level subdirectories. Confirm that the list matches your dataset.
3. **Existing config**: If a `focus_config.json` already exists in the directory (from a previous run or a manual edit), choose **Load existing config** to resume or modify it, or **Start fresh** to replace it. If the file cannot be read, FOCUS lists the errors; you can go back and repair the file, or start fresh.
4. **Starting config**: Shown when the directory has no config, or after **Start fresh**. Choose one of:
    - **Upload a config file**: Drag and drop a `focus_config.json` onto the drop area, or click it to browse. A summary of the file (modalities, reference, enabled steps, disabled samples) is shown before anything is applied. Click **Use this config** to load it. The `dataset_path` stored in the file is replaced by the folder selected in step 1. If the file fails validation, the errors are listed and nothing is applied. Validation requires every sample directory to contain a subdirectory for each modality defined in the file.
    - **Start with an empty config**: Build a new configuration from scratch.

    An existing or corrupted `focus_config.json` is replaced only after one of these two choices.

!!! tip "Auto-discovery of sample IDs"
    FOCUS infers sample identifiers from the names of the subdirectories directly under `dataset_path`. Review the list of discovered samples on this screen to confirm the directory structure is correct before proceeding.

---

## Stage 2: Configuration

The configuration builder guides you through four steps. The step indicator at the top shows where you are; completed steps can be reopened at any time by selecting them. The **Back** and **Next** buttons at the bottom move one step at a time, and **Next** stays disabled, with the reason shown next to it, until the current step is complete. Changes are saved to `focus_config.json` in `<dataset_path>` as you make them.

The header above the step indicator shows the dataset in use. Its menu (**⋯**) offers **Change dataset**, which returns to the Setup screen, and **Reset configuration**, which clears the configuration after a confirmation.

When a configuration already exists (an existing `focus_config.json` that you load, an uploaded configuration file, or a session restored after a page reload), the builder opens directly on **Review** with every step unlocked.

### 1. Samples

Select which samples take part in the run. Each sample is a tile; select it to include or exclude it. Excluded samples are skipped entirely. The filter field narrows the tiles by name, and **Include all** / **Exclude all** apply to the tiles currently shown. At least one sample must be included.

**Add sample** creates a new sample folder in the dataset. A new folder is empty: populate it with data files before starting processing.

### 2. Modalities

Add each modality present in your dataset, one row per modality:

- **Name**: The modality identifier. Must exactly match the subdirectory names inside your sample folders (case-sensitive).
- **Type**: `Microscopy Image`, `MSI`, `Raman` or `Spatial Transcriptomics`.
- **Reference**: The modality that defines the master coordinate system. All other modalities are aligned and registered onto this coordinate space. The first modality you add becomes the reference until you choose another one.

Changing the type of a modality resets its settings to the defaults of the new type, and making a modality the reference clears its registration settings, because the reference is never registered. In both cases the GUI asks for confirmation first.

Below the list, **Pipeline steps** turns **Alignment** and **Registration** on or off for the whole run. Registration requires alignment, and both require at least two modalities.

### 3. Modality settings

The settings of one modality are shown at a time. The tabs at the top list every modality: the reference is marked with a star, and a check marks the modalities you have already reviewed. **Next** moves to the following modality, and after the last one to **Review**. The modality type cannot be changed here; change it in the Modalities step.

Each modality has up to three sections:

**1. Preprocessing.** The parameters of the modality type, with their defaults pre-filled.

Key defaults to review:

| Modality | Parameter | Default |
|----------|-----------|---------|
| `st` | `min_count_per_spot` | `null` (disabled) |
| `st` | `total_counts_normalize` | `false` |
| `st` | `log1p_transform` | `false` |
| `msi` | `mass_tolerance` | `10` ppm |
| `msi` | `intensity_normalization` | `"none"` |
| `microscopy_image` | `gamma` | `0.45` |

**2. Alignment** (non-reference modalities only). How the reference is placed in the coordinate space of this modality. When the reference is spot-based, choose the strategy:

- **Manual** (default): Interactive visual alignment via the alignment GUI (see Stage 3 below).
- **Pre-aligned**: Skip the alignment GUI for this modality; assume the reference modality's coordinates are already expressed in the target modality's coordinate frame.

For other references, alignment is always manual and the section has no settings.

!!! info "When to use Pre-aligned"
    **Pre-aligned is applicable when:**
    - The reference modality is spot-based (`st` or `msi`). This is the constraint FOCUS enforces
    - The reference spots' coordinates are **already expressed in the target modality's coordinate frame** (most commonly an image's pixel coordinates, e.g. `microscopy_image`)
    
    **Example:** Your reference is a spatial transcriptomics (ST) dataset with spot coordinates that are already in the pixel frame of an H&E microscopy image (not in micrometers). In this case, you can select `alignment_strategy: "pre_aligned"` for the H&E target, and FOCUS will skip the manual alignment step, using the existing coordinates directly for registration.
    
    **If your spot coordinates are in micrometers or physical units**, you must use **Manual** alignment to establish the correspondence with the target modality's coordinate system.

**3. Registration** (non-reference modalities only). Select the registration method and fill in its settings:

- **None**: Align only; exclude from the final MuData.
- **Spot interpolation**: Gaussian-weighted spot interpolation (CPU). For `msi` and `st`.
- **Raman pixel interpolation**: the same Gaussian footprint interpolation applied to the hyperspectral OME-TIFF pixels (CPU). For `raman`.
- **Feature extraction**: Prov-GigaPath patch embeddings (GPU required). Only available for `microscopy_image`, and only appropriate when that image is an H&E-stained brightfield RGB section, which is what the model was pretrained on. For fluorescence, IHC or other stains, pick **None** instead: the GUI offers Feature extraction for every microscopy modality and nothing downstream checks the stain.

The reference modality shows only the Preprocessing section.

### 4. Review

Review summarizes the whole configuration and is where the run starts:

- **Samples**: how many samples are included and which are excluded. **Edit** reopens the Samples step.
- **Modalities**: one row per modality with its type, alignment and registration. Select a row to open that modality's settings; **Edit list** reopens the Modalities step.
- **Run options**: the **Alignment** and **Registration** switches, **Spatial annotations**, and the **HuggingFace token** when a modality uses Feature extraction. For spatial annotations, choose the modality whose directory contains the annotation files and the file type (`geojson`). FOCUS expects one annotation file per sample in `{sample_id}/{annotation modality}/`.
- **Force recompute**: one row per modality with a switch per stage (Preprocessing, Alignment, Registration). A forced stage ignores cached results and runs again. Stages that do not run for a modality show a dash. Forcing a stage also re-runs the stages that depend on it, and those appear dimmed: forcing the reference's preprocessing re-runs every alignment and registration; forcing another modality's preprocessing re-runs its alignment and registration; forcing an alignment re-runs that modality's registration.

When you open a step or a modality from Review, the bottom bar shows **Done, back to review**, which returns to Review directly without walking through the remaining steps.

Select **Start processing** to validate the configuration and launch the run. Validation errors, if any, are listed at the top of Review.

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
