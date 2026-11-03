"""Filter one sample's m/z features by S/N against a noise profile measured on its background spectra."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit

from focus.preprocessing.msi.feature_stats import (
    feature_lookup,
    feature_snr_counts,
    noise_histograms,
)
from focus.preprocessing.msi.features import MzFeatures
from focus.preprocessing.msi.matching import _flatten_spectra
from focus.preprocessing.msi.settings import (
    _FEATURE_BINS_PER_TOLERANCE,
    _FEATURE_FLOOR_BLOCK,
    _FEATURE_FLOOR_TOLERANCES,
)
from focus.preprocessing.msi.technology import (
    SIGNAL_TO_NOISE,
    SNR_MIN_BACKGROUND_FRACTION,
    SNR_MIN_BACKGROUND_SPECTRA,
    SNR_MIN_NOISE_PEAKS,
)

# log10-intensity levels of the noise histograms.
_NOISE_LEVELS = 256


@dataclass
class SnrResult:
    """Outcome of the S/N filter for the features of one sample and ion mode."""

    keep: np.ndarray  # (n,) bool, kept features
    judged: (
        np.ndarray
    )  # (n,) bool, features with a noise estimate and spectra holding them
    removed_tic: float  # share of the foreground TIC carried by the removed features
    skipped: str | None  # why the filter did not run, None when it ran


@njit(cache=True)
def _below(
    value: float,
    cumulative: np.ndarray,
    counts: np.ndarray,
    total: float,
    log_lo: float,
    log_step: float,
) -> float:
    """Number of histogram peaks at or below `value`, uniform in log10 within a level."""
    if value <= 0.0:
        return 0.0
    position = (np.log10(value) - log_lo) / log_step
    if position <= 0.0:
        return 0.0
    if position >= counts.size:
        return total
    level = int(position)
    return float(cumulative[level] + counts[level] * (position - level))


@njit(cache=True)
def _window_quantiles(
    histogram: np.ndarray,
    blocks: np.ndarray,
    half_window: int,
    log_lo: float,
    log_step: float,
    min_peaks: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Median and 1.4826 x MAD of the noise intensities in the window of blocks around each of `blocks`.

    Intensities are taken as uniform in log10 within a histogram level. A window with fewer than
    `min_peaks` peaks gives (0, 0), no estimate.
    """
    n_blocks, n_levels = histogram.shape
    location = np.zeros(blocks.size, dtype=np.float64)
    scale = np.zeros(blocks.size, dtype=np.float64)
    counts = np.zeros(n_levels, dtype=np.float64)
    cumulative = np.zeros(n_levels + 1, dtype=np.float64)
    for f in range(blocks.size):
        lo = max(blocks[f] - half_window, 0)
        hi = min(blocks[f] + half_window, n_blocks - 1)
        counts[:] = 0.0
        for b in range(lo, hi + 1):
            counts += histogram[b]
        total = counts.sum()
        if total < min_peaks:
            continue
        cumulative[1:] = np.cumsum(counts)

        # Median: invert the cumulative count at half the total.
        low, high = log_lo, log_lo + n_levels * log_step
        for _ in range(60):
            middle = 0.5 * (low + high)
            if (
                _below(10.0**middle, cumulative, counts, total, log_lo, log_step)
                < 0.5 * total
            ):
                low = middle
            else:
                high = middle
        median = 10.0 ** (0.5 * (low + high))
        # MAD: the half-width d around the median that holds half of the peaks.
        low, high = 0.0, 10.0 ** (log_lo + n_levels * log_step)
        for _ in range(60):
            middle = 0.5 * (low + high)
            if (
                _below(median + middle, cumulative, counts, total, log_lo, log_step)
                - _below(median - middle, cumulative, counts, total, log_lo, log_step)
                < 0.5 * total
            ):
                low = middle
            else:
                high = middle
        location[f] = median
        scale[f] = 1.4826 * 0.5 * (low + high)
    return location, scale


def noise_profile(
    background_mz: list[np.ndarray],
    background_intensity: list[np.ndarray],
    recurrence: tuple[int, np.ndarray, float],
    feature_mz: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Noise location and scale at each feature m/z, from the non-recurrent peaks of background spectra.

    The peaks outside the sample's recurrent m/z ranges (`recurrence`, :func:`recurrence_map`) are
    counted per block of `_FEATURE_FLOOR_BLOCK` tolerances on the feature grid; at a feature, the
    profile is the median and 1.4826 x MAD of the peaks in the blocks within
    `_FEATURE_FLOOR_TOLERANCES` tolerances of it, the layout of the local floor of
    :func:`find_features`. Features whose window holds fewer than `SNR_MIN_NOISE_PEAKS` noise peaks
    get (0, 0), no estimate.
    """
    recurrent_lo, recurrent, log_bin_width = recurrence
    none = np.zeros(feature_mz.size, dtype=np.float64)
    flat_mz, flat_intensity, offsets = _flatten_spectra(
        background_mz, background_intensity
    )
    positive = (flat_mz > 0) & (flat_intensity > 0)
    if feature_mz.size == 0 or not positive.any():
        return none, none.copy()
    block_bins = _FEATURE_FLOOR_BLOCK * _FEATURE_BINS_PER_TOLERANCE
    grid = np.floor(np.log(flat_mz[positive]) / log_bin_width).astype(np.int64)
    feature_grid = np.floor(np.log(feature_mz) / log_bin_width).astype(np.int64)
    block_lo = int(min(grid.min(), feature_grid.min()))
    n_blocks = int((max(grid.max(), feature_grid.max()) - block_lo) // block_bins) + 1
    log_values = np.log10(flat_intensity[positive])
    log_lo = float(log_values.min())
    log_step = max(float(log_values.max()) - log_lo, 1e-6) / _NOISE_LEVELS
    histogram = noise_histograms(
        flat_mz,
        flat_intensity,
        offsets,
        log_bin_width,
        recurrent_lo,
        recurrent,
        block_lo,
        block_bins,
        n_blocks,
        log_lo,
        log_step,
        _NOISE_LEVELS,
    )
    return _window_quantiles(
        histogram,
        (feature_grid - block_lo) // block_bins,
        _FEATURE_FLOOR_TOLERANCES // _FEATURE_FLOOR_BLOCK,
        log_lo,
        log_step,
        SNR_MIN_NOISE_PEAKS,
    )


def snr_filter(
    features: MzFeatures,
    candidate: np.ndarray,
    protected: np.ndarray,
    foreground_mz: list[np.ndarray],
    foreground_intensity: list[np.ndarray],
    background_mz: list[np.ndarray],
    background_intensity: list[np.ndarray],
    background_found: bool,
    n_spectra: int,
    recurrence: tuple[int, np.ndarray, float],
) -> SnrResult:
    """
    Keep the `candidate` features whose median S/N, over the foreground spectra holding them, is at
    least `SIGNAL_TO_NOISE`; `protected` features (annotated) are always kept.

    The noise is measured on the background spectra only (:func:`noise_profile`), so the foreground
    never enters its own noise estimate. The filter is skipped, every candidate kept, without an
    independent background: when the detector separated none (`background_found`), when there are
    fewer than `SNR_MIN_BACKGROUND_SPECTRA` background spectra or `SNR_MIN_BACKGROUND_FRACTION` of
    the `n_spectra`, or when no feature window holds `SNR_MIN_NOISE_PEAKS` noise peaks. A feature
    without a noise estimate is kept.

    The median S/N reaches the threshold exactly when the spectra at or above it are at least as many
    as those below, so only these two counts are kept per feature.
    """
    n = candidate.size
    keep = candidate.copy()
    judged = np.zeros(n, dtype=bool)
    required = max(SNR_MIN_BACKGROUND_SPECTRA, SNR_MIN_BACKGROUND_FRACTION * n_spectra)
    if not background_found:
        return SnrResult(
            keep, judged, 0.0, "no background separated from the foreground"
        )
    if len(background_mz) < required:
        return SnrResult(
            keep,
            judged,
            0.0,
            f"{len(background_mz):,} background spectra, fewer than {int(np.ceil(required)):,}",
        )
    index = np.flatnonzero(candidate)
    location, scale = noise_profile(
        background_mz, background_intensity, recurrence, features.mz[index]
    )
    if not (scale > 0).any():
        return SnrResult(
            keep,
            judged,
            0.0,
            f"fewer than {SNR_MIN_NOISE_PEAKS} background noise peaks around every feature",
        )
    lookup_lo, lookup = feature_lookup(features.left[index], features.right[index])
    flat_mz, flat_intensity, offsets = _flatten_spectra(
        foreground_mz, foreground_intensity
    )
    above, below, intensity, total = feature_snr_counts(
        flat_mz,
        flat_intensity,
        offsets,
        recurrence[2],
        lookup_lo,
        lookup,
        index.size,
        location,
        scale,
        SIGNAL_TO_NOISE,
    )
    held = (above + below) > 0
    judged[index] = (scale > 0) & held
    passes = (above >= below) | ~judged[index] | protected[index]
    keep[index] = passes
    removed_tic = float(intensity[~passes].sum() / total) if total > 0 else 0.0
    return SnrResult(keep, judged, removed_tic, None)
