# CLI Usage Guide

The `focus` command runs the full pipeline non-interactively from a JSON
configuration file. This page is a practical quick start; for the complete option and
logging reference see the [CLI Reference](../user_guide/cli_reference.md).

## Basic Command

```bash
conda activate FOCUS
focus --config /path/to/your/focus_config.json
```

Running `focus` **without** `--config` starts the GUI instead (see the
[GUI Usage Guide](gui_usage.md)).

## Options

`focus` accepts exactly these options:

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--config PATH` | `-c` | — | Path to a JSON config file (relative or absolute). If omitted, the GUI starts. |
| `--debug` | — | `false` | Enable DEBUG-level console output (including werkzeug HTTP request logs). |
| `--help` | `-h` | — | Show the help message and exit. |

There are no other flags. (In particular, there is no `--verbose` or `--version`
option, and FOCUS reads no runtime environment variables — `TORCH_VERSION` is only
used by the installer.)

```bash
focus --config /data/project/focus_config.json            # run the pipeline
focus --config /data/project/focus_config.json --debug    # run with debug logging
focus --help                                              # show usage
```

## Configuration File

The CLI requires a valid JSON configuration file. The three required top-level keys are
`dataset_path`, `modalities`, and `reference_modality`; everything else has a default.
See the [Configuration Reference](../configuration/config_structure.md) for the full
schema.

### Minimum Configuration Example

```json
{
  "dataset_path": "/path/to/dataset",
  "reference_modality": "microscopy",
  "perform_alignment": false,
  "perform_registration": false,
  "huggingface_token": null,
  "spatial_annotations": null,
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": false,
        "crop_to_tissue": false,
        "gamma": 0.45,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "microscopy_image"
    }
  ]
}
```

The `name` of each modality must exactly match the corresponding subdirectory name in
your dataset (case-sensitive).

## Controlling Which Stages Run

FOCUS runs preprocessing → alignment → registration → compilation in order. The two
top-level flags `perform_alignment` and `perform_registration` control how far it goes:

=== "Preprocessing only"

    ```json
    { "perform_alignment": false, "perform_registration": false }
    ```

=== "Preprocessing + alignment"

    ```json
    { "perform_alignment": true, "perform_registration": false }
    ```

=== "Full pipeline"

    ```json
    { "perform_alignment": true, "perform_registration": true }
    ```

!!! note "Manual alignment blocks headless runs"
    If a modality uses `"alignment_strategy": "manual"` while alignment/registration is
    active, FOCUS launches the interactive alignment GUI at `http://localhost:8000` and
    waits for you. For fully headless runs, either disable alignment/registration, use
    `"alignment_strategy": "pre_aligned"`, or split the work into passes. See the
    [CLI Reference](../user_guide/cli_reference.md) and [HPC guide](../deployment/hpc.md).

## Caching and Re-running

FOCUS caches stage outputs under `dataset_path`. Re-running the same config reuses
completed work and skips straight to what is missing. To force a stage to recompute,
set `force_recomputing: true` in that modality's `processing_settings` (or
`alignment_force_recomputing` / the registration settings' `force_recomputing`):

```json
{
  "modalities": [
    { "name": "msi", "processing_settings": { "force_recomputing": true } }
  ]
}
```

## Logging

Each run writes a single log file to `<dataset_path>/focus.log` (always at DEBUG level).
The console shows INFO by default; add `--debug` to also show DEBUG messages:

```bash
focus --config /path/to/config.json --debug
tail -f /path/to/dataset/focus.log
```

## Containers and Windows

=== "Docker / Podman"

    ```bash
    bash focus-container.sh --mount /data/project -- --config /data/project/focus_config.json
    ```

=== "Singularity / Apptainer"

    ```bash
    singularity run --bind /data/project focus.sif --config /data/project/focus_config.json
    ```

=== "Windows"

    ```bat
    conda activate FOCUS
    focus --config C:\data\project\focus_config.json
    ```

See [Container Deployment](../deployment/containers.md) for the full launcher reference.

## Batch Processing

Process a cohort of independent datasets, each with its own config, with a shell loop:

```bash
for config in /data/cohort/*/focus_config.json; do
    echo "Processing: $config"
    focus --config "$config"
done
```

For SLURM job arrays and batch script examples, see the
[HPC & Headless Servers](../deployment/hpc.md) guide.

## Validating a Config in Python

You can validate a config before running, using the same function the CLI uses:

```python
import json
from focus.utils import parse_config

with open("focus_config.json") as f:
    config = json.load(f)

try:
    parse_config(config)
    print("Configuration is valid!")
except Exception as e:
    print(f"Configuration error: {e}")
```

The pipeline can also be driven directly from Python via `focus.orchestrator.run` — see
[Using FOCUS as a Python Library](../user_guide/cli_reference.md#using-focus-as-a-python-library)
and the [API Reference](../api/index.md). The Python API is not yet stabilised; prefer
the CLI or GUI for production use.

## Next Steps

1. **Try the GUI** — see the [GUI Usage Guide](gui_usage.md).
2. **Full CLI reference** — see the [CLI Reference](../user_guide/cli_reference.md).
3. **Prepare your data** — see [Preparing Your Data](../user_guide/data_preparation.md).
4. **Configuration** — see the [Configuration Reference](../configuration/config_fields.md).
