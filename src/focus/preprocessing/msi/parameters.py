"""Keyword settings consumed by MsiDataset."""

from typing import TypedDict
import numpy as np


class MsiSettings(TypedDict):
    """Existing preprocessing parameters after adapter defaults are applied."""

    mass_tolerance: float
    intensity_normalization: str
    recalibration_reference: dict[str, np.ndarray] | str | None
    internal_standard_mz: dict[str, np.ndarray] | str | None
    sample_type: str
    resolution: str
    force_recomputing: bool
