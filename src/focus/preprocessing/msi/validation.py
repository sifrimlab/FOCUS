"""Validate and parse parameters in the established order."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.config.types import MassSetting

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np


from focus.config.keys import MsiPreprocessingParams
from focus.config.options import MsiIntensityNormalization
from focus.config.mass_lists import parse_mz_by_mode


def validate_parameters(
    dataset: MsiDataset,
    mass_tolerance: int,
    frequency_threshold: float,
    intensity_normalization: str,
    recalibration_reference: MassSetting,
    internal_standard_mz: MassSetting,
) -> tuple[dict[str, np.ndarray] | None, dict[str, np.ndarray] | None]:
    """Validate and parse parameters in the established order."""

    # Check if the required normalization method is implemented
    if intensity_normalization not in MsiIntensityNormalization.list():
        raise ValueError(
            f"Invalid intensity normalization method. Expected one of {MsiIntensityNormalization.list()}."
        )

    # Check the input parameters
    if type(mass_tolerance) is not int or mass_tolerance <= 0:
        raise ValueError("mass_tolerance must be a positive integer representing ppm.")
    if (
        type(frequency_threshold) is not float
        or frequency_threshold < 0.0
        or frequency_threshold > 1.0
    ):
        raise ValueError("frequency_threshold must be a float between 0.0 and 1.0.")
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
