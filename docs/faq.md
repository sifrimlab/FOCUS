# Frequently Asked Questions

---

## General

### Can I use FOCUS with only one modality?

Yes, but only the preprocessing stage will run. With a single modality there is nothing to align or register, so FOCUS stops after writing the preprocessed output (`.h5ad` or OME-TIFF). No alignment GUI is launched, no registration is performed, and no MuData file is created. The preprocessed file can be used directly with scanpy or any other AnnData-compatible tool.

---

### What is the reference modality?

The reference modality defines the common coordinate space that all other modalities are aligned into. Every non-reference modality goes through the alignment GUI, where the user physically drags the reference modality (the movable layer) to overlap it with the target modality (the fixed layer). This records the reference modality's spot or pixel coordinates in the target modality's coordinate space, enabling registration to pair observations 1-to-1. After registration, all spot-based outputs share the reference modality's physical coordinates.

Choose the modality with the **lowest spatial resolution** (largest spots) as your reference. The reference spot grid defines the atomic unit of information in the final dataset: every observation in the MuData corresponds to one reference spot. Non-reference modalities with higher spatial resolution can be aggregated down onto that grid during registration, but a modality cannot produce more spots than it originally measured — upsampling would generate artificial observations. For example, if you have ST (55 µm spots) and MSI (100 µm spots), MSI should be the reference, and ST features are interpolated onto the MSI grid.

---

### Do I need a GPU?

Only for `feature_extraction` registration, which uses the Prov-GigaPath deep-learning model to extract patch embeddings from microscopy images. All other pipeline stages — preprocessing, alignment, `spot_interpolation` registration, annotation transfer, and MuData compilation — run on CPU.

If you do not have a GPU, set `"registration_type": "spot_interpolation"` for all non-reference modalities (where the modality type is `msi`, `st`, or `raman`), and set `"registration_type": "none"` for microscopy modalities.

---

### What is the difference between alignment and registration?

| | Alignment | Registration |
|---|---|---|
| **What it does** | Records the reference modality's spot/pixel coordinates in each non-reference modality's coordinate space by letting the user physically drag the reference layer to overlap the fixed target layer | Transfers features (intensities, embeddings) from the non-reference modality onto the reference spot grid |
| **How it works** | The confirmed overlay transform is read back directly; the resulting coordinate mapping is stored in `obsm` with no landmark fitting | Gaussian-weighted interpolation (spot modalities) or patch embedding extraction (image modalities) |
| **User involvement** | Interactive — you drag and visually overlap the reference with the target in the browser GUI | Fully automated |
| **Output** | Aligned AnnData with `obsm['{modality}_spatial']` keys | Registered AnnData with feature matrix aligned to the reference grid |
| **Required for MuData?** | Yes | Yes (when the reference is spot-based) |

---

### Can I skip alignment?

Yes: set `"perform_alignment": false`. In this case FOCUS runs preprocessing only and writes per-modality output files. No alignment or registration is performed, and no MuData is created.

Note that you **cannot** enable registration while disabling alignment — FOCUS enforces this:

```
'perform_registration' requires 'perform_alignment' to be true.
```

---

### Can I skip registration?

Yes: set `"perform_registration": false` or set `"registration_type": "none"` for all modalities. Preprocessing and alignment still run. The aligned reference coordinates are written to the aligned AnnData files, but no feature mapping is performed and no MuData is produced.

---

### Can I rerun from a specific pipeline stage?

FOCUS caches the output of each stage as files on disk. On the next run:

- **Preprocessing**: cached per sample. Set `"force_recomputing": true` inside a modality's `processing_settings` to redo only that modality.
- **Alignment**: cached per sample and per reference-target pair. Set `"alignment_force_recomputing": true` on a specific non-reference modality to redo only that pair's alignment.
- **Registration**: cached per sample. Set `"force_recomputing": true` inside the modality's `registration_settings`.

If a run crashed mid-stage, FOCUS may detect incomplete partial outputs. Delete the relevant sample's output directory (e.g. `sample_001/registration/`) and rerun to regenerate only that stage.

---

## Data and Format Questions

### What directory structure does FOCUS expect?

```
dataset_path/
├── sample_001/
│   ├── <modality_name_1>/   (contains input files)
│   └── <modality_name_2>/
├── sample_002/
│   ├── <modality_name_1>/
│   └── <modality_name_2>/
└── ...
```

Every sample must have a subdirectory for every modality declared in the config. The directory names must match the `"name"` fields in the config exactly (case-sensitive). See [Preparing Your Data](user_guide/data_preparation.md) for per-modality file requirements.

---

### What does FOCUS write to disk?

FOCUS writes all outputs back into `dataset_path`. Nothing is created outside of that directory. The final product for a full pipeline run is `merged/multimodal_dataset.h5mu`. See [Understanding the Output](user_guide/output_guide.md) for the complete directory tree and file list.

---

### What is `spot_size`?

`spot_size` is a 2-element array `[width_µm, height_µm]` stored in `adata.uns['spot_size']` that records the physical diameter of one measurement spot in micrometres. It is written during preprocessing for all spot-based modalities (MSI, ST) and propagated to the final MuData's `mdata.uns['spot_size']`. Downstream tools (e.g. squidpy) use it for neighbourhood graph construction and spatial statistics.

---

### Can I use both positive and negative ion modes for MSI?

Yes. Place the positive-mode `.imzML`/`.ibd` pair in a `pos/` subdirectory and the negative-mode pair in a `neg/` subdirectory inside the modality folder:

```
sample_001/
└── msi/
    ├── pos/
    │   ├── data.imzML
    │   └── data.ibd
    └── neg/
        ├── data.imzML
        └── data.ibd
```

Whether you have one or both ion modes, the `.imzML` and `.ibd` files must always be placed in a `pos/` or `neg/` subfolder matching the ion mode — never directly in the modality folder.

---

### What format must the spatial transcriptomics input be in?

The input must be an AnnData `.h5ad` file with:

- `.obsm['spatial']` — a `(n_spots, 2)` array of physical coordinates (any consistent unit).
- `.uns['spot_size']` — a `(2,)` array giving the spot diameter in the same units as the coordinates.
- `.X` or `.layers` — a count matrix.
- `.obs['sample_id']` — a column identifying the sample (can be a single value if the file contains one sample).

See [Spatial Transcriptomics](modalities/transcriptomics.md) for the full input contract.

---

### What is `spatial_annotation` in the output?

`spatial_annotation` is a categorical column in `mdata.obs` (and in the reference modality's annotated AnnData `obs`) that assigns a region label to each spot. It is populated when annotation transfer is enabled via `"spatial_annotations"` in the config. Labels are read from a GeoJSON file (e.g. exported from QuPath) using point-in-polygon assignment. Spots that fall outside all annotated polygons receive `None`.

---

## Configuration Questions

### My config file was built by the GUI. Can I edit it manually?

Yes. The GUI writes a standard JSON file (`<dataset_path>/focus_config.json`) that you can open in any text editor. All fields are documented in the [Configuration Reference](configuration/config_fields.md). After editing, pass it with `focus --config focus_config.json`.

---

### What is `pre_aligned`?

`"alignment_strategy": "pre_aligned"` tells FOCUS that the reference modality's spot coordinates are already expressed in a non-reference modality's coordinate space, so no alignment GUI is needed. The alignment is replaced with a uniform (identity) transformation.

**Requirements:**
- The **reference modality** must be spot-based (`msi` or `st`) — its spots have well-defined coordinates.
- The **target modality** can be any type (`msi`, `st`, `raman`, `microscopy_image`), as long as the reference's spot coordinates are already in the target's coordinate frame.
- Example: ST spots with coordinates already in H&E microscopy pixel space (e.g., from the acquisition instrument's metadata).

---

### Can more than one modality use `pre_aligned`?

No. At most **one** non-reference modality may use `"alignment_strategy": "pre_aligned"`. The reason is geometric: the reference modality's spot coordinates can be expressed in only one target modality's coordinate space at a time. If you have two or more targets that are pre-aligned with the reference, you must use `"alignment_strategy": "manual"` for all but one, and the alignment GUI will guide you to manually align the others.

---

### I don't want to normalize my ST data. How do I disable it?

Set both normalization flags to `false` in the ST modality's `processing_settings`:

```json
"processing_settings": {
  "total_counts_normalize": false,
  "log1p_transform": false
}
```

Both default to `false`, so simply omitting them also disables normalization.

---

### Do I need a HuggingFace token for every run?

Only the first time the Prov-GigaPath model is used — it needs to be downloaded from HuggingFace and cached locally (in `~/.cache/huggingface/hub/`). Once cached, the token is not used again. You can leave the `"huggingface_token"` field in the config or remove it after the first successful run.

---

## Pipeline Behaviour

### How does FOCUS handle multiple samples?

FOCUS discovers sample subdirectories automatically by listing `dataset_path` and filtering out its own output directories. Preprocessing and alignment are run per sample, then per-sample outputs are concatenated into merged files. Registration and MuData compilation always use the merged files.

---

### What happens if preprocessing already ran and I add a new sample to the dataset?

The new sample's subdirectory is discovered automatically on the next run. Because caching is per-sample, FOCUS preprocesses only the new sample and reuses the existing per-sample outputs. However, the **merged** files are rebuilt from scratch to include the new sample. Set `"force_recomputing": false` (the default) to use cached per-sample outputs.

---

### Why is there no MuData file in my output?

MuData compilation is skipped if any of these conditions apply:

1. The reference modality is image-based (`microscopy_image` or `raman`). MuData requires a spot-based reference.
2. `"perform_registration"` is `false`.
3. Fewer than two modalities have completed registration.
4. A merged registration file is missing or has an observation count mismatch with the anchor.

Check `focus.log` for a line beginning with `"Skipping MuData compilation"` or `"Only one modality available"`.

---

### Can I use FOCUS as a Python library in my own scripts?

Yes. After activating the `FOCUS` conda environment, import the package:

```python
from focus import utils, orchestrator

config = utils.parse_config({
    "dataset_path": "/data/my_experiment",
    "reference_modality": "st",
    "modalities": [...]
})
output_files = orchestrator.run(config)
```

You can also use individual preprocessing classes directly. See the [API Reference](api/index.md) for class signatures and the [Data Types](api/data_types.md) page for the schemas of all output AnnData and MuData objects.

---

### Can I pass a progress callback to `orchestrator.run`?

Yes. The `run()` function accepts an optional `progress_callback` argument. The callback is called at each stage transition and sub-step with a status dictionary. This is how the GUI tracks progress. See the [API Reference](api/index.md) for import paths and class signatures.

---

### Does FOCUS support Apple Silicon (M-series Macs)?

FOCUS runs on Apple Silicon under Rosetta 2 or natively via conda's `osx-arm64` packages. `feature_extraction` registration uses PyTorch, which supports the MPS backend on Apple Silicon. However, the Prov-GigaPath model integration currently targets CUDA explicitly — if you are on Apple Silicon and need `feature_extraction`, open an issue on GitHub.

---

## Output and Downstream Analysis

### How do I load the final MuData in Python?

```python
import mudata as mu
mdata = mu.read("path/to/merged/multimodal_dataset.h5mu")
print(mdata)
print(mdata.mod.keys())        # registered modality names
print(mdata.obsm['spatial'])   # shared physical coordinates
```

---

### Is the output compatible with scanpy and squidpy?

Yes. Each modality in the MuData is a standard AnnData object compatible with scanpy. The shared `mdata.obsm['spatial']` coordinate array follows the convention used by squidpy. You can build spatial neighbourhood graphs, run spatial statistics, and visualise with squidpy directly:

```python
import squidpy as sq
sq.gr.spatial_neighbors(mdata.mod['st'], coord_type='generic', spatial_key='spatial')
sq.gr.moran(mdata.mod['st'], genes=mdata.mod['st'].var_names[:10])
```

---

### Where are the intermediate files? Can I delete them?

Intermediate per-sample files live in `sample_*/preprocessing/`, `sample_*/alignment/`, and `sample_*/registration/`. The merged stage files are in `merged/preprocessing/`, `merged/alignment/`, and `merged/registration/`. You can delete them after confirming the final `multimodal_dataset.h5mu` is complete. Keep the per-sample files if you need to rerun specific samples or stages without reprocessing everything.
