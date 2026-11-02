"""Dispatch MSI foreground detection while retaining sample-owned filtering state."""

from __future__ import annotations
from focus.preprocessing.msi.tissue import detect_tissue_spots
from focus.preprocessing.msi.microgrid import detect_microgrid_spots
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
import pandas as pd


class MsiForeground:
    """Select the modality-specific tissue or microgrid detector for a sample payload."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def _detect_tissue_spots(
        self,
        mz_vectors: list[np.ndarray],
        intensity_vectors: list[np.ndarray],
        ion_mode: str,
        database: pd.DataFrame | None = None,
        mass_tolerance: int | None = None,
    ) -> list[int]:
        """Dispatch tissue detection using this sample's configured acquisition type."""
        return detect_tissue_spots(
            self.sample,
            mz_vectors,
            intensity_vectors,
            ion_mode,
            database,
            mass_tolerance,
        )

    def _detect_microgrid_spots(
        self,
        mz_vectors: list[np.ndarray],
        intensity_vectors: list[np.ndarray],
        ion_mode: str,
        database: pd.DataFrame | None = None,
        mass_tolerance: int | None = None,
    ) -> list[int]:
        """Detect sparse cells using the existing local background model."""
        return detect_microgrid_spots(
            self.sample,
            mz_vectors,
            intensity_vectors,
            ion_mode,
            database,
            mass_tolerance,
        )
