# API Reference

FOCUS exposes a fully programmable Python API that mirrors every stage of the pipeline — preprocessing, alignment, registration, and annotation transfer. You can call individual classes directly for fine-grained control or use the high-level orchestrator for a full automated run.

---

## Import Structure

```python
# Preprocessing
from focus.preprocessing import preprocess_modality
from focus.preprocessing import BaseSample, BaseDataset
from focus.preprocessing import MicroscopyImage, MicroscopyImageDataset
from focus.preprocessing import MsiSample, MsiDataset
from focus.preprocessing import RamanImage, RamanMetadata, RamanDataset
from focus.preprocessing import SpatialTranscriptomic, SpatialTranscriptomicDataset

# Alignment
from focus.alignment import DirectMappingAligner

# Registration
from focus.registration import FeatureExtractorRegistration, SpotInterpolationRegistration

# Annotations
from focus.annotations import transfer_annotations, load_geojson
```

---

## Core Abstractions

### `BaseSample`

Abstract base class for a single sample of any modality. All modality-specific sample processors inherit from this class.

**Constructor**

```python
BaseSample(source_path: str, sample_id: str, modality_name: str)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_path` | `str` | Root path of the dataset directory |
| `sample_id` | `str` | Unique identifier for this sample (must match a subdirectory name) |
| `modality_name` | `str` | Name of the modality (must match a subdirectory inside the sample directory) |

On construction, validates that `source_path` is readable and creates the output directory at `<source_path>/<sample_id>/preprocessing/<modality_name>/`.

**Key attributes**

- `source_path` — root dataset path
- `sample_id` — sample identifier
- `modality_name` — modality name
- `output_path` — resolved preprocessing output directory

---

### `BaseDataset`

Abstract base class for a collection of samples. Enforces the `process_dataset` interface and provides shared cache-checking utilities.

**Constructor**

```python
BaseDataset(path: str, samples: list)
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Root path of the dataset directory |
| `samples` | `list[BaseSample]` | List of per-sample processor objects |

**Abstract method**

```python
process_dataset(**kwargs) -> dict[str, str]
```

Returns a dictionary mapping sample IDs to output file paths. Implementations also include a `"merged"` key pointing to the concatenated multi-sample output.

**Static utility**

```python
BaseDataset._check_cache(output_path: str, force_recomputing: bool) -> bool
```

Returns `True` when a cached output exists and `force_recomputing` is `False`, indicating the step can be skipped.

---

### `StepReporter`

Progress reporting utility used internally by all processing classes. Prints step descriptions to stdout during CLI usage and, when a callback is registered, sends structured progress payloads to the web GUI.

```python
from focus.preprocessing._utils import StepReporter

reporter = StepReporter(callback=None)
```

| Method | Description |
|--------|-------------|
| `step(desc)` | Begin a named step. Prints to stdout and fires the GUI callback. |
| `message(msg)` | Send a free-form status message. |
| `set_sample(sample_id, index, total)` | Set current sample context, resets sub-step fields. |
| `tqdm(iterable, desc, total)` | Drop-in `tqdm` replacement that also reports item-level progress to the GUI. |
| `update(desc, current, total)` | Update item-level progress without printing to stdout. |

---

## Orchestrated Pipeline

For running the full pipeline from a configuration file, use `focus.orchestrator`:

```python
from focus.orchestrator import run_pipeline
from focus.utils import parse_config

config = parse_config("focus_config.json")
run_pipeline(config)
```

This is the recommended entry point for most users. The API classes documented here are the underlying building blocks used internally by `run_pipeline`.

---

## Module Index

| Module | Contents |
|--------|----------|
| [api/preprocessing.md](preprocessing.md) | `preprocess_modality`, `BaseSample`, `BaseDataset`, and all modality classes |
| [api/alignment.md](alignment.md) | `DirectMappingAligner` |
| [api/registration.md](registration.md) | `FeatureExtractorRegistration`, `SpotInterpolationRegistration` |
| [api/annotations.md](annotations.md) | `load_geojson`, `transfer_annotations` |
| [api/data_types.md](data_types.md) | Output formats, AnnData conventions, MuData structure |
