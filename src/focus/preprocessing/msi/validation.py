"""Validate and parse parameters in the established order."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.config.types import MassSetting

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np


from focus.config.keys import MSI_REMOVED_NORMALIZATIONS, MsiPreprocessingParams
from focus.config.options import MsiIntensityNormalization, MsiSampleType
from focus.config.mass_lists import parse_mz_by_mode


def validate_parameters(
    dataset: MsiDataset,
    mass_tolerance: float,
    intensity_normalization: str,
    sample_type: str,
    recalibration_reference: MassSetting,
    internal_standard_mz: MassSetting,
) -> tuple[dict[str, np.ndarray] | None, dict[str, np.ndarray] | None]:
    """Validate the settings and parse the mass lists."""

    if intensity_normalization in MSI_REMOVED_NORMALIZATIONS:
        raise ValueError(
            f"intensity_normalization {intensity_normalization!r} was removed "
            f"(use {MSI_REMOVED_NORMALIZATIONS[intensity_normalization]!r})."
        )
    if intensity_normalization not in MsiIntensityNormalization.list():
        raise ValueError(
            f"Invalid intensity normalization method. Expected one of {MsiIntensityNormalization.list()}."
        )
    if sample_type not in MsiSampleType.list():
        raise ValueError(
            f"sample_type must be one of {MsiSampleType.list()}, got {sample_type!r}."
        )
    if (
        isinstance(mass_tolerance, bool)
        or not isinstance(mass_tolerance, (int, float))
        or not mass_tolerance > 0
    ):
        raise ValueError("mass_tolerance must be a positive number of ppm.")
    parsed_reference = parse_mz_by_mode(
        recalibration_reference, MsiPreprocessingParams.RECALIBRATION_REFERENCE
    )
    parsed_standard = parse_mz_by_mode(
        internal_standard_mz, MsiPreprocessingParams.INTERNAL_STANDARD_MZ
    )
    if intensity_normalization == MsiIntensityNormalization.INTERNAL_STANDARD:
        sample_modes = {mode for sample in dataset.samples for mode in sample.ion_modes}
        missing_modes = sorted(sample_modes - set(parsed_standard or {}))
        if missing_modes:
            raise ValueError(
                f"internal_standard normalization requires internal_standard_mz for ion mode(s) {missing_modes}."
            )
    return parsed_reference, parsed_standard
