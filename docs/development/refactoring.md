# Python architecture and verification

FOCUS is organized by pipeline stage and modality. The refactor preserves the
working tree captured on 2 November 2026, including the existing local cancellation
and frontend changes. Python module locations and internal interfaces changed;
configuration defaults, scientific operations, file representations and workflows
were retained. The obsolete debug example was removed with the maintainer's approval.

## Ownership

| Package | Responsibility |
| --- | --- |
| `focus.config` | Keys, choices, defaults, typed settings, CLI declarations and ordered validation |
| `focus.io` | Paths, discovery, scientific-container persistence, TIFF access and provenance |
| `focus.spatial` | Lattice inference, temporary clustering units, the shared alignment clustering, transforms and footprint membership |
| `focus.preprocessing.msi` | imzML/IBD reading, acquisition pairing, calibrants, mass features, normalization and MSI coordination |
| `focus.preprocessing.transcriptomics` | Count validation, QC, analytical clustering, sample processing and shared gene filtering |
| `focus.preprocessing.microscopy` | Image decoding, color enhancement, tissue preparation and OME pyramid writing |
| `focus.preprocessing.raman` | Leica metadata/planes, correction, spectral workers, tile geometry and external-tool execution |
| `focus.alignment` | Payload preparation, cache decisions, transforms, output writing and browser coordination |
| `focus.registration` | Separate scientific strategies, shared sample execution, readout planning, encoding and merging |
| `focus.runtime` | Reporting, cancellation, logging, resources and lazy export resolution |
| `focus.pipeline` | Stage coordination, invalidation, annotation execution and final MuData compilation |
| `focus.gui` | Separate HTTP routes, schemas, run history and server lifecycle; lowercase package name |

Stateful components retain narrow sample/run state. Numerical operations remain
functions where they do not need lifecycle state. Dataset and registration
coordination share implementations where their semantics agree. Raman and microscopy
segmentation remain distinct because their scientific assumptions differ. Public
package exports resolve lazily; removed internal module paths have no compatibility
shims. Scripts retain their command-line entrypoints.

## Reproduce the development checks

Install `.[dev]` in a development environment with the application's scientific
stack. Torch, torchvision, timm and huggingface-hub remain optional runtime-stack
installations, as before; the encoder tests require them. No pretrained weights are
downloaded by the tests. The CI workflow installs the CPU stack explicitly.

```sh
python -m pytest
python -m mypy
python -m ruff check src scripts tests tools typing
python -m ruff format --check src scripts tests tools typing
python scripts/check_structure.py
python -m build
python scripts/check_dist.py dist
```

Set `NUMBA_CACHE_DIR` and `MPLCONFIGDIR` to writable directories when the scientific
environment is read-only. Cache placement does not change pipeline parameters.
The auxiliary tools retain the separate `FOCUS_ASHLAR` and `FOCUS_BaSiCpy` environments.

Build release archives from a clean source tree without `node_modules` or stale
`*.egg-info`. The existing npm-workspace symlinks can cause setuptools to omit GUI
source files when building directly from a development checkout. `check_dist.py`
checks the complete GUI source inventory, owned Python/stubs/test contracts, typing
marker, and every compiled GUI asset's bytes in the wheel. The source distribution
includes scripts, tests, fixtures, type stubs, development documentation and auxiliary
Python entrypoints; the wheel contains the runtime package and compiled GUIs.

CI runs tests, strict typing, lint/format, structure checks, archive checks and
installed-wheel CLI/import/asset checks. Remote CI was not dispatched by this local
refactor; the corresponding checks were run locally.

## Size and documentation standards

All owned Python modules meet the 300-substantive-line target. Blank lines,
comments and docstrings do not count. Callables target 40 substantive lines.
[The exception inventory](structure_exceptions.json) records 52 individually reviewed
numerical/protocol kernels or coherent declarations with their exact current
maximum size and a specific reason. No whole module is exempt. Most exceptions are
scientific kernels; others are ordered HTTP/schema/OME declarations and controlled
characterization fixtures. The checker rejects unlisted growth, growth beyond an
exception's maximum and stale exceptions for definitions that now meet the target.

Modules, classes and callables have docstrings; scientific contracts describe
shapes, coordinate frames, units, dtypes and mutation where relevant. Constructor
contracts describe retained references, file creation and validation. The existing
NumPy operation order, sparse representations and random-state handling are retained.

## Type boundaries

Mypy runs in strict mode over `src/focus`, `scripts`, `tests` and `tools`, without
excluding owned modules. Settings and existing dictionary results use typed
contracts; transient run state uses small data classes. Geometry, acquisition and
encoder state record concrete array dtypes where fixed, and encoder callbacks have
explicit window/membership/result signatures. No conversion is introduced merely
for typing.

External-boundary exceptions are deliberately scoped:

- `focus.io.anndata` and `focus.io.mudata` forward the existing external writer and
  concatenation argument contracts. Their variadic keyword arguments remain dynamic;
  string conversion and input mutation are documented at those boundaries.
- `focus.gui.json` implements Flask's dynamic JSON-provider signature; reporter
  `tqdm` options are forwarded to the external tqdm API.
- Unannotated scientific dependencies are individually listed in the Mypy import
  override. The NumPy, pandas and SciPy typed interfaces remain checked; small SciPy
  constructor/storage adapters and invariant casts cover missing/widened overloads.
- `typing/numba/__init__.pyi` preserves decorated function signatures for checking.
  The single error-code-specific ignore in the compiled `noise_threshold` kernel
  covers NumPy median's inferred return type; inserting Python typing calls inside
  that Numba kernel would change its compilation contract.
- A few test callbacks deliberately forward external library arguments so the same
  scientific operation runs while its inputs are recorded.

Strict checking is evidence about the annotated Python contracts, not a proof about
untyped dependency internals or arbitrary scientific input shapes.

## Baseline and verification evidence

Before implementation, 124 tests passed with no skips and the existing Ruff error
checks passed. The immutable source archive includes relevant untracked files and
has SHA-256:

```text
4879843e88a5901784655bb73b2407f089d3a10752da1080c16838729dabc038
```

The archive, 404-path hash manifest and original dependency inventory are preserved
locally under ignored `.refactor/baseline/`. Runtime requirement lists and the
scientific environment's `pip freeze` were compared unchanged. Development tooling
was installed into a separate environment.

Verification completed during the refactor:

- **149 pytest tests passed, no skips.** All existing assertions were migrated to
  pytest, retaining the original decimal-rounding helper where required.
- **Strict Mypy, Ruff lint/format and structural checks passed.**
- Original and refactored implementations ran in separate processes for recorded
  configuration/error/default contracts, scientific fixtures and controlled stage
  progress, cancellation, invalidation and output-map contracts.
- Available `MEL.h5ad` ST data and dual-polarity MSI data (182,865 paired spectra)
  were processed with current defaults, forced recomputation and the alternate
  clustering resolution. Warm runs checked that output sizes and nanosecond mtimes
  stayed unchanged. All **12 per-sample/merged H5AD outputs matched exactly** against
  the original implementation's outputs.
- `scripts/compare_anndata.py` compares complete decoded X, observations, variables,
  unstructured metadata, multidimensional/pairwise arrays, layers and raw data.
  Checks include types, dtypes, ordering, categorical metadata and sparse storage
  format/data/indices/indptr; scientific archives are not compared only by hashes.
- Controlled microscopy fixtures cover physical pixel sizes, image regeneration,
  provenance and alignment cache reuse. A deterministic small ViT exercises encoder
  integration without authenticating or downloading model weights.
- Raman metadata validation was compared across 168 field cases. Controlled Leica
  planes check read order, spectral-overlap removal, float32 normalization and
  acquisition geometry. Original serial and two-worker RamanSPy outputs were recorded
  and matched exactly; the candidate also retained warm-cache behavior.
- Multiprocessing tests exercise relocated MSI and Raman workers. Raman tests verify
  project tool-path resolution, conda command/environment handling, output renames
  and cache reuse. Encoder backoff matches original ordered slices and reduced batch
  sizes under controlled allocation errors.
- BaSiCpy ran in its own environment on a 12 x 32 x 32 float32 fixture and produced
  exact before/after arrays. ASHLAR ran in its own environment on overlapping tiles;
  under controlled UUIDs, pixels, dtypes, every TIFF page tag and OME XML matched.
- All **39 compiled GUI assets** retain their baseline bytes. Fresh source/wheel
  builds and installed-wheel imports/CLI/assets were checked. CLI help matches the
  baseline and does not import Torch/timm; both installed GUI entry routes return 200.

Large local-data runs stay outside ordinary CI. All generated scientific outputs
were confined to temporary verification directories; raw data were not overwritten.

## Verification limits and preserved issues

Tests provide evidence rather than proof of universal equivalence. There was no
complete real multimodal run requiring new manual browser alignment, no real Leica
or additional microscopy acquisition, no full pretrained encoder download, and no
physical GPU OOM test. These paths use controlled fixtures or CPU model integration.
Measurements of elapsed time are allowed to differ.

The following pre-existing issues/restrictions were retained rather than repaired:

- Standalone grid configuration still passes the common validation constraints,
  including the alignment-enable requirement even though that script only encodes.
- MSI background detection remains conditional on having a lipid annotation database
  (resolved in `msi-13`: foreground detection always runs, without the database).
- `read_merged_sample_ids` retains its original broad error fallback; an exception
  before its explicit backed-file close does not gain a new cleanup path here.
- The auxiliary Raman scripts are resolved relative to a source installation. A
  standalone wheel does not install the external conda environments or repository
  tool directory into that expected location.
- The source manifest still warns about the already-missing `CONTRIBUTING.md`.
  The controlled ASHLAR fixture still warns that too few edges were aligned and
  falls back to the original stage positions in both implementations.

These observations are separate from the formal reorganization. Any behavior fix
should be a subsequent, explicitly reviewed change with its own scientific checks.
