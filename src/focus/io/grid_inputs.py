"""Preprocessed image discovery and coverage checks for standalone grid extraction."""

import logging
import os
import sys
from focus.config.catalog import MODALITY_FILE_EXTENSION
from focus.config.options import ModalityType
from focus.config.grid_settings import GridSettings
from focus.io.discovery import discover_sample_ids
from focus.io.paths import preprocessed_file


def collect_grid_files(
    dataset_path: str,
    modality_name: str,
    sample_ids: list[str],
    logger: logging.Logger,
) -> tuple[dict[str, str], list[str]]:
    """Build {sample_id: preprocessed_ome_tiff_path}, reporting any samples missing it."""
    ext = MODALITY_FILE_EXTENSION[ModalityType.MICROSCOPY_IMAGE]

    present: dict[str, str] = {}
    missing: list[str] = []
    for sid in sample_ids:
        path = preprocessed_file(dataset_path, sid, modality_name, ext)
        if os.path.isfile(path):
            present[sid] = path
        else:
            missing.append(sid)

    logger.info(
        f"Modality '{modality_name}': {len(present)}/{len(sample_ids)} preprocessed '.{ext}' file(s) present."
    )
    return present, missing


def require_grid_files(
    settings: GridSettings, ignore: list[str], logger: logging.Logger
) -> tuple[list[str], dict[str, str]]:
    """Check complete sample coverage before any model loading, retaining CLI error exits."""
    sample_ids = discover_sample_ids(settings.path, ignore_samples=ignore)
    if not sample_ids:
        logger.error(f"No sample directories found in '{settings.path}'.")
        sys.exit(1)
    logger.info(f"Discovered {len(sample_ids)} sample(s): {sample_ids}")

    # Strict check: every discovered sample must already be preprocessed, checked BEFORE any
    # model loading (unlike align_only.py's lenient per-pair warning policy).
    preprocessed_files, missing = collect_grid_files(
        settings.path, settings.modality, sample_ids, logger
    )
    if missing:
        ext = MODALITY_FILE_EXTENSION[ModalityType.MICROSCOPY_IMAGE]
        logger.error(
            f"Modality '{settings.modality}' is missing preprocessed '.{ext}' file(s) for "
            f"{len(missing)}/{len(sample_ids)} sample(s): {missing}. Expected at "
            f"{{dataset_path}}/{{sample}}/preprocessing/{settings.modality}/. Aborting: this script "
            f"requires every discovered sample to already be preprocessed."
        )
        sys.exit(1)

    return sample_ids, preprocessed_files
