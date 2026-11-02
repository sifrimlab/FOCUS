"""Compute the unchanged dataset fingerprint payload."""

from __future__ import annotations
import os
from typing import cast
from focus.io.discovery import find_imzml_pair
from focus.io.provenance import fingerprint, file_digest
from focus.preprocessing.msi.settings import METHOD_VERSION
from focus.preprocessing.msi.run_state import MsiRunSettings
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


def settings_fingerprint(dataset: MsiDataset, settings: MsiRunSettings) -> str:
    """Compute the unchanged dataset fingerprint payload."""

    def as_lists(
        by_mode: dict[str, np.ndarray] | None,
    ) -> dict[str, list[float]] | None:
        """Serialize the existing per-mode m/z lists for fingerprinting."""
        return (
            None
            if by_mode is None
            else {mode: masses.tolist() for mode, masses in sorted(by_mode.items())}
        )

    dataset_key = dataset_fingerprint(
        dataset,
        {
            "mass_tolerance": settings.mass_tolerance,
            "frequency_threshold": settings.frequency_threshold,
            "intensity_normalization": settings.normalization,
            "recalibration_reference": as_lists(settings.references),
            "internal_standard_mz": as_lists(settings.standards),
            "detect_background": settings.detect_background,
            "sample_type": settings.sample_type,
            "resolution": settings.resolution,
        },
    )
    return dataset_key


def dataset_fingerprint(dataset: MsiDataset, settings: dict[str, object]) -> str:
    """
    Fingerprint of the whole dataset run: method version, settings, lipid database and every
    sample's inputs (imzML SHA-256 and .ibd size; the .ibd itself is too large to hash). Every
    sample's output depends on all samples (aligned mass scales and the m/z backbone), so
    the same key is stored in every per-sample file and in the merged file.
    """
    inputs: dict[str, dict[str, dict[str, str | int]]] = {}
    for sample in sorted(dataset.samples, key=lambda s: s.sample_id):
        inputs[sample.sample_id] = {}
        for mode, mode_path in sorted(sample.input_paths.items()):
            imzml, ibd = cast(tuple[str, str], find_imzml_pair(mode_path))
            inputs[sample.sample_id][str(mode)] = {
                "imzml_sha256": file_digest(imzml),
                "ibd_size": os.path.getsize(ibd),
            }
    database = dataset._lipid_annotation_db_path
    return fingerprint(
        {
            "method": METHOD_VERSION,
            "modality": dataset.samples[0].modality_name if dataset.samples else None,
            "settings": settings,
            "lipid_annotation_db_sha256": file_digest(database)
            if database is not None
            else None,
            "inputs": inputs,
        }
    )
