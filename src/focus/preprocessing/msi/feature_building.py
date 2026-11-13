"""Recalibrate, detect the foreground and select each sample's m/z features, then build the backbone."""

from __future__ import annotations

import gc
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.backbone import SampleFeatures, _BackboneBuilder
from focus.preprocessing.msi.features import (
    feature_bin_width,
    feature_widths,
    find_features,
    peak_histogram,
)
from focus.preprocessing.msi.intensity_filter import snr_filter
from focus.preprocessing.msi.lipid_database import (
    annotate_masses,
    annotated_mask,
    chance_annotations,
)
from focus.preprocessing.msi.sample import MsiSample
from focus.preprocessing.msi.settings import _step
from focus.preprocessing.msi.technology import (
    DECOY_OFFSETS,
    DECOY_WARN_CHANCE,
    FREQUENCY_THRESHOLD,
    SNR_WARN_TIC_FRACTION,
    SampleProfile,
)
from focus.preprocessing.msi.tissue import SpectraContext
from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


def select_sample_features(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    foreground: np.ndarray,
    background_found: bool,
    context: SpectraContext,
    mass_tolerance: float,
    database: pd.DataFrame | None,
    ion_mode: str,
) -> SampleFeatures:
    """
    The m/z features of one sample and ion mode (docs/scientific/msi_methods.md §5).

    1. Frequency: the recurrent ions of the foreground spectra (:func:`find_features`, no intensity)
       present in at least `FREQUENCY_THRESHOLD` times the spectra of the most present one.
    2. Annotation of those features, with the number expected by chance (decoy masses).
    3. S/N filter against the background noise profile (:func:`snr_filter`); annotated features are
       kept whatever their S/N.
    4. Width of every kept feature (:func:`feature_widths`).
    """
    reporter = get_reporter()
    label = str(ion_mode).upper()
    log_bin_width = feature_bin_width(mass_tolerance)
    fg_index = np.flatnonzero(foreground)
    fg_mz = [mz_list[i] for i in fg_index]
    bins, counts, mz_sums = peak_histogram(fg_mz, log_bin_width)
    features = find_features(bins, counts, mz_sums, len(fg_mz), mass_tolerance)
    if features.mz.size == 0:
        reporter.detail(f"m/z features ({label}): none found")
        empty = np.array([], dtype=np.float64)
        return SampleFeatures(empty, empty, empty, np.array([], dtype=bool))
    frequent = features.presence >= FREQUENCY_THRESHOLD * features.presence.max()

    annotated = (
        annotated_mask(database, features.mz, ion_mode, mass_tolerance) & frequent
    )
    if database is not None:
        chance = chance_annotations(
            database, features.mz[frequent], ion_mode, mass_tolerance, DECOY_OFFSETS
        )
        n_annotated = int(annotated.sum())
        message = (
            f"Annotation ({label}): {n_annotated:,} of {int(frequent.sum()):,} features, "
            f"about {chance:.0f} expected by chance"
        )
        if n_annotated and chance > DECOY_WARN_CHANCE * n_annotated:
            reporter.warning(
                message
                + "; the database matches noise at this tolerance, so annotated "
                "features are not a reliable exemption from the S/N filter"
            )
        else:
            reporter.detail(message)

    background = ~foreground & np.array([mz.size > 0 for mz in mz_list])
    bg_index = np.flatnonzero(background)
    snr = snr_filter(
        features,
        frequent,
        annotated,
        fg_mz,
        [intensity_list[i] for i in fg_index],
        [mz_list[i] for i in bg_index],
        [intensity_list[i] for i in bg_index],
        background_found,
        len(mz_list),
        context.recurrence,
    )
    keep = snr.keep
    summary = (
        f"m/z features ({label}): {features.mz.size:,} found, {int(frequent.sum()):,} present in "
        f"at least {FREQUENCY_THRESHOLD:g} times the spectra of the most present one"
    )
    if snr.skipped is not None:
        reporter.detail(f"{summary}; S/N filter skipped: {snr.skipped}")
    else:
        removed = int(frequent.sum() - keep.sum())
        message = (
            f"{summary}, {int(keep.sum()):,} kept at median S/N 3 against the background "
            f"noise ({removed:,} removed, {snr.removed_tic:.1%} of the foreground TIC; "
            f"{int(snr.judged.sum()):,} judged)"
        )
        if snr.removed_tic > SNR_WARN_TIC_FRACTION:
            reporter.warning(
                message
                + "; the background may not represent the noise of the foreground"
            )
        else:
            reporter.detail(message)
    sigma = feature_widths(features, bins, counts, log_bin_width)
    return SampleFeatures(
        features.mz[keep], features.presence[keep], sigma[keep], annotated[keep]
    )


def build_features(
    dataset: MsiDataset,
    iter_samples: list[MsiSample],
    has_partial_cache: bool,
    mass_tolerance: float,
    profile: SampleProfile,
    reporter: StepReporter,
) -> None:
    """Select every sample's features in one pass over its recalibrated spectra, then merge them."""
    if has_partial_cache:
        # The backbone and annotations come from the cached per-sample files; the queued samples
        # are recalibrated and their foreground detected while they are binned.
        for index in (4, 5, 6):
            reporter.step(*_step(index), cached=True)
        return

    backbone = _BackboneBuilder(mass_tolerance)
    for sample in reporter.tqdm(iter_samples, *_step(4), unit="sample"):
        mz_vectors, intensity_vectors = sample.load_payload(mass_tolerance)
        contexts = sample.foreground.detect(
            mz_vectors,
            intensity_vectors,
            mass_tolerance,
            profile,
            dataset.lipid_annotation_db,
        )
        foreground = sample.foreground_mask
        for mode in sample.ion_modes:
            backbone.add(
                mode,
                select_sample_features(
                    mz_vectors[mode],
                    intensity_vectors[mode],
                    foreground,
                    sample.background_found,
                    contexts[mode],
                    mass_tolerance,
                    dataset.lipid_annotation_db,
                    mode,
                ),
            )
        del mz_vectors, intensity_vectors, contexts
        gc.collect()

    reporter.step(*_step(5))
    built = backbone.build()
    # Stored as .var (float32) and binned from these values, so a resumed run, which reads them back
    # from .var, bins every spectrum exactly as a fresh run.
    dataset.reference_mz = {mode: built[mode].mz.astype(np.float32) for mode in built}
    dataset.reference_sigma = {
        mode: built[mode].sigma.astype(np.float32) for mode in built
    }
    del backbone
    gc.collect()
    reporter.detail(
        f"Reference backbone: {len(dataset.reference_mz.get(MsiIonMode.POSITIVE, []))} m/z values (POSITIVE), "
        f"{len(dataset.reference_mz.get(MsiIonMode.NEGATIVE, []))} m/z values (NEGATIVE)"
    )
    annotate_backbone(dataset, mass_tolerance, reporter)


def annotate_backbone(
    dataset: MsiDataset, mass_tolerance: float, reporter: StepReporter
) -> None:
    """Annotate the merged backbone m/z with the lipid database."""
    reporter.step(*_step(6))
    dataset.lipid_annotations = {}
    if dataset.lipid_annotation_db is not None:
        for mode in dataset.reference_mz.keys():
            dataset.lipid_annotations[mode] = annotate_masses(
                dataset.lipid_annotation_db,
                dataset.reference_mz[mode],
                mode,
                mass_tolerance=mass_tolerance,
            )
