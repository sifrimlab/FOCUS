"""Validated settings for standalone microscopy grid extraction."""

from __future__ import annotations
from dataclasses import dataclass, field
import logging
import sys
from focus.config.types import PipelineConfig, ModalityConfig
from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import SegmentationBackgroundColor

_PATCH_SIZE = 224


@dataclass(frozen=True)
class GridSettings:
    """Resolved input paths, model credential, grid size, and cache controls."""

    path: str
    modality: str
    token: str = field(repr=False)
    patch_size: int
    background: str
    force: bool
    levels: int


def require_grid_registration(config: PipelineConfig, logger: logging.Logger) -> str:
    """Require enabled registration and a token before configuring the dataset log file."""
    if not config[ConfigParameters.PERFORM_REGISTRATION]:
        logger.error(
            "'perform_registration' is false in the config, so there is nothing to do."
        )
        sys.exit(1)

    # require_raw_inputs=False also skips parse_config's own huggingface_token requirement
    # (gated behind that flag), so check it here, since this script unconditionally needs
    # it to download Prov-GigaPath.
    hf_token = config[ConfigParameters.HUGGINGFACE_TOKEN]
    if not hf_token or not isinstance(hf_token, str):
        logger.error(
            "'huggingface_token' is required to run feature extraction (used to download Prov-GigaPath)."
        )
        sys.exit(1)

    return hf_token


def grid_settings(
    dataset_path: str,
    modality: ModalityConfig,
    hf_token: str,
    levels: int,
    logger: logging.Logger,
) -> GridSettings:
    """Apply the existing grid options and emit their original configuration summary."""
    modality_name = modality[ModalityParameters.NAME]
    reg_settings = modality[ModalityParameters.REGISTRATION_SETTINGS]
    if "patch_size" in reg_settings:
        logger.warning(
            "patch_size is ignored: the grid tile is the encoder's input size"
        )
    patch_size = _PATCH_SIZE
    background_color = (
        reg_settings.get("background_color") or SegmentationBackgroundColor.WHITE
    )
    force_recomputing = reg_settings.get("force_recomputing", False)
    logger.info(
        f"Modality '{modality_name}': patch_size={patch_size}, "
        f"background_color='{background_color}', force_recomputing={force_recomputing}"
    )
    num_levels = levels
    logger.info(
        f"Resolution levels requested: {num_levels} "
        f"({'full resolution only' if num_levels == 1 else f'pyramid levels 0..{num_levels - 1}'})."
    )

    return GridSettings(
        dataset_path,
        modality_name,
        hf_token,
        patch_size,
        background_color,
        force_recomputing,
        num_levels,
    )
