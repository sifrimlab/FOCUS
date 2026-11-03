"""Match measured ions to references and estimate robust ppm offset histograms."""

from collections.abc import Callable, Iterator

import numpy as np

from numba import njit
from scipy.ndimage import gaussian_filter1d
from scipy.stats import poisson as poisson_distribution


from focus.preprocessing.msi.settings import (
    _BLOCK_PEAKS,
    _NOISE_MIN_PEAKS,
    _NO_RECURRENCE,
    _RECAL_MIN_SEARCH_PPM,
    _RECAL_OFFSET_ALPHA,
    _RECAL_OFFSET_CHANCE,
    _RECAL_OFFSET_MIN_REFERENCES,
    _RECAL_SEARCH_FACTOR,
    _SIGNAL_TO_NOISE,
)


def recalibration_search_ppm(mass_tolerance: float) -> float:
    """Half-width, in ppm, of the line-level calibrant search window."""
    return float(max(_RECAL_SEARCH_FACTOR * mass_tolerance, _RECAL_MIN_SEARCH_PPM))


def _peak_blocks(
    spectra: list[np.ndarray], max_peaks: int = _BLOCK_PEAKS
) -> Iterator[tuple[int, int]]:
    """Consecutive (start, stop) spectrum ranges holding at most `max_peaks` peaks (at least one spectrum each)."""
    start, peaks = 0, 0
    for k, spectrum in enumerate(spectra):
        if k > start and peaks + spectrum.size > max_peaks:
            yield start, k
            start, peaks = k, 0
        peaks += spectrum.size
    if start < len(spectra):
        yield start, len(spectra)


@njit(cache=True)
def spectrum_noise(
    mz: np.ndarray,
    intensity: np.ndarray,
    recurrent_lo: int,
    recurrent: np.ndarray,
    log_bin_width: float,
) -> tuple[float, float]:
    """
    Location and scale of one spectrum's noise: median and 1.4826 x MAD of its noise peaks.

    Noise peaks are the peaks outside the recurrent m/z ranges of the sample: `recurrent` is a bool
    mask over the log bins ``floor(ln(m) / log_bin_width)`` starting at `recurrent_lo`
    (:func:`recurrence_map`); with an empty mask every peak counts. Fewer than `_NOISE_MIN_PEAKS`
    noise peaks give (0, 0), no estimate.
    """
    values = np.empty(intensity.size, dtype=np.float64)
    n = 0
    for k in range(intensity.size):
        if recurrent.size > 0 and mz[k] > 0.0:
            b = int(np.floor(np.log(mz[k]) / log_bin_width)) - recurrent_lo
            if 0 <= b < recurrent.size and recurrent[b]:
                continue
        values[n] = intensity[k]
        n += 1
    if n < _NOISE_MIN_PEAKS:
        return 0.0, 0.0
    noise = values[:n]
    median = np.median(noise)
    return median, 1.4826 * np.median(np.abs(noise - median))


@njit(cache=True)
def noise_threshold(
    mz: np.ndarray,
    intensity: np.ndarray,
    recurrent_lo: int,
    recurrent: np.ndarray,
    log_bin_width: float,
    signal_to_noise: float,
) -> float:
    """
    Median + `signal_to_noise` x 1.4826 x MAD of the intensities of one spectrum's noise peaks
    (:func:`spectrum_noise`); 0, no gate, without a noise estimate.
    """
    median, sigma = spectrum_noise(
        mz, intensity, recurrent_lo, recurrent, log_bin_width
    )
    if sigma <= 0.0 and median <= 0.0:
        return 0.0
    return median + signal_to_noise * sigma


def _flatten_spectra(
    mz_list: list[np.ndarray], intensity_list: list[np.ndarray] | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Concatenate spectra into float64 arrays plus CSR-style offsets (len = n_spectra + 1)."""
    lengths = np.fromiter((m.size for m in mz_list), dtype=np.int64, count=len(mz_list))
    offsets = np.zeros(lengths.size + 1, dtype=np.int64)
    np.cumsum(lengths, out=offsets[1:])
    if offsets[-1] == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty, offsets
    flat_mz = np.concatenate(mz_list).astype(np.float64, copy=False)
    flat_intensity = (
        np.concatenate(intensity_list).astype(np.float64, copy=False)
        if intensity_list is not None
        else np.empty(0, dtype=np.float64)
    )
    return flat_mz, flat_intensity, offsets


@njit(cache=True)
def match_references(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    reference_mz: np.ndarray,
    centre_ppm: np.ndarray,
    window_ppm: float,
    signal_to_noise: float,
    recurrent_lo: int,
    recurrent: np.ndarray,
    log_bin_width: float,
) -> np.ndarray:
    """
    Match every reference m/z in every spectrum.

    For spectrum s and reference j the search window is centred at
    `reference_mz[j] * (1 + centre_ppm[s, j] * 1e-6)` with half-width `window_ppm` ppm of the
    reference. The most intense peak in the window is the match, provided it reaches the spectrum's
    `noise_threshold` (recurrence map `recurrent_lo`, `recurrent`, `log_bin_width`).

    Returns
    -------
    errors : (n_spectra, n_ref) float32
            ppm error `(m_match - reference) / reference * 1e6`; NaN where nothing matched.
    """
    n_spectra = offsets.size - 1
    n_ref = reference_mz.size
    errors = np.full((n_spectra, n_ref), np.nan, dtype=np.float32)

    for s in range(n_spectra):
        start = offsets[s]
        end = offsets[s + 1]
        if end <= start:
            continue
        mz = flat_mz[start:end]
        intensity = flat_intensity[start:end]
        min_intensity = noise_threshold(
            mz, intensity, recurrent_lo, recurrent, log_bin_width, signal_to_noise
        )

        # imzML centroid spectra are normally stored in ascending m/z; sort only if they are not.
        ascending = True
        for k in range(1, mz.size):
            if mz[k] < mz[k - 1]:
                ascending = False
                break
        if not ascending:
            order = np.argsort(mz)
            mz = mz[order]
            intensity = intensity[order]

        for j in range(n_ref):
            reference = reference_mz[j]
            centre = reference * (1.0 + centre_ppm[s, j] * 1e-6)
            half_width = reference * window_ppm * 1e-6
            left = np.searchsorted(mz, centre - half_width)
            right = np.searchsorted(mz, centre + half_width, side="right")
            best = -1
            best_intensity = -1.0
            for k in range(left, right):
                if intensity[k] >= min_intensity and intensity[k] > best_intensity:
                    best = k
                    best_intensity = intensity[k]
            if best >= 0:
                errors[s, j] = (mz[best] - reference) / reference * 1e6

    return errors


@njit(cache=True)
def offset_histogram(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    reference_mz: np.ndarray,
    half_width_ppm: float,
    signal_to_noise: float,
    recurrent_lo: int,
    recurrent: np.ndarray,
    log_bin_width: float,
) -> np.ndarray:
    """
    Per reference, the number of signal peaks at every whole-ppm offset from it.

    Returns an (n_ref, 2 * half_width_ppm + 1) int64 array; column k counts the peaks reaching their
    spectrum's `noise_threshold` at `round((m - reference) / reference * 1e6) == k - half_width_ppm`.
    """
    half = int(half_width_ppm)
    counts = np.zeros((reference_mz.size, 2 * half + 1), dtype=np.int64)
    for s in range(offsets.size - 1):
        start = offsets[s]
        end = offsets[s + 1]
        if end <= start:
            continue
        mz = flat_mz[start:end]
        intensity = flat_intensity[start:end]
        threshold = noise_threshold(
            mz, intensity, recurrent_lo, recurrent, log_bin_width, signal_to_noise
        )
        ascending = True
        for k in range(1, mz.size):
            if mz[k] < mz[k - 1]:
                ascending = False
                break
        if not ascending:
            order = np.argsort(mz)
            mz = mz[order]
            intensity = intensity[order]
        for j in range(reference_mz.size):
            reference = reference_mz[j]
            left = np.searchsorted(mz, reference * (1.0 - half * 1e-6))
            right = np.searchsorted(mz, reference * (1.0 + half * 1e-6), side="right")
            for k in range(left, right):
                if intensity[k] >= threshold:
                    b = int(np.round((mz[k] - reference) / reference * 1e6)) + half
                    if 0 <= b <= 2 * half:
                        counts[j, b] += 1
    return counts


def at_least_probability(probabilities: np.ndarray, k: int) -> np.ndarray:
    """
    Per row of `probabilities` (independent events), the probability that at least `k` occur.

    The Poisson-binomial count is built one event at a time, keeping P(count = 0 .. k - 1).
    """
    probabilities = np.asarray(probabilities, dtype=np.float64)
    below = np.zeros((probabilities.shape[0], max(k, 1)), dtype=np.float64)
    below[:, 0] = 1.0
    for column in range(probabilities.shape[1]):
        p = probabilities[:, column : column + 1]
        below[:, 1:] = below[:, 1:] * (1.0 - p) + below[:, :-1] * p
        below[:, :1] *= 1.0 - p
    return (
        np.clip(1.0 - below.sum(axis=1), 0.0, 1.0)
        if k > 0
        else np.ones(probabilities.shape[0])
    )


def sample_offset_ppm(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    reference_mz: np.ndarray,
    half_width_ppm: float,
    mass_tolerance: float,
    recurrence: tuple[int, np.ndarray, float] = _NO_RECURRENCE,
) -> float:
    """
    The ppm offset of a sample's calibrants from the reference masses, searched within `half_width_ppm`.

    A signal peak reaches its spectrum's `noise_threshold` under the recurrence map `recurrence`
    (:func:`recurrence_map`). The calibrants of one spectrum drift together, so their offsets agree
    while other ions near each reference fall at unrelated offsets. Per reference, the signal peaks within `mass_tolerance` of
    every candidate offset are counted (:func:`offset_histogram`) and compared with the count its
    peaks would give if spread evenly over the search range: a reference supports the offset when
    that count is exceeded at Poisson tail probability below `_RECAL_OFFSET_ALPHA`. The offset is
    the one supported by the most references; ties go to the maximum of the summed histograms, each
    scaled to unit sum so every reference weighs alike and smoothed with a Gaussian kernel whose FWHM
    is `mass_tolerance`.

    Other ions near the references also support offsets, and with several references some of them
    line up by chance; a sample sharing their chemistry then lines up at the same wrong offset. So
    the best support is tested against chance agreement: reference j supports a random offset with
    probability q_j, its share of supported offsets, the references independently, so the support
    at one offset follows a Poisson-binomial law (:func:`at_least_probability`). Its tail at the best
    support, times the number of independent offsets in the search range (range / (2 *
    ceil(mass_tolerance) + 1)), must stay below `_RECAL_OFFSET_CHANCE`. Otherwise, or with fewer than
    `_RECAL_OFFSET_MIN_REFERENCES` supporting references, the result is 0.
    """
    counts = None
    for start, stop in _peak_blocks(mz_list):
        flat_mz, flat_intensity, offsets = _flatten_spectra(
            mz_list[start:stop], intensity_list[start:stop]
        )
        if flat_mz.size == 0:
            continue
        block = offset_histogram(
            flat_mz,
            flat_intensity,
            offsets,
            reference_mz,
            float(half_width_ppm),
            _SIGNAL_TO_NOISE,
            *recurrence,
        )
        counts = block if counts is None else counts + block
    if counts is None or counts.sum() == 0:
        return 0.0
    reach = max(1, int(np.ceil(mass_tolerance)))
    cumulative = np.pad(
        np.cumsum(counts, axis=1), ((0, 0), (reach + 1, reach)), mode="edge"
    )
    cumulative[:, : reach + 1] = 0
    near = cumulative[:, 2 * reach + 1 :] - cumulative[:, : -2 * reach - 1]
    totals = counts.sum(axis=1, keepdims=True)
    expected = totals * (2 * reach + 1) / counts.shape[1]
    critical = np.where(
        totals > 0,
        poisson_distribution.isf(_RECAL_OFFSET_ALPHA, np.maximum(expected, 1e-12)),
        np.inf,
    )
    supports = near > critical
    support = supports.sum(axis=0)
    if support.max() < _RECAL_OFFSET_MIN_REFERENCES:
        return 0.0
    chance = at_least_probability(supports.mean(axis=1)[None, :], int(support.max()))[0]
    if chance * counts.shape[1] / (2 * reach + 1) >= _RECAL_OFFSET_CHANCE:
        return 0.0
    density = gaussian_filter1d(
        (counts / np.maximum(totals, 1)).sum(axis=0),
        mass_tolerance / (2 * np.sqrt(2 * np.log(2))),
        mode="constant",
    )
    best = np.flatnonzero(support == support.max())
    return float(best[np.argmax(density[best])] - int(half_width_ppm))


def _match_all(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    reference_mz: np.ndarray,
    centre_ppm: Callable[[int, int], np.ndarray],
    window_ppm: float,
    recurrence: tuple[int, np.ndarray, float] = _NO_RECURRENCE,
) -> np.ndarray:
    """Blocked `match_references` over a whole sample; `centre_ppm(start, stop)` gives each block's centres."""
    n_spectra = len(mz_list)
    errors = np.full((n_spectra, reference_mz.size), np.nan, dtype=np.float32)
    for start, stop in _peak_blocks(mz_list):
        flat_mz, flat_intensity, offsets = _flatten_spectra(
            mz_list[start:stop], intensity_list[start:stop]
        )
        if flat_mz.size == 0:
            continue
        errors[start:stop] = match_references(
            flat_mz,
            flat_intensity,
            offsets,
            reference_mz,
            centre_ppm(start, stop),
            float(window_ppm),
            _SIGNAL_TO_NOISE,
            *recurrence,
        )
    return errors
