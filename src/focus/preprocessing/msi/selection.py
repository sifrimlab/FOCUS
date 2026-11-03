"""Select sample references and align their mass scales."""

from __future__ import annotations
from focus.preprocessing.msi.run_state import ReferenceSelection
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np

import gc

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.settings import (
    _RECALIBRATION_REFERENCES,
    _SCALE_MIN_SHARED,
    _step,
)
from focus.preprocessing.msi.payload import read_vectors
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.backbone import mass_scale_shifts
from focus.preprocessing.msi.references import _CalibrationReferenceSelector
from focus.preprocessing.msi.matching import recalibration_search_ppm


def select_references(
    dataset: MsiDataset,
    iter_samples: list[MsiSample],
    stored_references: dict[str, dict[str, np.ndarray]],
    stored_pools: dict[str, dict[str, dict[str, np.ndarray | float]]],
    mass_tolerance: float,
    recalibration_reference: dict[str, np.ndarray] | None,
    internal_standard_mz: dict[str, np.ndarray] | None,
    reporter: StepReporter,
) -> None:
    """Select sample references and align their mass scales."""

    # STEP 2-3: Recalibration references, one set per sample: the ions that sample's own spectra hold
    # (see _CalibrationReferenceSelector), selected from every spectrum before any foreground is
    # known, so matrix and background ions present everywhere are eligible calibrants. They always
    # include the internal standards when configured. Step 3 puts the samples' references on one mass
    # scale (mass_scale_shifts) and reports them. A user-supplied reference serves every sample as is.
    state = ReferenceSelection({}, {}, {})
    _collect_references(
        dataset,
        iter_samples,
        state,
        mass_tolerance,
        recalibration_reference,
        internal_standard_mz,
        reporter,
    )
    if recalibration_reference is None:
        scale_shift = _align_references(
            state, stored_references, stored_pools, mass_tolerance, internal_standard_mz
        )
        _report_references(iter_samples, state, scale_shift, reporter)

    else:
        reporter.step(*_step(3))
        reporter.detail("Using the provided recalibration reference m/z values")
        for sample in iter_samples:
            sample.recalibration_reference = recalibration_reference


def _collect_references(
    dataset: MsiDataset,
    iter_samples: list[MsiSample],
    state: ReferenceSelection,
    mass_tolerance: float,
    recalibration_reference: dict[str, np.ndarray] | None,
    internal_standard_mz: dict[str, np.ndarray] | None,
    reporter: StepReporter,
) -> None:
    """Load queued payloads and retain sample-selected references and candidate pools."""
    selected, pools, candidates = state.selected, state.pools, state.candidates
    for sample in reporter.tqdm(iter_samples, *_step(2), unit="sample"):
        if recalibration_reference is None:
            mz_vectors, intensity_vectors = read_vectors(sample)
            selector = _CalibrationReferenceSelector(mass_tolerance)
            selector.add_sample(mz_vectors, intensity_vectors)
            selected[sample.sample_id] = selector.select(
                number_of_references=_RECALIBRATION_REFERENCES,
                required_mz=internal_standard_mz,
            )
            pools[sample.sample_id] = selector.pool
            candidates[sample.sample_id] = selector.candidates
            del selector, mz_vectors, intensity_vectors  # Free memory
            gc.collect()


def _align_references(
    state: ReferenceSelection,
    stored_references: dict[str, dict[str, np.ndarray]],
    stored_pools: dict[str, dict[str, dict[str, np.ndarray | float]]],
    mass_tolerance: float,
    internal_standard_mz: dict[str, np.ndarray] | None,
) -> dict[str, dict[str, tuple[float, bool]]]:
    """
    Align selected sample references and pools to a shared mass scale in place, using cached anchors.

    A cached sample anchors the alignment with its stored pool (its references when the file holds
    none), the same ions a fresh run compares, so a resumed run reaches the shifts of a fresh one.
    """
    selected, pools = state.selected, state.pools
    scale_shift: dict[str, dict[str, tuple[float, bool]]] = {s: {} for s in selected}
    for mode in MsiIonMode.list():
        names = [s for s in selected if selected[s].get(mode, np.array([])).size]
        anchors = [
            s
            for s in stored_references
            if np.asarray(stored_references[s].get(str(mode), [])).size
        ]
        if not names or (
            internal_standard_mz is not None and mode in internal_standard_mz
        ):
            continue
        # Cached samples are a prefix of the dataset order, so anchors first gives every pair the
        # orientation of a fresh run.
        stored = [
            _anchor(stored_references, stored_pools, s, str(mode)) for s in anchors
        ]
        shifts, connected = mass_scale_shifts(
            [pool for pool, _ in stored]
            + [
                pools[s][mode]
                if pools[s].get(mode, np.array([])).size
                else selected[s][mode]
                for s in names
            ],
            recalibration_search_ppm(mass_tolerance),
            fixed=np.arange(len(anchors) + len(names)) < len(anchors),
            fixed_shifts=np.array([shift for _, shift in stored] + [0.0] * len(names)),
        )
        for k, s in enumerate(names, start=len(anchors)):
            scale_shift[s][mode] = (
                float(shifts[k]),
                bool(connected[k]) or len(names) + len(anchors) == 1,
            )
            selected[s][mode] = selected[s][mode] / (1 + shifts[k] * 1e-6)

    return scale_shift


def _anchor(
    stored_references: dict[str, dict[str, np.ndarray]],
    stored_pools: dict[str, dict[str, dict[str, np.ndarray | float]]],
    sample_id: str,
    mode: str,
) -> tuple[np.ndarray, float]:
    """
    A cached sample's calibrant pool on its own scale and its mass-scale shift, as a fresh run uses
    them; its stored references (already on the common scale, shift 0) when no pool was stored.
    """
    stored = stored_pools.get(sample_id, {}).get(mode)
    if stored is not None and np.asarray(stored["pool"]).size:
        return np.asarray(stored["pool"], dtype=np.float64), float(stored["shift_ppm"])
    return np.asarray(stored_references[sample_id][mode], dtype=np.float64), 0.0


def _report_references(
    iter_samples: list[MsiSample],
    state: ReferenceSelection,
    scale_shift: dict[str, dict[str, tuple[float, bool]]],
    reporter: StepReporter,
) -> None:
    """Assign references and report connectivity/count warnings in sample and ion-mode order."""
    selected, candidates = state.selected, state.candidates
    for sample in reporter.tqdm(iter_samples, *_step(3), unit="sample"):
        sample.recalibration_reference = selected[sample.sample_id]
        sample.recalibration_pool = {
            str(mode): {
                "pool": np.asarray(pool, dtype=np.float64),
                "shift_ppm": scale_shift[sample.sample_id].get(mode, (0.0, True))[0],
            }
            for mode, pool in state.pools[sample.sample_id].items()
            if np.asarray(pool).size
        }
        for mode in sample.ion_modes:
            masses = selected[sample.sample_id].get(mode, np.array([]))
            listed = ", ".join(f"{m:.4f}" for m in masses)
            if len(masses) == 0:
                found, isolated, matched, wrong = candidates[sample.sample_id].get(
                    mode, (0, 0, 0.0, 0.0)
                )
                reporter.warning(
                    f"No recalibration reference found ({str(mode).upper()}): {found} m/z features, "
                    f"{isolated} isolated in the search window (trial candidates matched in "
                    f"{matched:.0%} of the spectra, {wrong:.0%} of the matches on another ion, "
                    f"medians); m/z values are not recalibrated"
                )
                continue
            shift, is_connected = scale_shift[sample.sample_id].get(mode, (0.0, True))
            scale = f"; mass scale shifted {shift:+.1f} ppm" if shift != 0.0 else ""
            if not is_connected:
                reporter.warning(
                    f"Recalibration references ({str(mode).upper()}): {listed}; fewer than "
                    f"{_SCALE_MIN_SHARED} calibrant ions shared with any other sample, so its mass "
                    f"scale is not aligned with theirs"
                )
            elif len(masses) < _RECALIBRATION_REFERENCES:
                reporter.warning(
                    f"Only {len(masses)} recalibration reference(s) found ({str(mode).upper()}): {listed}{scale}"
                )
            else:
                reporter.detail(
                    f"Recalibration references ({str(mode).upper()}): {listed}{scale}"
                )
