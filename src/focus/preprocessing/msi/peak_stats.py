"""Sampling grid, baseline and noise of a profile spectrum, per log-spaced m/z band (numba kernels)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit

_MAD_TO_SIGMA = 1.4826

# A band needs at least this many grid positions (stored points plus unstored ones) to be estimated.
_MIN_BAND_POINTS = 8


@dataclass(frozen=True)
class GridModel:
    """Grid step in Da per log-spaced m/z band: band b spans log m/z from log_lo + b / inv_step."""

    log_lo: float
    inv_step: float
    steps: np.ndarray


@njit(cache=True, nogil=True)
def band_of(mz: float, log_lo: float, inv_step: float, n_bands: int) -> int:
    """Index of the log-spaced band holding ``mz``, clipped to the band range."""
    band = int((np.log(max(mz, 1e-9)) - log_lo) * inv_step)
    return min(max(band, 0), n_bands - 1)


@njit(cache=True, nogil=True)
def links(
    mz: np.ndarray, log_lo: float, inv_step: float, steps: np.ndarray, gap: float
) -> np.ndarray:
    """linked[i]: points i and i + 1 are neighbours on the sampling grid (closer than ``gap`` steps)."""
    n = mz.size
    linked = np.empty(max(n - 1, 0), dtype=np.bool_)
    for i in range(n - 1):
        step = steps[band_of(mz[i], log_lo, inv_step, steps.size)]
        linked[i] = mz[i + 1] - mz[i] <= gap * step
    return linked


@njit(cache=True, nogil=True)
def _kth(values: np.ndarray, inserted: int, count: int, fill: float, k: int) -> float:
    """Element k of the sorted ``values`` with ``count`` copies of ``fill`` inserted in order."""
    if k < inserted:
        return float(values[k])
    if k < inserted + count:
        return fill
    return float(values[k - count])


@njit(cache=True, nogil=True)
def _filled_median(values: np.ndarray, count: int, fill: float) -> float:
    """Median of the sorted ``values`` together with ``count`` copies of ``fill``."""
    total = values.size + count
    if total == 0:
        return 0.0
    inserted = int(np.searchsorted(values, fill))
    upper = _kth(values, inserted, count, fill, total // 2)
    if total % 2 == 1:
        return upper
    return 0.5 * (_kth(values, inserted, count, fill, total // 2 - 1) + upper)


@njit(cache=True, nogil=True)
def _band_statistics(
    y: np.ndarray, start: int, stop: int, zeros: int
) -> tuple[float, float]:
    """Median and 1.4826 x MAD of ``y[start:stop]`` together with ``zeros`` unstored zero points."""
    values = np.sort(y[start:stop])
    median = _filled_median(values, zeros, 0.0)
    deviation = np.sort(np.abs(values - median))
    return median, _MAD_TO_SIGMA * _filled_median(deviation, zeros, median)


@njit(cache=True, nogil=True)
def _fill_bands(base: np.ndarray, noise: np.ndarray) -> None:
    """Give every band without an estimate the values of its nearest estimated band."""
    valid = np.flatnonzero(~np.isnan(base))
    if valid.size == 0:
        base[:] = 0.0
        noise[:] = 0.0
        return
    for band in range(base.size):
        if np.isnan(base[band]):
            nearest = valid[0]
            for candidate in valid:
                if abs(candidate - band) < abs(nearest - band):
                    nearest = candidate
            base[band] = base[nearest]
            noise[band] = noise[nearest]


@njit(cache=True, nogil=True)
def baseline_noise(
    mz: np.ndarray, y: np.ndarray, log_lo: float, inv_step: float, steps: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Baseline (median) and noise (1.4826 x MAD) of the signal per band.

    The statistics run over the sampling grid: the grid positions of a band that hold no stored
    point count as zeros, so a sparse or ion-counting profile gets baseline 0 and noise 0 and a dense
    one gets a real estimate. Bands without enough points take their nearest estimated band.
    """
    n_bands, n = steps.size, mz.size
    base = np.full(n_bands, np.nan)
    noise = np.full(n_bands, np.nan)
    start = 0
    while start < n:
        band = band_of(mz[start], log_lo, inv_step, n_bands)
        stop = start
        while stop < n and band_of(mz[stop], log_lo, inv_step, n_bands) == band:
            stop += 1
        if start > 0 and stop < n:
            span = np.exp(log_lo + (band + 1) / inv_step) - np.exp(
                log_lo + band / inv_step
            )
        else:
            span = mz[stop - 1] - mz[start] + steps[band]
        zeros = max(0, int(span / steps[band] + 0.5) - (stop - start))
        if stop - start + zeros >= _MIN_BAND_POINTS:
            base[band], noise[band] = _band_statistics(y, start, stop, zeros)
        start = stop
    _fill_bands(base, noise)
    return base, noise


@njit(cache=True, nogil=True)
def interpolate(values: np.ndarray, mz: float, log_lo: float, inv_step: float) -> float:
    """Linear interpolation of a per-band value between the band centres in log m/z."""
    position = (np.log(max(mz, 1e-9)) - log_lo) * inv_step - 0.5
    low = int(np.floor(position))
    if low < 0:
        return float(values[0])
    if low >= values.size - 1:
        return float(values[values.size - 1])
    fraction = position - low
    return float(values[low] * (1.0 - fraction) + values[low + 1] * fraction)
