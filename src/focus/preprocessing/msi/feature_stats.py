"""Per-spectrum and per-feature statistics on the feature grid: detections, S/N counts, noise profiles."""

import numpy as np
from numba import njit

from focus.preprocessing.msi.matching import spectrum_noise


def feature_lookup(left: np.ndarray, right: np.ndarray) -> tuple[int, np.ndarray]:
    """
    Feature index of every bin of the feature grid between the first and the last feature range.

    `left` and `right` are the first and last grid bin of each feature (:class:`MzFeatures`). Returns
    (first bin, int32 lookup); a bin outside every range holds -1. Ranges of one feature set never
    overlap (:func:`find_features`), so a bin belongs to at most one feature.
    """
    if left.size == 0:
        return 0, np.full(0, -1, dtype=np.int32)
    lo = int(left.min())
    lookup = np.full(int(right.max()) - lo + 1, -1, dtype=np.int32)
    for j in range(left.size):
        lookup[left[j] - lo : right[j] - lo + 1] = j
    return lo, lookup


@njit(cache=True)
def _feature_of(
    mz: float, log_bin_width: float, lookup_lo: int, lookup: np.ndarray
) -> int:
    """Feature holding a peak at `mz`, -1 when it falls outside every feature range."""
    if mz <= 0.0 or lookup.size == 0:
        return -1
    b = int(np.floor(np.log(mz) / log_bin_width)) - lookup_lo
    if b < 0 or b >= lookup.size:
        return -1
    return int(lookup[b])


@njit(cache=True)
def detections_per_spectrum(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    log_bin_width: float,
    lookup_lo: int,
    lookup: np.ndarray,
    n_features: int,
    recurrent_lo: int,
    recurrent: np.ndarray,
    signal_to_noise: float,
) -> np.ndarray:
    """
    Number of distinct features each spectrum holds above its own noise gate.

    The gate is median + `signal_to_noise` x sigma of the spectrum's non-recurrent peaks
    (:func:`spectrum_noise`); a spectrum without a noise estimate counts every feature peak.
    """
    n_spectra = offsets.size - 1
    counts = np.zeros(n_spectra, dtype=np.int32)
    seen = np.full(n_features, -1, dtype=np.int64)
    for s in range(n_spectra):
        start, end = offsets[s], offsets[s + 1]
        if end <= start:
            continue
        mz = flat_mz[start:end]
        intensity = flat_intensity[start:end]
        median, sigma = spectrum_noise(
            mz, intensity, recurrent_lo, recurrent, log_bin_width
        )
        gate = median + signal_to_noise * sigma if sigma > 0.0 else 0.0
        for k in range(mz.size):
            if intensity[k] < gate or intensity[k] <= 0.0:
                continue
            j = _feature_of(mz[k], log_bin_width, lookup_lo, lookup)
            if j >= 0 and seen[j] != s:
                seen[j] = s
                counts[s] += 1
    return counts


@njit(cache=True)
def feature_snr_counts(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    log_bin_width: float,
    lookup_lo: int,
    lookup: np.ndarray,
    n_features: int,
    noise_location: np.ndarray,
    noise_scale: np.ndarray,
    signal_to_noise: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """
    Per feature, the spectra whose most intense peak in the feature range reaches `signal_to_noise`.

    S/N of feature j in spectrum s is (I - noise_location[j]) / noise_scale[j], I the most intense
    peak of s in the range of j. Features with noise_scale <= 0 have no estimate and are not counted.

    Returns
    -------
    above, below : (n_features,) int64
            Spectra holding the feature at S/N >= `signal_to_noise`, and below it.
    intensity : (n_features,) float64
            Sum over the spectra of the most intense peak in the feature range.
    total : float
            Total intensity of every peak of every spectrum.
    """
    n_spectra = offsets.size - 1
    above = np.zeros(n_features, dtype=np.int64)
    below = np.zeros(n_features, dtype=np.int64)
    intensity_sum = np.zeros(n_features, dtype=np.float64)
    best = np.zeros(n_features, dtype=np.float64)
    mark = np.full(n_features, -1, dtype=np.int64)
    touched = np.empty(n_features, dtype=np.int64)
    total = 0.0
    for s in range(n_spectra):
        n_touched = 0
        for k in range(offsets[s], offsets[s + 1]):
            value = flat_intensity[k]
            total += value
            j = _feature_of(flat_mz[k], log_bin_width, lookup_lo, lookup)
            if j < 0:
                continue
            if mark[j] != s:
                mark[j] = s
                best[j] = value
                touched[n_touched] = j
                n_touched += 1
            elif value > best[j]:
                best[j] = value
        for t in range(n_touched):
            j = touched[t]
            intensity_sum[j] += best[j]
            if noise_scale[j] > 0.0:
                if (best[j] - noise_location[j]) / noise_scale[j] >= signal_to_noise:
                    above[j] += 1
                else:
                    below[j] += 1
    return above, below, intensity_sum, total


@njit(cache=True)
def noise_histograms(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    log_bin_width: float,
    recurrent_lo: int,
    recurrent: np.ndarray,
    block_lo: int,
    block_bins: int,
    n_blocks: int,
    log_lo: float,
    log_step: float,
    n_levels: int,
) -> np.ndarray:
    """
    Histogram of log10 intensity of the non-recurrent peaks, per m/z block of the feature grid.

    Block b covers grid bins [block_lo + b * block_bins, block_lo + (b + 1) * block_bins); level l
    covers log10 intensities [log_lo + l * log_step, log_lo + (l + 1) * log_step), clipped to the
    first and last level.
    """
    histogram = np.zeros((n_blocks, n_levels), dtype=np.int32)
    for s in range(offsets.size - 1):
        for k in range(offsets[s], offsets[s + 1]):
            mz = flat_mz[k]
            value = flat_intensity[k]
            if mz <= 0.0 or value <= 0.0:
                continue
            b = int(np.floor(np.log(mz) / log_bin_width))
            r = b - recurrent_lo
            if recurrent.size > 0 and 0 <= r < recurrent.size and recurrent[r]:
                continue
            block = (b - block_lo) // block_bins
            if block < 0 or block >= n_blocks:
                continue
            level = int((np.log10(value) - log_lo) / log_step)
            level = min(max(level, 0), n_levels - 1)
            histogram[block, level] += 1
    return histogram


@njit(cache=True)
def feature_intensity_fraction(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    log_bin_width: float,
    lookup_lo: int,
    lookup: np.ndarray,
    selected: np.ndarray,
) -> np.ndarray:
    """
    Per spectrum, the share of its total peak intensity in the `selected` features.

    0 for a spectrum without peaks. `selected` is a bool per feature of the lookup.
    """
    n_spectra = offsets.size - 1
    out = np.zeros(n_spectra, dtype=np.float64)
    for s in range(n_spectra):
        total = 0.0
        inside = 0.0
        for k in range(offsets[s], offsets[s + 1]):
            value = flat_intensity[k]
            if value <= 0.0:
                continue
            total += value
            j = _feature_of(flat_mz[k], log_bin_width, lookup_lo, lookup)
            if j >= 0 and selected[j]:
                inside += value
        if total > 0.0:
            out[s] = inside / total
    return out
