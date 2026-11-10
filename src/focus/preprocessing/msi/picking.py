"""Peak picking of one profile spectrum: apexes above the baseline noise, merged and fitted (numba)."""

from __future__ import annotations

import numpy as np
from numba import njit

from focus.preprocessing.msi.peak_stats import (
    GridModel,
    baseline_noise,
    interpolate,
    links,
)
from focus.preprocessing.msi.settings import (
    _PICK_GAP,
    _PICK_SIGNAL_TO_NOISE,
    _PICK_VALLEY,
)


@njit(cache=True, nogil=True)
def _vertex(
    x0: float, x1: float, x2: float, v0: float, v1: float, v2: float
) -> tuple[float, float, bool]:
    """Vertex of the parabola through three points (any spacing); ok only for a maximum inside."""
    d01, d12 = x1 - x0, x2 - x1
    if d01 <= 0.0 or d12 <= 0.0:
        return x1, v1, False
    s01, s12 = (v1 - v0) / d01, (v2 - v1) / d12
    a = (s12 - s01) / (x2 - x0)
    if not a < 0.0:
        return x1, v1, False
    x = 0.5 * (x0 + x1) - s01 / (2.0 * a)
    if not (x > x0 and x < x2):
        return x1, v1, False
    return x, v0 + s01 * (x - x0) + a * (x - x0) * (x - x1), True


@njit(cache=True, nogil=True)
def _apex(
    mz: np.ndarray, y: np.ndarray, linked: np.ndarray, i: int, base: float
) -> tuple[float, float]:
    """Position and height above the baseline of the apex ``i``.

    A Gaussian through the apex and its two neighbours (a parabola on the log of the signal above
    the baseline); a parabola on the signal when a neighbour is not above the baseline; the apex
    sample itself when a neighbour is not on the grid. The height is at least the apex sample's.
    """
    own = y[i] - base
    if i == 0 or i == mz.size - 1 or not (linked[i - 1] and linked[i]):
        return mz[i], own
    v0, v2 = y[i - 1] - base, y[i + 1] - base
    if v0 > 0.0 and v2 > 0.0:
        x, value, ok = _vertex(
            mz[i - 1], mz[i], mz[i + 1], np.log(v0), np.log(own), np.log(v2)
        )
        if ok:
            return x, max(np.exp(value), own)
    x, value, ok = _vertex(mz[i - 1], mz[i], mz[i + 1], v0, own, v2)
    if ok:
        return x, max(value, own)
    return mz[i], own


@njit(cache=True, nogil=True)
def _is_apex(y: np.ndarray, linked: np.ndarray, i: int) -> bool:
    """A local maximum of its run; a plateau gives its lowest-m/z point."""
    left_ok = i > 0 and linked[i - 1]
    right_ok = i < y.size - 1 and linked[i]
    return not (left_ok and not y[i] > y[i - 1]) and not (
        right_ok and not y[i] >= y[i + 1]
    )


@njit(cache=True, nogil=True)
def _gate(
    base: np.ndarray,
    noise: np.ndarray,
    mz: float,
    y: float,
    log_lo: float,
    inv_step: float,
    sigma: float,
) -> float:
    """Baseline at ``mz`` when ``y`` stands ``sigma`` noise deviations above it, NaN otherwise."""
    b = interpolate(base, mz, log_lo, inv_step)
    height = y - b
    if height > 0.0 and height >= sigma * interpolate(noise, mz, log_lo, inv_step):
        return b
    return np.nan


@njit(cache=True, nogil=True)
def significant_apexes(
    mz: np.ndarray,
    y: np.ndarray,
    log_lo: float,
    inv_step: float,
    steps: np.ndarray,
    gap: float,
    sigma: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Interior apexes with both neighbours on the grid standing ``sigma`` noise deviations above
    the baseline, and the baseline at each: the peaks whose width says how well the profile is
    sampled (a maximum of noise is one or two points wide whatever the sampling)."""
    linked = links(mz, log_lo, inv_step, steps, gap)
    base, noise = baseline_noise(mz, y, log_lo, inv_step, steps)
    found = np.empty(mz.size, dtype=np.int64)
    baselines = np.empty(mz.size, dtype=np.float64)
    count = 0
    for i in range(1, mz.size - 1):
        if not (linked[i - 1] and linked[i] and y[i] > y[i - 1] and y[i] >= y[i + 1]):
            continue
        b = interpolate(base, mz[i], log_lo, inv_step)
        height = y[i] - b
        if height > 0.0 and height >= sigma * interpolate(
            noise, mz[i], log_lo, inv_step
        ):
            found[count], baselines[count] = i, b
            count += 1
    return found[:count].copy(), baselines[:count].copy()


@njit(cache=True, nogil=True)
def _emit(
    mz: np.ndarray,
    y: np.ndarray,
    linked: np.ndarray,
    keep: int,
    base: float,
    out_mz: np.ndarray,
    out_h: np.ndarray,
    count: int,
) -> int:
    """Append the fitted apex ``keep`` to the output and return the new peak count."""
    out_mz[count], out_h[count] = _apex(mz, y, linked, keep, base)
    return count + 1


@njit(cache=True, nogil=True)
def pick_peaks(
    mz: np.ndarray,
    y: np.ndarray,
    log_lo: float,
    inv_step: float,
    steps: np.ndarray,
    signal_to_noise: float,
    gap: float,
    valley: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Centroids of one profile spectrum: m/z ascending and apex heights above the baseline.

    ``mz`` is ascending. Points closer than ``gap`` grid steps are neighbours; a run ends at a larger
    spacing. An apex is a peak when it stands ``signal_to_noise`` noise deviations above the baseline.
    Two apexes of a run are one peak unless the signal between them falls below ``valley`` of the
    lower one above the baseline; the higher one is kept.
    """
    n = mz.size
    out_mz = np.empty(n // 2 + 1, dtype=np.float64)
    out_h = np.empty(n // 2 + 1, dtype=np.float32)
    linked = links(mz, log_lo, inv_step, steps, gap)
    base, noise = baseline_noise(mz, y, log_lo, inv_step, steps)
    count, keep, keep_base, run_min = 0, -1, 0.0, 0.0
    for i in range(n):
        if keep >= 0 and (i == 0 or not linked[i - 1]):
            count = _emit(mz, y, linked, keep, keep_base, out_mz, out_h, count)
            keep = -1
        if keep >= 0 and y[i] < run_min:
            run_min = y[i]
        if not _is_apex(y, linked, i):
            continue
        b = _gate(base, noise, mz[i], y[i], log_lo, inv_step, signal_to_noise)
        if np.isnan(b):
            continue
        if keep >= 0 and run_min - keep_base > valley * (
            min(y[keep], y[i]) - keep_base
        ):
            if y[i] > y[keep]:
                keep, keep_base, run_min = i, b, y[i]
            continue
        if keep >= 0:
            count = _emit(mz, y, linked, keep, keep_base, out_mz, out_h, count)
        keep, keep_base, run_min = i, b, y[i]
    if keep >= 0:
        count = _emit(mz, y, linked, keep, keep_base, out_mz, out_h, count)
    return out_mz[:count].copy(), out_h[:count].copy()


def centroid_spectrum(
    mz: np.ndarray, intensity: np.ndarray, grid: GridModel
) -> tuple[np.ndarray, np.ndarray]:
    """Peak-pick one profile spectrum (any m/z order, finite values) with the grid model."""
    mz = np.array(mz, dtype=np.float64)
    y = np.array(intensity, dtype=np.float64)
    if mz.size > 1 and (np.diff(mz) < 0).any():
        order = np.argsort(mz, kind="stable")
        mz, y = mz[order], y[order]
    return pick_peaks(
        mz,
        y,
        grid.log_lo,
        grid.inv_step,
        grid.steps,
        _PICK_SIGNAL_TO_NOISE,
        _PICK_GAP,
        _PICK_VALLEY,
    )
