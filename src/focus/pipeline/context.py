"""Resolved run inputs, modality ordering, reporter and pipeline output maps."""

from focus.config.types import ModalityConfig
from focus.config.options import RegistrationType


from focus.config.types import PipelineConfig

import logging

from focus.config.keys import (
    ConfigParameters,
    ModalityParameters,
    MsiPreprocessingParams,
)
from focus.config.options import ModalityType, MsiSampleType
from focus.config.catalog import IMAGE_MODALITY_TYPES, SPOT_MODALITY_TYPES


# NOTE: the registration engines are imported lazily inside _run_registration() rather
# than here, because focus.registration.encoder imports torch / timm /
# huggingface_hub at module load. Keeping these out of the module top lets callers that
# only need the alignment stage (e.g. scripts/align_only.py via _run_alignment) import
# this module without pulling in the PyTorch / GigaPath stack.

logger = logging.getLogger("focus.orchestrator")


_IMAGE_MODALITIES = IMAGE_MODALITY_TYPES

_SPOT_MODALITIES = SPOT_MODALITY_TYPES


def _get_reference_modality(config: PipelineConfig) -> ModalityConfig:
    """Find and return the reference modality config entry."""
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]
    for m in config[ConfigParameters.MODALITIES]:
        if m[ModalityParameters.NAME] == ref_name:
            return m
    raise ValueError(f"Reference modality '{ref_name}' not found.")


def _has_spot_modalities(config: PipelineConfig) -> bool:
    """Check if there is at least one spot-based modality in the config."""
    ref_mod = _get_reference_modality(config)
    return ref_mod[ModalityParameters.TYPE] in _SPOT_MODALITIES


def _is_microgrid_experiment(config: PipelineConfig) -> bool:
    """Return True if any MSI modality in the config is a microgrid acquisition.

    Microgrid alignment matches the pattern of foreground spots between grids, so in that case
    every spot modality is shown in the alignment GUI at its exact spot positions (see
    DirectMappingAligner exact_spots).
    """
    for m in config[ConfigParameters.MODALITIES]:
        if m[ModalityParameters.TYPE] != ModalityType.MSI:
            continue
        settings = m.get(ModalityParameters.PROCESSING_SETTINGS, {}) or {}
        if (
            settings.get(MsiPreprocessingParams.SAMPLE_TYPE, MsiSampleType.TISSUE)
            == MsiSampleType.MICROGRID
        ):
            return True
    return False


def registration_targets(
    modalities: list[ModalityConfig], reference: str
) -> list[ModalityConfig]:
    """Select enabled non-reference registrations in their configured modality order."""
    return [
        modality
        for modality in modalities
        if modality[ModalityParameters.NAME] != reference
        and modality[ModalityParameters.REGISTRATION_TYPE] != RegistrationType.NONE
    ]
