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
| `st` | `total_counts_normalize` | `true` |
| `st` | `log1p_transform` | `true` |
| `msi` | `mass_tolerance` | `10` ppm |
| `msi` | `intensity_normalization` | `"tic"` |
| `microscopy_image` | `gamma` | `0.45` |
| `microscopy_image` | `pyramid_levels` | `4` |

### 4. Alignment Settings

For each non-reference modality, select the alignment strategy:

- **Manual** (default) — Interactive landmark-based alignment. Requires the alignment GUI (see Stage 3 below).
- **Pre-aligned** — Skip alignment for this modality; assume it is already co-registered with the reference. Only available when the reference modality is spot-based (`st` or `msi`).

### 5. Registration Settings

For each non-reference modality, select the registration type and fill in any additional settings:

- **None** — Align only; exclude from the final MuData.
- **Spot interpolation** — Gaussian-weighted interpolation (CPU). Suitable for `msi`, `st`, and `raman`.
- **Feature extraction** — Prov-GigaPath patch embeddings (GPU required). Only available for `microscopy_image`.

If any modality uses **Feature extraction**, a HuggingFace token field will appear at the top of the configuration panel.

### 6. Spatial Annotations (Optional)

If your samples include GeoJSON annotation files, expand this panel and fill in:

- **Annotation modality** — The `name` of the modality whose directory contains the `.geojson` files.
- **File type** — Select `geojson`.

Leave this section collapsed if you do not have annotation files.

### 7. Preview and Save

Click **Preview config** to see the generated JSON before saving. This is a good opportunity to check for any unexpected values. Click **Save config** to write `focus_config.json` into `dataset_path`. The file path is shown in the confirmation banner.

---

## Stage 3: Running the Pipeline

Click **Run FOCUS** to start the pipeline. A live log panel streams output from the pipeline process in real time. Progress bars show the current stage and per-sample progress.

### Normal Progression

The pipeline advances automatically through:

1. Preprocessing (all modalities, all samples)
2. Alignment (pauses for user interaction — see below)
3. Registration (all non-reference modalities, all samples)
4. Compilation (merge into `multimodal_dataset.h5mu`)

### Alignment Stage: Interactive Landmark Placement

When the pipeline reaches the alignment stage, processing pauses and a prompt appears in the main GUI:

> **Alignment required.** Open the alignment tool at [http://localhost:8000](http://localhost:8000) in a new tab, complete alignment for all samples, then click "Alignment complete" below.

Open [http://localhost:8000](http://localhost:8000) in a new browser tab. The alignment GUI shows two image panels side by side:

- **Left panel** — the reference modality for the current sample
- **Right panel** — the non-reference modality to align

**For each sample and each non-reference modality:**

1. Click corresponding landmark points on the left panel (reference) and the right panel (target). Landmarks must be placed in matched pairs.
2. Use scroll to zoom and click-drag to pan both panels independently.
3. Use the flip, rotate, and scale controls if the target image needs coarse reorientation before fine landmark placement.
4. Place a minimum of 4 landmark pairs. 8–12 pairs are typical for good accuracy.
5. Click **Submit** to record the alignment transform for this sample/modality pair.
6. The GUI advances to the next sample or the next modality automatically.

Once all samples and modalities are submitted, return to the main GUI at localhost:5050 and click **Alignment complete**. The pipeline resumes automatically.

!!! tip "Choosing good landmarks"
    Select anatomically distinctive and unique points: tissue edges, blood vessels, branching structures, distinctive cell clusters, or staining artefacts. Avoid points that appear identical at multiple locations in the tissue (e.g., centres of uniformly distributed cells). Landmarks distributed across the full tissue area give more accurate transforms than landmarks clustered in one region.

!!! warning "Keep the alignment tab open"
    Do not close the alignment tab (localhost:8000) until you have submitted all samples. The main pipeline waits indefinitely for alignment to complete. Closing the tab will not cancel the wait, but you will lose any unsaved landmark placements.

!!! warning "Complete all samples before clicking Alignment complete"
    Clicking **Alignment complete** before all samples are submitted will cause the pipeline to proceed with incomplete transforms, producing incorrect registration results for the missed samples. The alignment GUI shows a progress indicator listing which samples still need to be submitted.

---

## Stage 4: Complete

When the full pipeline finishes, the GUI displays a completion summary:

- A list of all output files with their sizes
- A direct link to `merged/multimodal_dataset.h5mu`
- An option to open the output directory in your system's file manager
- A link to download `focus.log` for the full run record

The pipeline can be re-run from the same config at any time. With `force_recomputing: false` (the default), stages whose outputs already exist will be skipped, making re-runs fast when only a subset of settings have changed.

---

## Tips and Troubleshooting

!!! tip "Running GUI and CLI together"
    If you have already built and saved a config via the GUI, you can run the exact same config non-interactively from the command line. This is useful for reprocessing on an HPC node without a display, provided you use `alignment_strategy: "pre_aligned"` or already have aligned outputs.

!!! tip "Saving multiple configs"
    You can save multiple config files in the same `dataset_path` with different names (e.g., `focus_config_st_only.json`, `focus_config_full.json`) and choose which one to load on the Setup screen. Only `focus_config.json` is auto-saved by the GUI; any other name must be specified manually.

!!! warning "Do not run two FOCUS processes on the same dataset_path simultaneously"
    Concurrent writes to the same output files will corrupt results. Run one FOCUS process at a time per dataset.
