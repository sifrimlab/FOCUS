"""Internal Raman run parameters; no validation or default changes are introduced."""

from dataclasses import dataclass
from typing import TypedDict


class RamanSettings(TypedDict):
    """Existing preprocessing keywords after adapter defaults are applied."""

    force_recomputing: bool
    max_workers: int
    savgol_window: int
    savgol_polyorder: int
    bg_min_area_fraction: float
    otsu_threshold_factor: float
    min_object_size: int


@dataclass(frozen=True)
class RamanRunSettings:
    """Correction, smoothing and worker parameters passed through to each sample."""

    force_recomputing: bool
    max_workers: int
    savgol_window: int
    savgol_polyorder: int
    bg_min_area_fraction: float
    otsu_threshold_factor: float
    min_object_size: int
