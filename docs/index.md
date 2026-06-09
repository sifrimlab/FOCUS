# FOCUS — Flexible Omics Curation and Unified Standardization

FOCUS is an end-to-end pipeline for integrating spatial multiomics data from multiple imaging and omics instruments acquired on the same tissue section. It handles preprocessing, spatial alignment, and optional feature registration. When a spot-based modality is used as reference and registration is enabled, FOCUS assembles outputs into a single MuData (`.h5mu`) file ready for downstream analysis in scanpy, squidpy, and AnnData. Alignment-only workflows produce merged AnnData files per modality.

---

## Who is this for?

<div class="grid cards" markdown>

-   **New users**

    ---

    Never used FOCUS before? Start with the interactive GUI — no Python required.

    [:octicons-arrow-right-24: GUI Walkthrough](quick_start/gui_usage.md)

-   **CLI / power users**

    ---

    Running FOCUS in batch mode, on an HPC cluster, or from a script? The CLI reference is your entry point.

    [:octicons-arrow-right-24: CLI Reference](quick_start/cli_usage.md)

-   **Developers**

    ---

    Integrating FOCUS outputs, extending modality support, or building on top of the Python API?

    [:octicons-arrow-right-24: API Reference](api/data_types.md)

</div>

---

## Key features

- **No programming required** — the entire pipeline is driven by a JSON configuration file or the interactive web GUI
- **MuData output** — results are written as `.h5mu`, compatible with [scanpy](https://scanpy.readthedocs.io), [squidpy](https://squidpy.readthedocs.io), and [AnnData](https://anndata.readthedocs.io)
- **Four modalities** — fluorescence/brightfield microscopy, MSI/lipidomics, Raman spectroscopy imaging, and spatial transcriptomics
- **Cross-platform** — Windows, macOS, Linux, and HPC environments are all supported
- **Container support** — Docker, Podman, and Singularity/Apptainer images are available for reproducible deployment

---

## Getting started in 3 steps

**1. Install FOCUS**

FOCUS is installed from source — clone the repository and run the installer, which
creates a `FOCUS` conda environment and registers the `focus` command:

```bash
git clone https://github.com/sifrimlab/FOCUS.git
cd FOCUS
bash install.sh          # Windows (PowerShell): .\install.ps1
conda activate FOCUS
```

See the full [Installation Guide](installation.md) for container and HPC options.

**2. Prepare your data**

Organize raw files into the standard directory layout:

```
dataset/
├── sample_001/
│   ├── microscopy/   # .tiff / .tif / .ome.tiff / .czi
│   ├── msi/          # pos/ and/or neg/ with .imzML + .ibd
│   ├── raman/        # .lif
│   └── st/           # .h5ad
└── sample_002/
    └── ...
```

See [Directory Structure](overview.md#directory-structure-convention) for the full specification.

**3. Run the pipeline**

=== "GUI"

    ```bash
    focus
    ```

    Running `focus` with no arguments starts the web GUI. Open `http://localhost:5050`
    in your browser, load or build a configuration, then press **Start Processing**.

=== "CLI"

    ```bash
    focus --config my_config.json
    ```

    All pipeline stages run automatically. Results are written to `dataset/merged/multimodal_dataset.h5mu`.

---

## Pipeline at a glance

```
Raw Data → [1] Preprocessing → [2] Alignment → [3] Registration → [4] Compilation → MuData (.h5mu)
```

| Stage | What happens |
|-------|-------------|
| [Preprocessing](pipeline/preprocessing.md) | Per-modality QC, normalization, format conversion |
| [Alignment](pipeline/alignment.md) | Interactive visual alignment via web GUI |
| [Registration](pipeline/registration.md) | Patch embeddings (images) or Gaussian interpolation (omics) onto reference coordinates |
| [Compilation](pipeline/compilation.md) | All modalities merged into a single MuData file |

---

## Further reading

- [System Overview](overview.md) — pipeline architecture, modality table, directory layout
- [Key Concepts](user_guide/concepts.md) — glossary of FOCUS terminology
- [Configuration Reference](configuration/config_fields.md) — every JSON field explained
- [Scientific Background](scientific/overview.md) — motivation and algorithm design
- [Data Schemas](api/data_types.md) — canonical AnnData / MuData schemas for developers
