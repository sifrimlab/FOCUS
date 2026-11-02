"""Build logarithmic m/z peak densities and detect recurring mass features."""

import numpy as np

from dataclasses import dataclass
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.stats import poisson as poisson_distribution


from focus.preprocessing.msi.settings import (
    _FEATURE_ALPHA,
    _FEATURE_BINS_PER_TOLERANCE,
    _FEATURE_FLOOR_BLOCK,
    _FEATURE_FLOOR_TOLERANCES,
    _FEATURE_KERNEL_FWHM,
    _FEATURE_MIN_PEAKS,
    _NO_RECURRENCE,
)
from focus.preprocessing.msi.matching import _flatten_spectra, _peak_blocks
from focus.preprocessing.msi.density import density_feature_ranges, valley_heights


def peak_histogram(
    spectra: list[np.ndarray], log_bin_width: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Reduce one sample + ion mode to an m/z occurrence histogram on a logarithmic grid.

    The bin of a peak is ``floor(ln(m) / log_bin_width)``, so bins have a constant width in ppm.
    Every peak is counted, whatever its intensity: what separates an ion from noise is that it recurs
    at the same m/z (:func:`find_features`). Spectra are consumed in blocks of at most
    ``_BLOCK_PEAKS`` peaks, and the dense accumulator spans only the masses this sample actually
    contains (it is grown on demand), so peak memory is bounded by the block size plus the sample's own
    mass span, never by its spectrum or peak count. Only occupied bins are returned. Binning uses
    ``np.bincount``, a single linear pass with no sort.

    Parameters
    ----------
    spectra : list[np.ndarray]
            One M/Z array per spectrum.
    log_bin_width : float
            Bin width in natural-log units.

    Returns
    -------
    bins : np.ndarray[np.int64]
            Ascending occupied bin indices.
    counts : np.ndarray[np.int64]
            Occurrences per bin.
    mz_sums : np.ndarray[np.float64]
            Sum of the raw m/z values per bin; divide by `counts` for the bin's exact mean mass.
    """

    empty = (
        np.array([], dtype=np.int64),
        np.array([], dtype=np.int64),
        np.array([], dtype=np.float64),
    )
    if not spectra:
        return empty

    bins_lo = 0
    accumulators = None  # counts, mz_sums

    for start, stop in _peak_blocks(spectra):
        block_mz, _, _ = _flatten_spectra(spectra[start:stop])
        # ln is undefined at or below zero, and such m/z are invalid for MSI anyway.
        block_mz = block_mz[block_mz > 0.0]
        if block_mz.size == 0:
            continue

        bin_idx = np.floor(np.log(block_mz) / log_bin_width).astype(np.int64)
        block_lo, block_hi = int(bin_idx.min()), int(bin_idx.max())

        if accumulators is None:
            bins_lo = block_lo
            size = block_hi - block_lo + 1
            accumulators = [
                np.zeros(size, dtype=np.int64),
                np.zeros(size, dtype=np.float64),
            ]
        elif block_lo < bins_lo or block_hi > bins_lo + accumulators[0].size - 1:
            # Grow the accumulators to span the widened mass range, preserving alignment.
            new_lo = min(bins_lo, block_lo)
            new_size = max(bins_lo + accumulators[0].size - 1, block_hi) - new_lo + 1
            offset = bins_lo - new_lo
            grown = []
            for values in accumulators:
                larger = np.zeros(new_size, dtype=values.dtype)
                larger[offset : offset + values.size] = values
                grown.append(larger)
            accumulators = grown
            bins_lo = new_lo

        bin_idx -= bins_lo
        size = accumulators[0].size
        accumulators[0] += np.bincount(bin_idx, minlength=size)
        accumulators[1] += np.bincount(bin_idx, weights=block_mz, minlength=size)
        del block_mz, bin_idx

    if accumulators is None:
        return empty

    occupied = np.flatnonzero(accumulators[0])
    return occupied + bins_lo, accumulators[0][occupied], accumulators[1][occupied]


def feature_bin_width(mass_tolerance: float) -> float:
    """Bin width, in natural-log units, of the feature grid: a tenth of the mass tolerance."""
    return mass_tolerance * 1e-6 / _FEATURE_BINS_PER_TOLERANCE


@dataclass
class MzFeatures:
    """The features of one peak histogram (see find_features), in ascending m/z."""

    mz: np.ndarray  # mean m/z of the peaks within half a tolerance of the maximum
    presence: np.ndarray  # peaks above the background, per spectrum (at most 1)
    left: np.ndarray  # first and last bin of the feature's range on the feature grid
    right: np.ndarray
    floor: np.ndarray  # local floor of the density at the maximum, in peaks per bin
    low: (
        np.ndarray
    )  # first and last bin before the valleys toward the neighbouring features
    high: np.ndarray  # (the range without the one-tolerance cap)
    peaks: np.ndarray  # peaks in the range

    @classmethod
    def empty(cls) -> "MzFeatures":
        """Construct an empty feature result with the established array dtypes."""
        none = np.array([], dtype=np.int64)
        return cls(
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            none,
            none,
            np.array([], dtype=np.float64),
            none,
            none,
            np.array([], dtype=np.float64),
        )


def find_features(
    bins: np.ndarray,
    counts: np.ndarray,
    mz_sums: np.ndarray,
    n_spectra: int,
    mass_tolerance: float,
    kernel_fwhm: float = _FEATURE_KERNEL_FWHM,
) -> MzFeatures:
    """
    The ions of a peak histogram: the maxima of its smoothed density that rise above the local background.

    A real ion recurs at the same m/z across spectra; noise peaks fall at random m/z and form a
    background. The histogram (:func:`peak_histogram` on the grid of :func:`feature_bin_width`, every
    peak counted) is smoothed with a Gaussian kernel whose FWHM is `kernel_fwhm` tolerances (by default
    ``_FEATURE_KERNEL_FWHM``), and every local maximum is a candidate. A candidate's range reaches the lowest point between it and
    each neighbouring candidate (the valley) and at most one tolerance from it. Its background, in peaks
    per bin, is the larger of

    - the local floor: the running median over ``_FEATURE_FLOOR_TOLERANCES`` tolerances on either
      side, of the peak counts in blocks of one tolerance, and
    - the smoothed density at the valley toward each higher neighbouring candidate (the col, as in
      topographic prominence), so a maximum on the flank of a larger one must rise above the col
      between them, while the larger one is measured against the floor.

    It is a feature when its range holds at least ``_FEATURE_MIN_PEAKS`` peaks and more than background
    x range width at Poisson tail probability below ``_FEATURE_ALPHA``. Rejected candidates are removed
    and the ranges recomputed, so their bins join the neighbouring features, until every candidate
    passes. A feature's m/z is the mean m/z of the peaks within half a tolerance of its maximum, a
    window symmetric about the maximum so the background does not pull it; its presence is (peaks in
    range - background x range width) / `n_spectra`, at most 1.
    """
    if bins.size == 0 or n_spectra == 0:
        return MzFeatures.empty()
    reach = _FEATURE_BINS_PER_TOLERANCE  # one tolerance, in bins
    # Margins so that no range reaches the ends of the grid.
    lo = int(bins[0]) - 2 * reach
    size = int(bins[-1]) + 2 * reach - lo + 1
    dense = np.zeros(size, dtype=np.float64)
    dense[bins - lo] = counts
    smooth = gaussian_filter1d(
        dense,
        kernel_fwhm * reach / (2 * np.sqrt(2 * np.log(2))),
        mode="constant",
        cval=0.0,
        truncate=4.0,
    )
    # Local maxima, strict on the low-mass side so a plateau yields one maximum.
    padded = np.concatenate(([0.0], smooth, [0.0]))
    maxima = np.flatnonzero(
        (padded[1:-1] > padded[:-2]) & (padded[1:-1] >= padded[2:]) & (smooth > 0)
    )
    block = _FEATURE_FLOOR_BLOCK * reach
    blocks = np.add.reduceat(dense, np.arange(0, size, block))
    window = 2 * (_FEATURE_FLOOR_TOLERANCES // _FEATURE_FLOOR_BLOCK) + 1
    floor = np.repeat(
        median_filter(blocks, size=window, mode="reflect") / block, block
    )[:size]
    prefix = np.concatenate(([0.0], np.cumsum(dense)))

    while maxima.size:
        left, right = density_feature_ranges(smooth, maxima, reach)
        # Col between each pair of neighbouring maxima; it counts for the lower of the two.
        height = smooth[maxima]
        between = valley_heights(smooth, maxima)
        col = np.zeros(maxima.size, dtype=np.float64)
        col[1:] = np.where(height[:-1] > height[1:], between, 0.0)
        col[:-1] = np.maximum(
            col[:-1], np.where(height[1:] > height[:-1], between, 0.0)
        )
        expected = np.maximum(floor[maxima], col) * (right - left + 1)
        peaks = prefix[right + 1] - prefix[left]
        keep = (peaks >= _FEATURE_MIN_PEAKS) & (
            poisson_distribution.sf(peaks - 1, np.maximum(expected, 1e-12))
            < _FEATURE_ALPHA
        )
        if keep.all():
            break
        maxima = maxima[keep]
    if maxima.size == 0:
        return MzFeatures.empty()

    sums = np.zeros(size, dtype=np.float64)
    sums[bins - lo] = mz_sums
    sum_prefix = np.concatenate(([0.0], np.cumsum(sums)))
    half = np.minimum(np.minimum(maxima - left, right - maxima), reach // 2)
    core = prefix[maxima + half + 1] - prefix[maxima - half]
    core_sum = sum_prefix[maxima + half + 1] - sum_prefix[maxima - half]
    range_sum = sum_prefix[right + 1] - sum_prefix[left]
    mz = np.where(core > 0, core_sum / np.maximum(core, 1), range_sum / peaks)
    presence = np.clip((peaks - expected) / n_spectra, 0.0, 1.0)
    low, high = density_feature_ranges(smooth, maxima, size)
    return MzFeatures(
        mz, presence, left + lo, right + lo, floor[maxima], low + lo, high + lo, peaks
    )


def recurrence_map(
    features: MzFeatures, log_bin_width: float, search_ppm: float
) -> tuple[int, np.ndarray, float]:
    """
    The m/z ranges of a sample where its peaks are ions rather than noise, for :func:`noise_threshold`.

    Every feature range, widened by the calibrant search window `search_ppm` on each side so peaks that
    drift moved off the range still count, marked on the feature grid. Returns (first bin, bool mask,
    bin width); no features give the empty map, under which every peak counts as noise.
    """
    if features.mz.size == 0:
        return _NO_RECURRENCE
    pad = int(np.ceil(np.log1p(search_ppm * 1e-6) / log_bin_width))
    lo = int(features.left.min()) - pad
    size = int(features.right.max()) + pad - lo + 1
    delta = np.zeros(size + 1, dtype=np.int64)
    np.add.at(delta, features.left - pad - lo, 1)
    np.add.at(delta, features.right + pad - lo + 1, -1)
    return lo, np.cumsum(delta[:-1]) > 0, log_bin_width
