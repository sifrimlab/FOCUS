# CLI Reference

The `focus` command is the single entry point for both GUI mode and non-interactive (CLI) pipeline execution.

---

## Basic Invocation

```bash
focus                                    # Start the GUI at localhost:5050
focus --config config.json               # Run the full pipeline from a JSON config
focus --config config.json --debug       # Run with DEBUG-level console logging
focus --help                             # Show help and exit
```

---

## Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | — | Path to a JSON config file. If omitted, GUI mode starts. The path may be relative or absolute. |
| `--debug` | — | `false` | Enable DEBUG-level console output, including werkzeug HTTP request logs. The log file always captures DEBUG regardless of this flag. |
| `--help` | `-h` | — | Show the help message and exit. |

---

## GUI Mode vs CLI Mode

=== "GUI mode (no `--config`)"

    ```bash
    conda activate FOCUS
    focus
    ```

    Starts a Flask web server at `http://localhost:5050`. Use this mode to:

    - Build or edit a configuration interactively
    - Run the pipeline with a visual progress monitor
    - Perform interactive visual alignment via the alignment tool at `localhost:8000`

=== "CLI mode (`--config`)"

    ```bash
    conda activate FOCUS
    focus --config /path/to/dataset/focus_config.json
    ```

    Loads and validates the config, then runs the full pipeline non-interactively. Use this mode for:

    - Scripted or automated processing
    - HPC batch jobs
    - Reprocessing with an existing config that does not require interactive alignment

    !!! note "Interactive alignment in CLI mode"
        If your config requires manual alignment (at least one modality with `"alignment_strategy": "manual"` when `perform_alignment` or `perform_registration` is active), FOCUS will launch the interactive alignment GUI at `http://localhost:8000` and wait for you to complete the alignment before resuming the pipeline.
        
        For truly automated/headless operation without manual alignment, use one of these approaches:
        
        1. **Skip alignment and registration entirely**: Set `"perform_alignment": false` and `"perform_registration": false`. The pipeline will only run preprocessing.
        
        2. **Use pre-aligned modalities**: Set `"alignment_strategy": "pre_aligned"` on all non-reference modalities.
        
            This requires:
            
            - Your data is already co-registered (reference coordinates are expressed in each target modality's coordinate system)
            - You have exactly 2 modalities (one reference, one target)
            - With 3 or more modalities, at least one non-reference modality will require manual alignment
        
        3. **Split processing steps**: Run the pipeline in multiple passes with different stage settings. This allows you to complete alignment interactively without blocking headless processing:
        
            **Pass 1 (headless preprocessing)**:
            ```json
            {
              "perform_alignment": false,
              "perform_registration": false
            }
            ```
            
            **Pass 2 (GUI alignment)**:
            ```json
            {
              "perform_alignment": true,
              "perform_registration": false,
              "modalities": [
                {
                  "processing_settings": {
                    "force_recomputing": false
                  }
                }
              ]
            }
            ```
            Run in GUI mode to complete manual alignment. All preprocessing outputs are reused.
            
            **Pass 3 (headless registration)**:
            ```json
            {
              "perform_alignment": true,
              "perform_registration": true,
              "modalities": [
                {
                  "alignment_force_recomputing": false,
                  "processing_settings": {
                    "force_recomputing": false
                  },
                  "registration_settings": {
                    "force_recomputing": false
                  }
                }
              ]
            }
            ```
            Run in CLI mode (headless). Alignment is already cached, so the registration stage will not block waiting for user input.

---

## Logging

### Log file

Every run writes a log file to `<dataset_path>/focus.log`. The file handler always operates at DEBUG level, capturing the full record of every pipeline step regardless of the `--debug` flag.

Log entry format:
```
2024-03-15 09:12:34 [INFO] focus (orchestrator.py:42): Starting preprocessing for sample 'sample_001'
2024-03-15 09:12:41 [DEBUG] focus (transcriptomic.py:110): st_sample_001_processed.h5ad written (2.3 MB)
```

### Console output

By default, the console shows INFO-level messages. Use `--debug` to also show DEBUG-level messages and werkzeug HTTP request logs in the terminal:

```bash
focus --config config.json --debug
```

---


---

## Running on HPC Without a Display

CLI mode requires no display or browser. It is fully compatible with headless Linux servers and HPC batch schedulers.

### Option 1: Pre-aligned data (no alignment needed)

If your reference modality's coordinates are already expressed in each target modality's coordinate system (for example, a 10x Visium dataset where the H&E image is co-registered by the instrument software), set `alignment_strategy: "pre_aligned"` on all non-reference modalities. The pipeline will skip the alignment stage and run without any user interaction.

!!! warning "Pre-aligned constraints"
    `pre_aligned` only works when:
    - You have exactly **2 modalities** (one reference, one target)
    - The reference coordinates are **already expressed** in the target modality's coordinate frame
    
    With **3 or more modalities**, at least one target will require manual alignment, so `pre_aligned` cannot be used for all of them.

```json
{
  "modalities": [
    {
      "alignment_strategy": "pre_aligned",
      "name": "msi",
      "processing_settings": { ... },
      "registration_settings": {},
      "registration_type": "none",
      "type": "msi"
    }
  ]
}
```

### Option 2: SSH port-forwarding for GUI alignment on a remote server

If you need the interactive alignment GUI on a remote machine, forward both ports to your local machine:

```bash
# On your local machine, open an SSH tunnel:
ssh -L 5050:localhost:5050 -L 8000:localhost:8000 user@cluster.example.org

# On the cluster, start FOCUS in GUI mode:
conda activate FOCUS
focus

# Open http://localhost:5050 in your local browser as usual
```

### SLURM batch job example

```bash
#!/bin/bash
#SBATCH --job-name=focus
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --gres=gpu:1          # remove this line if not using feature_extraction

conda activate FOCUS
focus --config /scratch/mylab/project/focus_config.json
```

---

## Batch Processing Multiple Datasets

If you have a cohort where each subdirectory contains an independent dataset with its own config, you can process them sequentially with a simple shell loop:

```bash
for config in /data/cohort/*/focus_config.json; do
    echo "Processing: $config"
    focus --config "$config"
done
```

For parallel processing on SLURM, use a job array:

```bash
#!/bin/bash
#SBATCH --job-name=focus_array
#SBATCH --array=0-9            # adjust to the number of datasets
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G

CONFIGS=(/data/cohort/*/focus_config.json)
CONFIG="${CONFIGS[$SLURM_ARRAY_TASK_ID]}"

conda activate FOCUS
focus --config "$CONFIG"
```

---

## Using FOCUS as a Python Library

The FOCUS preprocessing and registration components can be imported and used directly in Python scripts. This is an advanced use case intended for developers who need to integrate specific pipeline stages into their own workflows.

```python
# Preprocessing
from focus.preprocessing.transcriptomic import SpatialTranscriptomic
from focus.preprocessing.lipidomics import MsiSample
from focus.preprocessing.microscopy_image import MicroscopyImage

# Alignment
from focus.alignment.alignment import DirectMappingAligner

# Registration
from focus.registration.registration import RegistrationPipeline

# Utilities
from focus.utils import parse_config, setup_logging
```

!!! note "API stability"
    The public Python API is not yet stabilised. Internal class and function signatures may change between versions. For production use, prefer the CLI or GUI interfaces which offer stable behaviour. Refer to the [API Reference](../api/index.md) for the current module documentation.
