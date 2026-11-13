"""Detect the foreground of one MSI sample over its ion modes and keep it on the sample."""

from __future__ import annotations
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from focus.config.keys import MsiMetadata
from focus.preprocessing.msi.microgrid import detect_microgrid_spots
from focus.preprocessing.msi.lipid_database import annotated_mask
from focus.preprocessing.msi.technology import (
    DECOY_OFFSETS,
    FREQUENCY_THRESHOLD,
    LIPID_MIN_ANNOTATED,
    LIPID_MIN_ENRICHMENT,
    SampleProfile,
)
from focus.preprocessing.msi.tissue import (
    ForegroundResult,
    SpectraContext,
    detect_foreground,
    lipid_fraction,
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
        database: pd.DataFrame | None = None,
    ) -> dict[str, SpectraContext]:
        """
        Detect the foreground of every ion mode on recalibrated spectra and keep their union.

        With a lipid `database`, each mode's lipid fraction (:func:`_lipid_score`) is offered to the
        detector as a tissue score.

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
            contexts[mode] = spectra_context(mz_vectors[mode], mass_tolerance)
            result = self._detect_mode(
                mode,
                (mz_vectors[mode], intensity_vectors[mode]),
                contexts[mode],
                profile,
                (database, mass_tolerance),
            )
            union = result.mask if union is None else union | result.mask
            score = result.score if score is None else np.maximum(score, result.score)
            background |= result.background
        assert union is not None and score is not None
        if not union.any():
            union, background = _with_peaks(mz_vectors, sample.ion_modes), False
            get_reporter().warning(
                "No foreground found; every spectrum is used as foreground"
            )
        sample.filtered_idx = np.flatnonzero(union).tolist()
        sample.foreground_score = score.astype(np.float32)
        sample.background_found = background and not union.all()
        return contexts

    def _detect_mode(
        self,
        mode: str,
        spectra: tuple[list[np.ndarray], list[np.ndarray]],
        context: SpectraContext,
        profile: SampleProfile,
        annotation: tuple[pd.DataFrame | None, float],
    ) -> ForegroundResult:
        """The preset's detector on one ion mode, reported; the lipid score only for mixtures."""
        mz_list, intensity_list = spectra
        coordinates = self.sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES]
        if profile.detector == "microgrid":
            result = detect_microgrid_spots(
                mz_list, intensity_list, coordinates, context
            )
        else:
            database, mass_tolerance = annotation
            lipids = _lipid_score(
                mz_list, intensity_list, context, database, mode, mass_tolerance
            )
            result = detect_foreground(
                mz_list, intensity_list, coordinates, context, profile, lipids
            )
        get_reporter().detail(
            f"Foreground ({str(mode).upper()}): {int(result.mask.sum()):,} of "
            f"{result.mask.size:,} spectra; {result.summary}"
        )
        return result


def _lipid_score(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    context: SpectraContext,
    database: pd.DataFrame | None,
    mode: str,
    mass_tolerance: float,
) -> np.ndarray | None:
    """
    Per spectrum, the share of its intensity in the database-annotated frequent recurrent ions.

    None without a database, with fewer than `LIPID_MIN_ANNOTATED` annotated ions, or when the
    annotated ions hold less than `LIPID_MIN_ENRICHMENT` times the mean intensity share of the ions
    annotated at decoy masses (`DECOY_OFFSETS` window widths away). The check is weighted by
    intensity like the score: a dense database annotates many weak ions by chance, while the
    intense true lipids dominate the score.
    """
    mz = context.features.mz
    if database is None or mz.size == 0:
        return None
    presence = context.features.presence
    frequent = presence >= FREQUENCY_THRESHOLD * presence.max()

    def share(masses: np.ndarray) -> np.ndarray:
        """Per-spectrum intensity share of the frequent ions annotated at `masses`."""
        hits = annotated_mask(database, masses, mode, mass_tolerance) & frequent
        return lipid_fraction(mz_list, intensity_list, context, hits)

    annotated = annotated_mask(database, mz, mode, mass_tolerance) & frequent
    score = share(mz)
    decoy = float(
        np.mean(
            [
                share(mz * (1 + 2 * k * mass_tolerance * 1e-6)).mean()
                for k in DECOY_OFFSETS
            ]
        )
    )
    target = float(score.mean())
    if (
        int(annotated.sum()) < LIPID_MIN_ANNOTATED
        or target < LIPID_MIN_ENRICHMENT * decoy
    ):
        get_reporter().detail(
            f"Foreground ({str(mode).upper()}): lipid fraction not used "
            f"({int(annotated.sum())} annotated recurrent ions holding {target:.1%} of the "
            f"intensity, {decoy:.1%} at decoy masses)"
        )
        return None
    return score


def _with_peaks(
    mz_vectors: dict[str, list[np.ndarray]], ion_modes: list[str]
) -> np.ndarray:
    """Spectra holding a peak in any ion mode."""
    has_peaks: np.ndarray | None = None
    for mode in ion_modes:
        peaks = np.array([mz.size > 0 for mz in mz_vectors[mode]])
        has_peaks = peaks if has_peaks is None else has_peaks | peaks
    assert has_peaks is not None
    return has_peaks
