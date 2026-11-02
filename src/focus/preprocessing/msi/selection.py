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
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.backbone import mass_scale_shifts
from focus.preprocessing.msi.references import _CalibrationReferenceSelector
from focus.preprocessing.msi.matching import recalibration_search_ppm


def select_references(
    dataset: MsiDataset,
    iter_samples: list[MsiSample],
    stored_references: dict[str, dict[str, np.ndarray]],
    mass_tolerance: int,
    detect_background: bool,
    recalibration_reference: dict[str, np.ndarray] | None,
    internal_standard_mz: dict[str, np.ndarray] | None,
    reporter: StepReporter,
) -> None:
    """Select sample references and align their mass scales."""

    # STEP 2-3: Recalibration references, one set per sample: the ions that sample's own spectra hold
    # (see _CalibrationReferenceSelector; the tissue spectra when background detection ran, all
    # spectra otherwise), always including the internal standards when configured. They are selected
    # while the payload loaded for tissue detection is in hand; step 3 puts the samples' references
    # on one mass scale (mass_scale_shifts) and reports them. A user-supplied reference serves every
    # sample as is. Recalibration runs with or without a lipid DB.
    state = ReferenceSelection({}, {}, {})
    _collect_references(
        dataset,
        iter_samples,
        state,
        mass_tolerance,
        detect_background,
        recalibration_reference,
        internal_standard_mz,
        reporter,
    )
    if recalibration_reference is None:
        scale_shift = _align_references(
            state, stored_references, mass_tolerance, internal_standard_mz
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
    mass_tolerance: int,
    detect_background: bool,
    recalibration_reference: dict[str, np.ndarray] | None,
    internal_standard_mz: dict[str, np.ndarray] | None,
    reporter: StepReporter,
) -> None:
    """Load queued payloads and retain sample-selected references and candidate pools."""
    selected, pools, candidates = state.selected, state.pools, state.candidates
    for sample in reporter.tqdm(iter_samples, *_step(2), unit="sample"):
        _, _, filtered_mz, filtered_intensity = sample.load_payload(
            annotation_db=dataset.lipid_annotation_db,
            mass_tolerance=mass_tolerance,
            detect_background=detect_background,
        )
        if recalibration_reference is None:
            selector = _CalibrationReferenceSelector(mass_tolerance)
            selector.add_sample(filtered_mz, filtered_intensity)
            selected[sample.sample_id] = selector.select(
                number_of_references=_RECALIBRATION_REFERENCES,
                required_mz=internal_standard_mz,
            )
            pools[sample.sample_id] = selector.pool
            candidates[sample.sample_id] = selector.candidates
            del selector
        del filtered_mz, filtered_intensity  # Free memory
        gc.collect()


def _align_references(
    state: ReferenceSelection,
    stored_references: dict[str, dict[str, np.ndarray]],
    mass_tolerance: int,
    internal_standard_mz: dict[str, np.ndarray] | None,
) -> dict[str, dict[str, tuple[float, bool]]]:
    """Align selected sample references to a shared mass scale in place, using cached anchors."""
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
        shifts, connected = mass_scale_shifts(
            [
                pools[s][mode]
                if pools[s].get(mode, np.array([])).size
                else selected[s][mode]
                for s in names
            ]
            + [stored_references[s][str(mode)] for s in anchors],
            recalibration_search_ppm(mass_tolerance),
            fixed=np.arange(len(names) + len(anchors)) >= len(names),
        )
        for k, s in enumerate(names):
            scale_shift[s][mode] = (
                float(shifts[k]),
                bool(connected[k]) or len(names) + len(anchors) == 1,
            )
            selected[s][mode] = selected[s][mode] / (1 + shifts[k] * 1e-6)

    return scale_shift


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
