"""Alignment modality groups, progress-step declarations and legacy logger identity."""

import logging

from focus.config.catalog import IMAGE_MODALITY_TYPES, SPOT_MODALITY_TYPES


logger = logging.getLogger("focus.alignment")


_H5AD_COMPRESSION = "gzip"


# Modality type groupings (shared with the orchestrator and config validation)
_IMAGE_MODALITIES = IMAGE_MODALITY_TYPES


# Alignment steps of one modality pair, as (index, total, name) for StepReporter.
_STEP_ALIGN = (1, 3, "Aligning samples")

_STEP_SAVE = (2, 3, "Saving aligned coordinates")

_STEP_MERGE = (3, 3, "Merging aligned samples")

_SPOT_MODALITIES = SPOT_MODALITY_TYPES
