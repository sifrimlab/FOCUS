# Troubleshooting

This page covers the most common errors encountered when installing and running FOCUS, with their root causes and step-by-step fixes. If your error is not listed here, check the log file at `<dataset_path>/focus.log` for a detailed traceback, then open an issue on GitHub.

---

## Installation Problems

### `conda: command not found`

**Symptom:** The install script exits immediately with `conda: command not found` or `'conda' is not recognized`.

**Cause:** Conda (Miniconda or Anaconda) is not installed, or its `bin/` directory is not on `PATH`.

**Fix:**

1. Install [Miniconda](https://docs.conda.io/en/latest/miniconda.html) for your platform.
2. Close and reopen your terminal so that the shell profile changes take effect.
3. On Windows, use an **Anaconda Prompt** rather than a plain `cmd` or PowerShell window.
4. Re-run `bash install.sh` (macOS/Linux) or `install.bat` (Windows).

---

### CUDA Not Detected: CPU-Only PyTorch Installed

**Symptom:** The install script prints a message such as:

```
CUDA not detected. Installing CPU-only PyTorch.
```

**Cause:** The script queries `nvcc --version`, `nvidia-smi`, and common HPC module environment variables to detect the CUDA toolkit version. If none of these are available at install time, it falls back to a CPU-only build.

**Fix:**

If you have a GPU and want to use `feature_extraction` registration:

1. Load the CUDA module before running the install script (HPC):
   ```bash
   module load cuda/12.1
   bash install.sh
   ```
2. Or install PyTorch manually with the correct CUDA index URL after running the script:
   ```bash
   conda activate FOCUS
   pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
   ```
   Replace `cu121` with your CUDA version (e.g. `cu118` for CUDA 11.8, `cu124` for CUDA 12.4).
3. Verify the GPU is visible:
   ```python
   import torch
   print(torch.cuda.is_available())   # Must be True
   print(torch.cuda.get_device_name(0))
   ```

---

### Raman Processing Fails — Missing `FOCUS_BaSiCpy` or `FOCUS_ASHLAR` Environment

**Symptom:** FOCUS starts but fails when preprocessing a `raman` modality with an error about a missing conda environment or a missing command.

**Cause:** The auxiliary environments for Raman processing are optional and must be created explicitly during installation.

**Fix:**

Run the install script and confirm both prompts with `y`:

```bash
bash install.sh
# When prompted:
# "Install FOCUS_BaSiCpy environment for Raman BaSiC illumination correction? [y/N]" → y
# "Install FOCUS_ASHLAR environment for Raman ASHLAR stitching? [y/N]" → y
```

Also ensure **Java 21** or later is installed and on `PATH`, as ASHLAR requires it.

---

### `focus: command not found` After Installation

**Symptom:** Running `focus` after installation reports `command not found`.

**Cause:** The `FOCUS` conda environment is not activated.

**Fix:**

```bash
conda activate FOCUS
focus --help
```

To avoid activating the environment every time, add it to your shell profile or use the full path to the conda environment's `focus` executable.

---

## Configuration Errors

All configuration errors are caught by `parse_config` before any computation starts. The exact error message is printed to the console and written to `focus.log`.

---

### `Missing required key '...' in config`

**Cause:** A required top-level or modality field is absent from the JSON file.

**Required top-level keys:** `dataset_path`, `modalities`, `reference_modality`.  
**Required per-modality keys:** `name`, `type`, `processing_settings`.

**Fix:** Add the missing key. See the [Configuration Reference](configuration/config_fields.md) for the full field list.

---

### `'...' must be <type>, got <type>`

**Cause:** A field has the wrong JSON type. For example, passing `"true"` (string) instead of `true` (boolean), or `"10"` (string) instead of `10` (number).

**Fix:** Correct the value's JSON type. Common pitfalls:

| Field | Wrong | Correct |
|---|---|---|
| `perform_alignment` | `"true"` | `true` |
| `perform_registration` | `1` | `true` |
| `mass_tolerance` | `"10"` | `10` |
| `modalities` | `{}` | `[...]` |

---

### `Unsupported modality type '...'`

**Cause:** The `"type"` field contains a value that FOCUS does not recognize.

**Valid values:** `"microscopy_image"`, `"msi"`, `"raman"`, `"st"`.

**Fix:** Correct the `"type"` field. Note that the type is case-sensitive.

---

### `Reference modality '...' not found in declared modalities`

**Cause:** The `"reference_modality"` string does not exactly match the `"name"` of any entry in `"modalities"`.

**Fix:** Ensure the value of `"reference_modality"` is an exact string match (case-sensitive) to one of the modality names.

---

### `'perform_registration' requires 'perform_alignment' to be true`

**Cause:** You set `"perform_registration": true` but `"perform_alignment": false`. Registration maps features onto the reference coordinate space, which requires alignment to have been done first.

**Fix:** Either:
- Set `"perform_alignment": true` to run both stages, or
- Set `"perform_registration": false` if you only want preprocessing and alignment outputs.

---

### `No sample directories found in '...'`

**Cause:** The `dataset_path` directory contains no subdirectories that look like sample folders, or all subdirectories are reserved FOCUS output names (`preprocessing`, `alignment`, `registration`, `annotations`, `plots`, `resources`, `merged`).

**Fix:** Verify your directory layout. Each sample must be a subdirectory of `dataset_path`:

```
dataset_path/
├── sample_001/
│   ├── msi/
│   └── st/
└── sample_002/
    ├── msi/
    └── st/
```

---

### `Missing modality directory for sample '...': <path>`

**Cause:** A sample subdirectory does not contain a folder named after the modality. Every sample must have a folder for every declared modality.

**Fix:** Create the missing directory and place the input files inside it:

```
dataset_path/
└── sample_001/
    ├── microscopy/   ← required for modality named "microscopy"
    ├── msi/          ← required for modality named "msi"
    └── st/           ← required for modality named "st"
```

---

### `Registration type '...' is not compatible with modality type '...'`

**Cause:** An incompatible `registration_type` / modality `type` combination was specified.

**Compatibility rules:**

| `registration_type` | Compatible modality types |
|---|---|
| `feature_extraction` | `microscopy_image` only |
| `spot_interpolation` | `msi`, `st`, `raman` |
| `none` | any |

**Fix:** Adjust `"registration_type"` to a value compatible with the modality's `"type"`.

---

### `'pre_aligned' cannot be set on the reference modality`

**Cause:** You set `"alignment_strategy": "pre_aligned"` on the modality that is also the `reference_modality`. The reference is the coordinate system anchor — it cannot itself be pre-aligned to anything.

**Fix:** Set `"alignment_strategy": "pre_aligned"` only on *non-reference* modalities.

---

### `'pre_aligned' requires a spot-based reference modality`

**Cause:** `"alignment_strategy": "pre_aligned"` requires the reference modality to be spot-based (`msi` or `st`), because pre-alignment assumes the reference's spot coordinates are already expressed in the target modality's coordinate space. Image-based reference modalities (`microscopy_image`, `raman`) have no discrete spot locations and cannot be used with `pre_aligned`.

**Fix:** Use a spot-based modality as the reference when using `"pre_aligned"`.

---

### `'huggingface_token' is required when any modality uses 'feature_extraction' registration`

**Cause:** You enabled `feature_extraction` registration but did not supply a HuggingFace token. The Prov-GigaPath model must be downloaded from HuggingFace the first time it is used.

**Fix:** Add your token to the config:

```json
"huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxx"
```

Obtain a token from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). After the model is cached locally, the token is no longer needed for subsequent runs.

---

### `No .geojson annotation file found for sample '...' in '...'`

**Cause:** Annotation transfer is enabled but no `.geojson` file is present in the annotation modality folder for one or more samples.

**Fix:** Place exactly one `.geojson` file (exported from QuPath or a compatible tool) in each sample's annotation modality directory:

```
dataset_path/
└── sample_001/
    └── microscopy/
        └── annotations.geojson   ← exactly one .geojson per sample
```

---

## Alignment Problems

### The Alignment GUI Does Not Open

**Symptom:** FOCUS appears to hang at the alignment stage with no browser window appearing.

**Cause:** The alignment GUI runs at `http://localhost:8000`. The browser window must be opened manually if it does not open automatically.

**Fix:**

1. Open `http://localhost:8000` in a browser.
2. If you are running FOCUS on a remote server or HPC node, create an SSH tunnel:
   ```bash
   ssh -L 5050:localhost:5050 -L 8000:localhost:8000 user@hpc-node
   ```
   Then open both `http://localhost:5050` (main GUI) and `http://localhost:8000` (alignment GUI) locally.

---

### Alignment Result Not Saved After Confirming

**Symptom:** After clicking "Confirm" in the alignment GUI, FOCUS continues but subsequent stages fail because alignment data is missing.

**Fix:**

- Confirm that you clicked **Confirm** in the alignment GUI and waited for the page to advance to the next sample (or close). Navigating away from the page before the POST completes will discard the transform.
- Check that the browser did not block the POST request — look for errors in the browser developer console (F12).
- If the problem persists, check `focus.log` for a write error on the alignment output file.

---

### Alignment Results Ignored on Rerun

**Symptom:** When rerunning FOCUS, the alignment GUI appears again even though the previous run completed alignment successfully.

**Cause:** `"alignment_force_recomputing": true` is set on one of the non-reference modality entries in the config.

**Fix:** Set `"alignment_force_recomputing": false` (the default) on each modality to reuse cached alignment results.

---

## Registration Problems

### `RuntimeError: CUDA out of memory`

**Cause:** The GPU does not have enough VRAM to run the Prov-GigaPath model on the number of patches required by the image.

**What you cannot change:** The patch size is fixed at 224 × 224 pixels — this is a hard requirement of the Prov-GigaPath model and changing it would produce incorrect embeddings. The internal batch size is also not user-configurable.

**What you can try:**

1. Close all other GPU processes before running FOCUS to free as much VRAM as possible.
2. If the problem persists, the GPU simply does not have enough VRAM for the `feature_extraction` step. You will need to run it on a machine with a larger GPU.

**If you want to use a different (lighter) model:** The built-in `feature_extraction` step is specific to Prov-GigaPath. To use a different model you must implement a custom registration workflow in Python using the FOCUS preprocessing outputs directly — the built-in registration step cannot be swapped for a different model without code changes.

!!! warning "Do not use `spot_interpolation` as a fallback for image modalities"
    Setting `"registration_type": "spot_interpolation"` on a `microscopy_image` modality is not supported and will produce results that carry no meaningful morphological information. If `feature_extraction` is not feasible for your hardware, omit registration for the image modality (`"registration_type": "none"`) and work with the aligned OME-TIFF outputs directly.

---

### `RuntimeError: No CUDA GPUs are available`

**Cause:** `feature_extraction` registration is enabled but PyTorch was installed without CUDA support, or no GPU is visible to the process.

**Fix:**

1. Verify GPU visibility: `nvidia-smi`
2. Verify PyTorch sees the GPU:
   ```python
   import torch
   print(torch.cuda.is_available())
   ```
3. If PyTorch shows `False`, reinstall with CUDA support (see [CUDA Not Detected](#cuda-not-detected-cpu-only-pytorch-installed)).
4. On HPC systems, ensure you have requested a GPU node: `#SBATCH --gres=gpu:1`

---

### `OSError: We couldn't connect to 'https://huggingface.co'`

**Cause:** The HuggingFace model download failed because the compute node has no internet access (common on HPC clusters).

**Fix:** Download the model on a login node with internet access before submitting the job:

```bash
conda activate FOCUS
python -c "
from huggingface_hub import snapshot_download
snapshot_download('prov-gigapath/prov-gigapath', token='hf_...')
"
```

The model is cached in `~/.cache/huggingface/hub/`. On subsequent runs on nodes without internet, the cached version is used automatically.

---

### MuData File Is Not Created

**Cause:** `_compile_mudata` is skipped when any of the following conditions is true:

- The reference modality is image-based (`microscopy_image` or `raman`). MuData compilation requires a spot-based reference (`msi` or `st`).
- `"perform_registration"` is `false`.
- Fewer than two modalities have registration outputs.
- The merged registration file for a modality is missing or has a different number of observations than the anchor.

**Fix:**

1. Confirm the reference modality is `msi` or `st`.
2. Confirm `"perform_registration": true`.
3. Check `focus.log` for observation count mismatch warnings.
4. If the merged file is missing, check whether the per-sample registration files exist. A missing merged file usually indicates an earlier crash — delete the partial outputs and rerun with `"force_recomputing": true`.

---

## Runtime and I/O Errors

### `FileNotFoundError: The specified path does not exist: <path>`

**Cause:** `dataset_path` in the config points to a directory that does not exist or has a typo.

**Fix:** Verify the path exists:

```bash
ls /path/to/your/dataset
```

Use an absolute path (not a relative path) in the config.

---

### `PermissionError: The specified path is not readable: <path>`

**Cause:** The process does not have read permission on `dataset_path`.

**Fix:** Check and fix permissions:

```bash
ls -la /path/to/your/dataset
chmod -R u+r /path/to/your/dataset
```

---

### `json.JSONDecodeError: ...`

**Cause:** The config file contains invalid JSON (trailing commas, unquoted keys, or comments left in).

**Fix:** Validate the JSON with a linter:

```bash
python -m json.tool path/to/config.json
```

Remove any comments (JSON does not support `//` or `/* */` comments).

---

### Large Files Cause Memory Errors During Preprocessing

**Cause:** FOCUS processes one sample at a time — no more than one sample is ever fully loaded in RAM simultaneously — so peak RAM usage scales with the size of a single sample, not the whole dataset. Large tissue sections (high-resolution MSI or Raman data in particular) can individually require very large amounts of RAM.

**Expected RAM usage:**

- **Typical tissue samples:** 40–50 GB RAM during preprocessing.
- **Large tissue samples:** up to ~100 GB RAM may be required to process a single sample without errors.

**Fix:** Run FOCUS on a machine with sufficient RAM. There is no configuration parameter that reduces peak RAM usage for a given sample — the memory footprint is determined by the data itself. If the available RAM is insufficient, move the dataset to a machine (or HPC node) with more memory.

---

## Getting More Help

- **Check the log:** `<dataset_path>/focus.log` contains a full `DEBUG`-level trace of every step.
- **Enable debug mode:** Run with `focus --config config.json --debug` to see all log levels in the console, including HTTP traffic from the GUI.
- **Open an issue:** [github.com/sifrimlab/FOCUS/issues](https://github.com/sifrimlab/FOCUS/issues). Attach the relevant portion of `focus.log` and your config file (remove the `huggingface_token` before sharing).
