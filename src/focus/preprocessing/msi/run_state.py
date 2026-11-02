"""Transient state exchanged between the existing MSI preprocessing stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import scipy.sparse as sp

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample


@dataclass(frozen=True)
class MsiRunSettings:
    """Validated run parameters; mass lists retain their parsed array references."""

    mass_tolerance: int
    frequency_threshold: float
    normalization: str
    references: dict[str, np.ndarray] | None
    standards: dict[str, np.ndarray] | None
    detect_background: bool
    sample_type: str
    resolution: str
    force: bool


@dataclass
class ResumeState:
    """Cached files and the ordered subset of samples still requiring processing."""

    samples: list[MsiSample]
    spot_sizes: dict[str, list[float]]
    stored_references: dict[str, dict[str, np.ndarray]]
    files: dict[str, str]
    partial: bool


@dataclass
class FeatureState:
    """Shared feature metadata and polarity column ranges for sample assembly."""

    positive_cols: int
    negative_cols: int
    total_cols: int
    var: pd.DataFrame
    standard_columns: dict[str, np.ndarray]
    qc_modes: list[str]


@dataclass
class SampleArrays:
    """Spatial arrays and sparse count matrices ready for AnnData construction."""

    physical: np.ndarray
    raster: np.ndarray
    spot_size: np.ndarray
    n_obs: int
    normalized: sp.csr_matrix
    raw: sp.csr_matrix


@dataclass
class CachedPrefix:
    """First valid feature schema and the first incompatible sample index, if any."""

    var: pd.DataFrame | None = None
    mz: np.ndarray | None = None
    mode: np.ndarray | None = None
    annotation: np.ndarray | None = None
    invalid_index: int | None = None


@dataclass
class ReferenceSelection:
    """Per-sample calibrants, candidate pools and selection diagnostics before mass alignment."""

    selected: dict[str, dict[str, np.ndarray]]
    pools: dict[str, dict[str, np.ndarray]]
    candidates: dict[str, dict[str, tuple[int, int, float, float]]]
