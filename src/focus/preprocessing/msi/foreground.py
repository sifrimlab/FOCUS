"""Detect the foreground of one MSI sample over its ion modes and keep it on the sample."""

from __future__ import annotations
from typing import TYPE_CHECKING

import numpy as np

from focus.config.keys import MsiMetadata
from focus.preprocessing.msi.microgrid import detect_microgrid_spots
from focus.preprocessing.msi.technology import SampleProfile
from focus.preprocessing.msi.tissue import (
    ForegroundResult,
    SpectraContext,
    detect_foreground,
    spectra_context,
)
from focus.runtime.reporter_context import get_reporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample


class MsiForeground:
    """Select the preset's detector, combine the ion modes and store the result on the sample."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its sample."""
        self.sample = sample

    def detect(
        self,
        mz_vectors: dict[str, list[np.ndarray]],
        intensity_vectors: dict[str, list[np.ndarray]],
        mass_tolerance: float,
        profile: SampleProfile,
    ) -> dict[str, SpectraContext]:
        """
        Detect the foreground of every ion mode on recalibrated spectra and keep their union.

        Sets ``sample.filtered_idx`` (foreground indices), ``sample.foreground_score`` (the largest
        per-mode score) and ``sample.background_found`` (a detector separated a background). A sample
        in which no foreground is found keeps every spectrum with peaks, with a warning. Returns the
        per-mode recurrent ions, reused by feature selection.
        """
        sample = self.sample
        contexts: dict[str, SpectraContext] = {}
        union: np.ndarray | None = None
        score: np.ndarray | None = None
        background = False
        for mode in sample.ion_modes:
            mz_list, intensity_list = mz_vectors[mode], intensity_vectors[mode]
            contexts[mode] = spectra_context(mz_list, mass_tolerance)
            coordinates = sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES]
            result: ForegroundResult = (
                detect_microgrid_spots(
                    mz_list, intensity_list, coordinates, contexts[mode]
                )
                if profile.detector == "microgrid"
                else detect_foreground(
                    mz_list, intensity_list, coordinates, contexts[mode], profile
                )
            )
            get_reporter().detail(
                f"Foreground ({str(mode).upper()}): {int(result.mask.sum()):,} of "
                f"{result.mask.size:,} spectra; {result.summary}"
            )
            union = result.mask if union is None else union | result.mask
            score = result.score if score is None else np.maximum(score, result.score)
            background |= result.background
        assert union is not None and score is not None
        if not union.any():
            has_peaks = np.zeros(union.size, dtype=bool)
            for mode in sample.ion_modes:
                has_peaks |= np.array([mz.size > 0 for mz in mz_vectors[mode]])
            union, background = has_peaks, False
            get_reporter().warning(
                "No foreground found; every spectrum is used as foreground"
            )
        sample.filtered_idx = np.flatnonzero(union).tolist()
        sample.foreground_score = score.astype(np.float32)
        sample.background_found = background and not union.all()
        return contexts
