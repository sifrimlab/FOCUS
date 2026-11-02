#!/usr/bin/env python3
"""Run standalone, ANCHORLESS microscopy feature extraction from preprocessed OME-TIFFs.

This is a thin, standalone entry point for single-`microscopy_image`-modality FOCUS
projects. FOCUS requires ``reference_modality`` to name a declared modality, so with only
one modality it is unavoidably its own reference, and the real pipeline's registration
stage always skips the reference modality. That means ``registration_type:
"feature_extraction"`` (which normally extracts a Prov-GigaPath embedding for the image
patch centered on each *anchor* spot produced by the alignment stage) never actually runs
for such a project: there is no alignment step and no anchor coordinates.

This script uses ``MicroscopyImageFeatureExtractor.extract_grid_features``: the image is
tiled into a non-overlapping grid of 224x224 patches (the encoder's input size), patches that
are >=99% background are dropped using the same background test the anchor-based path applies
to encoder tokens, and the rest are encoded with Prov-GigaPath (the standard CLS embedding of
each tile). No alignment or anchor modality is required.

Like ``align_only.py``, it consumes only the per-sample preprocessed OME-TIFF that lives
under ``{dataset_path}/{sample_id}/preprocessing/{modality}/`` and does not require raw
input data. Unlike ``align_only.py``, sample coverage is checked STRICTLY: every discovered
sample must already have a preprocessed file, or the script aborts before loading any model.

Output is written to the exact same paths the real anchor-based ``feature_extraction``
registration would use, so the result is a drop-in for any downstream FOCUS step that reads
registration output:

  - per-sample : {dataset_path}/{sample_id}/registration/{modality}_{sample_id}_processed_aligned_registered.h5ad
  - merged     : {dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad

Multi-resolution
----------------
By default only the full-resolution level is extracted (identical to the paths above).
``--resolution-levels N`` additionally encodes coarser levels by reading the multi-resolution
pyramid already stored in each preprocessed OME-TIFF (FOCUS preprocessing writes a ``0.5**i``
pyramid); nothing is downsampled at run time. Level ``i`` is written to a sibling file with a
``_res{i}`` suffix, and merged the same way:

  - per-sample (level i>=1) : {..}/{modality}_{sample_id}_processed_aligned_registered_res{i}.h5ad
  - merged     (level i>=1) : {..}/merged/registration/{modality}_merged_processed_aligned_registered_res{i}.h5ad

Level 0 (full resolution) is left byte-for-byte as before (unchanged filename, no extra
``uns`` keys). Coarser-level files carry the same AnnData format plus ``uns['res_level']`` and
``uns['downsample_factor']``; their ``obsm['spatial']`` patch centers are reconciled to the
full-resolution (canonical FOCUS) frame using each level's actual measured dimension ratio.
Levels are cached per file: only missing or stale ``.h5ad`` files are (re)computed.

(The filenames retain "processed_aligned_registered" even though no alignment ran. They are kept
for drop-in compatibility with any tooling that expects registration output at these paths.
A stamped ``uns['patch_mode'] = 'free_form'`` marker keeps this anchorless output from ever
being silently confused with an anchor-based output at the same path, in either direction:
this script rejects a cache missing the marker, and the real pipeline reuses a registration
file only when it carries the pipeline's registration fingerprint, which free-form files never
have.)

Requires ``huggingface_token`` in the config (to download Prov-GigaPath) and, for reasonable
runtime, a CUDA GPU (falls back to CPU otherwise).

Usage
-----
    python scripts/feature_extraction_only.py -c /path/to/config.json
    python scripts/feature_extraction_only.py -c /path/to/config.json --debug

The config is the standard FOCUS JSON config. The declared ``modalities`` list must contain
exactly one entry, of type ``microscopy_image``. Its ``registration_settings``
(``background_color``, ``force_recomputing``) are read with the same defaults as the real
pipeline; ``registration_type`` itself is not checked, since this script always performs
feature extraction regardless of that field's value.
"""

import os  # noqa: E402 - import follows runtime setup
import sys  # noqa: E402 - import follows runtime setup
import logging  # noqa: E402 - import follows runtime setup


# Allow running straight from a source checkout (scripts/ is a sibling of src/) without
# requiring an editable install. An installed ``focus`` still takes precedence if present.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(os.path.join(_SRC, "focus")) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from focus.config.grid_settings import grid_settings, require_grid_registration  # noqa: E402 - source path configured above
from focus.io.grid_inputs import require_grid_files  # noqa: E402 - source path configured above
from focus.config.grid_cli import build_grid_parser  # noqa: E402 - source path configured above
from focus.config.command_file import load_command_config  # noqa: E402 - source path configured above
from focus.config.types import PipelineConfig, ModalityConfig  # noqa: E402 - import follows runtime setup
from focus.config.keys import ConfigParameters, ModalityParameters  # noqa: E402 - import follows runtime setup
from focus.config.options import (  # noqa: E402 - import follows runtime setup
    ModalityType,
)  # noqa: E402 - import follows runtime setup
from focus.registration.free_form import GridExtraction  # noqa: E402 - source path configured above

import focus.runtime.logging as runtime_logging  # noqa: E402 - import follows runtime setup

# Unlike align_only.py, there is no lazy-import benefit to preserve here, since this script
# always needs the encoder (timm/torch/huggingface_hub).

_H5AD_COMPRESSION = "gzip"

# Prov-GigaPath's input size: the grid tile side.


def _select_microscopy_modality(
    config: PipelineConfig, logger: logging.Logger
) -> ModalityConfig | None:
    """Return the single declared modality, requiring it to be of type microscopy_image."""
    modalities = config[ConfigParameters.MODALITIES]
    if len(modalities) != 1:
        names = [m[ModalityParameters.NAME] for m in modalities]
        logger.error(
            f"This script requires exactly one declared modality, found {len(modalities)}: {names}. "
            f"It is designed for single-microscopy_image-modality projects."
        )
        return None

    modality = modalities[0]
    mod_type = modality[ModalityParameters.TYPE]
    if mod_type != ModalityType.MICROSCOPY_IMAGE:
        logger.error(
            f"Declared modality '{modality[ModalityParameters.NAME]}' has type '{mod_type}', "
            f"but this script only supports '{ModalityType.MICROSCOPY_IMAGE}'."
        )
        return None

    return modality


def main() -> None:
    """Parse command-line arguments and execute the requested workflow."""
    parser = build_grid_parser()
    args = parser.parse_args()

    if args.resolution_levels < 1:
        # Console logging is not configured yet; argparse's own stderr is the right channel here.
        parser.error(f"--resolution-levels must be >= 1, got {args.resolution_levels}.")

    # Phase 1: console-only logging so validation errors are formatted.
    runtime_logging.setup_logging(debug=args.debug)
    logger = logging.getLogger("focus")

    config = load_command_config(args.config, logger, require_raw_inputs=False)

    hf_token = require_grid_registration(config, logger)

    # Phase 2: full logging. Adds the focus.log file handler now that dataset_path is known.
    dataset_path = config[ConfigParameters.DATASET_PATH]
    runtime_logging.setup_logging(dataset_path, debug=args.debug)
    logger.info(f"Config loaded and validated (feature-extraction-only): {args.config}")

    modality = _select_microscopy_modality(config, logger)
    if modality is None:
        sys.exit(1)

    settings = grid_settings(
        dataset_path, modality, hf_token, args.resolution_levels, logger
    )

    sample_ids, preprocessed_files = require_grid_files(
        settings, config[ConfigParameters.IGNORE_SAMPLES], logger
    )

    logger.info("=" * 60)
    logger.info(
        "Running standalone feature extraction (anchorless, free-form patch grid)"
    )
    logger.info("=" * 60)

    GridExtraction(settings, sample_ids, preprocessed_files, logger).run()
    sys.exit(0)


if __name__ == "__main__":
    main()
