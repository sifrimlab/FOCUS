# API Reference

FOCUS exposes Python APIs for preprocessing, alignment, registration, annotation transfer, config validation, and orchestration.

This page reflects the current implementation in `src/focus`.

---

## Import map (implemented)

```python
# Preprocessing
from focus.preprocessing import (
    preprocess_modality,
    BaseSample,
    BaseDataset,
    MicroscopyImage,
    MicroscopyImageDataset,
    MsiSample,
    MsiDataset,
    RamanImage,
    RamanMetadata,
    RamanDataset,
    SpatialTranscriptomic,
    SpatialTranscriptomicDataset,
)

# Alignment
from focus.alignment import DirectMappingAligner

# Registration
from focus.registration import FeatureExtractorRegistration, SpotInterpolationRegistration

# Annotations
from focus.annotations import transfer_annotations
from focus.annotations.annotations import load_geojson

# Config + orchestration
from focus.utils import parse_config
from focus.orchestrator import run
```

Notes:

- `focus.annotations` exports `transfer_annotations` only.
- `load_geojson` is implemented in `focus.annotations.annotations`.
- Orchestrator entry point is `run(config: dict, progress_callback=None)`.

---

## Core preprocessing abstractions

### `BaseSample`

```python
BaseSample(source_path: str, sample_id: str, modality_name: str)
```

Behavior:

- validates `source_path` readability
- sets `source_path`, `sample_id`, `modality_name`
- creates output directory:
  - `<source_path>/<sample_id>/preprocessing/<modality_name>/`

### `BaseDataset`

```python
BaseDataset(path: str, samples: list)
```

Defines abstract interface:

```python
process_dataset(**kwargs) -> dict[str, str]
```

Includes helper:

```python
BaseDataset._check_cache(output_path: str, force_recomputing: bool) -> bool
```

---

## Progress reporting utility

`StepReporter` lives in `focus.preprocessing._utils`. Preprocessing and orchestration use it for CLI and GUI progress updates.

```python
from focus.preprocessing._utils import StepReporter
```

Main methods:

- `step(desc, current=0, total=0)`
- `update(desc, current, total)`
- `tqdm(iterable, desc, total=None, **kwargs)`
- `message(msg)`
- `set_sample(sample_id, index, total)`

---

## Programmatic full pipeline run

```python
import json
from focus.utils import parse_config
from focus.orchestrator import run

with open("focus_config.json", "r") as f:
    config = json.load(f)

config = parse_config(config)
outputs = run(config)
```

`parse_config` expects a dictionary, not a file path.

---

## API docs in this folder

- `docs/api/preprocessing.md`
- `docs/api/data_types.md`
