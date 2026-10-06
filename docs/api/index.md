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

`StepReporter` lives in `focus.reporting` (re-exported by `focus.preprocessing._utils`). It is the single reporting interface of the pipeline. Every progress change becomes one activity line with the same structure on the console, in `focus.log` and in the GUI:

```
[step] [sample] [modality] [stage]
```

For example `[4/8 - Computing per-sample m/z backbones] [S01] [MSI] [Preprocessing]`. A finished step adds a line with its duration (`... done in 12.4s`), except when the step was served from cache. Detail lines start with `-`, warnings with `!`.

```python
from focus.reporting import StepReporter, get_reporter
```

Context, opened by the orchestrator:

- `stage(stage_id, index, total)` and `modality(name, index, total)`: context managers. The open step closes when they end.
- `samples(items, id_of=None)`: iterates samples whose steps are reported inside the loop. Each item becomes the current sample; the sample is cleared when the loop ends.

Steps and progress:

- `step(index, total, name, *, unit=None, items_total=0, cached=False)`: starts step `index/total`. Labels carry no parameters; values go to `detail()`.
- `tqdm(iterable, index, total, name, *, unit=None, id_of=None, **kwargs)`: a console progress bar for step `index/total`. With `unit="sample"` each item becomes the current sample and gets its own line.
- `update(current, total=None)`: item progress inside the open step (tiles, patches). It never produces a line.
- `cached()`: marks the current line as served from cache.

Free text and run state:

- `detail(msg)`, `warning(msg)`: lines attached to the current context.
- `set_state(state, **extra)`: run state changes such as `alignment_waiting`.
- `finish()`, `fail(exc)`: end of the run.

Code without a reporter handle uses `get_reporter()`, which returns the reporter of the running pipeline, or a console-only one outside a run.

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
