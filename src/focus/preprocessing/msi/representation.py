"""Decide from the stored spectra whether an IBD holds a sampled profile or a peak list."""

from __future__ import annotations

import mmap
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from focus.preprocessing.msi.binary import _read
from focus.preprocessing.msi.peak_stats import GridModel
from focus.preprocessing.msi.picking import significant_apexes
from focus.preprocessing.msi.settings import (
    _PICK_GAP,
    _PROFILE_APEX_SIGMA,
    _PROFILE_BANDS,
    _PROFILE_GRID_TOLERANCE,
    _PROFILE_MAX_STEP,
    _PROFILE_MIN_POINTS,
    _PROFILE_MIN_POINTS_PER_PEAK,
    _PROFILE_PROBE_SPECTRA,
    _PROFILE_REGULARITY,
    _PROFILE_SHARE,
    _PROFILE_TOP_APEXES,
)
from focus.runtime.reporter_context import get_reporter

PROFILE = "profile"
UNRESOLVED = "unresolved"
PEAKS = "peaks"
MIXED = "mixed"

# Spacings kept per probed spectrum for the grid step: a deterministic stride bounds the memory of
# spectra with hundreds of thousands of points.
_SPACINGS_PER_SPECTRUM = 2000


@dataclass(frozen=True)
class Representation:
    """What the probed spectra hold.

    ``points_per_peak`` is the median full width at half maximum of the largest apexes, in grid
    steps, and ``step`` the median grid step at them in Da; both are NaN unless the spectra lie on a
    regular grid. ``grid`` is the grid step model the picker uses, None without informative spectra.
    """

    kind: str
    profile_share: float
    points_per_peak: float
    step: float
    grid: GridModel | None
    spectra: int


def _iter_probe(
    path: str,
    mz_dtype: type[np.generic],
    int_dtype: type[np.generic],
    mz_meta: np.ndarray,
    int_meta: np.ndarray,
    indices: np.ndarray,
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield the finite, ascending (m/z, intensity) float64 copies of the probed spectra."""
    with open(path, "rb") as stream:
        mm = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for i in indices:
                mz = np.array(_read(mm, mz_dtype, mz_meta[i]), dtype=np.float64)
                y = np.array(_read(mm, int_dtype, int_meta[i]), dtype=np.float64)
                if not (np.isfinite(mz).all() and np.isfinite(y).all()):
                    continue
                if (np.diff(mz) < 0).any():
                    order = np.argsort(mz, kind="stable")
                    mz, y = mz[order], y[order]
                yield mz, y
        finally:
            mm.close()


def _probe_indices(mz_meta: np.ndarray, int_meta: np.ndarray) -> np.ndarray:
    """Evenly spaced spectra, those with too few points or unequal array lengths left out."""
    counts = mz_meta[:, 0]
    n = counts.size
    picks = np.unique(
        np.linspace(0, n - 1, min(n, _PROFILE_PROBE_SPECTRA)).round().astype(np.int64)
    )
    keep = (counts[picks] >= _PROFILE_MIN_POINTS) & (
        int_meta[picks, 0] == counts[picks]
    )
    return np.asarray(picks[keep], dtype=np.int64)


def _grid_model(
    path: str,
    mz_dtype: type[np.generic],
    int_dtype: type[np.generic],
    mz_meta: np.ndarray,
    int_meta: np.ndarray,
    indices: np.ndarray,
) -> GridModel | None:
    """Median spacing per log-spaced m/z band over the probed spectra, None without any spacing."""
    mids: list[np.ndarray] = []
    gaps: list[np.ndarray] = []
    lo, hi = np.inf, 0.0
    for mz, _ in _iter_probe(path, mz_dtype, int_dtype, mz_meta, int_meta, indices):
        positive = mz[mz > 0]
        if positive.size < 2:
            continue
        lo, hi = min(lo, float(positive[0])), max(hi, float(positive[-1]))
        d = np.diff(positive)
        stride = max(1, d.size // _SPACINGS_PER_SPECTRUM)
        mids.append(positive[:-1][::stride])
        gaps.append(d[::stride])
    if not mids or not hi > lo:
        return None
    mid, gap = np.concatenate(mids), np.concatenate(gaps)
    log_lo = float(np.log(lo))
    inv_step = _PROFILE_BANDS / max(float(np.log(hi)) - log_lo, 1e-9)
    band = np.clip(
        ((np.log(mid) - log_lo) * inv_step).astype(np.int64), 0, _PROFILE_BANDS - 1
    )
    steps = np.full(_PROFILE_BANDS, np.nan)
    for b in np.unique(band):
        values = gap[(band == b) & (gap > 0)]
        if values.size:
            steps[b] = np.median(values)
    valid = np.isfinite(steps)
    if not valid.any():
        return None
    index = np.arange(_PROFILE_BANDS)
    steps = np.interp(index, index[valid], steps[valid])
    return GridModel(log_lo, inv_step, steps)


def _half_width_steps(
    mz: np.ndarray,
    y: np.ndarray,
    linked: np.ndarray,
    i: int,
    step: float,
    baseline: float,
) -> float:
    """Full width at half maximum of the apex ``i`` above its baseline, in grid steps (the
    half-height crossings are interpolated linearly)."""
    half = baseline + 0.5 * (y[i] - baseline)
    j = i
    while j > 0 and linked[j - 1] and y[j - 1] > half:
        j -= 1
    if j > 0 and linked[j - 1]:
        left = mz[j - 1] + (half - y[j - 1]) / (y[j] - y[j - 1]) * (mz[j] - mz[j - 1])
    else:
        left = mz[j]
    k = i
    while k < mz.size - 1 and linked[k] and y[k + 1] > half:
        k += 1
    if k < mz.size - 1 and linked[k]:
        right = mz[k] + (y[k] - half) / (y[k] - y[k + 1]) * (mz[k + 1] - mz[k])
    else:
        right = mz[k]
    return float((right - left) / step)


def _measure_spectrum(
    mz: np.ndarray, y: np.ndarray, grid: GridModel
) -> tuple[list[float], list[float]] | None:
    """Half-maximum widths (grid steps) and grid steps (Da) of the largest apexes of a spectrum on
    the grid, an empty pair when it is on the grid but has no such apex, None when it is not a
    sampled profile (``_PROFILE_REGULARITY`` of its spacings lie within ``_PROFILE_GRID_TOLERANCE``
    of the grid step of their band)."""
    log_mz = np.log(np.maximum(mz[:-1], np.exp(grid.log_lo)))
    band = np.clip(
        ((log_mz - grid.log_lo) * grid.inv_step).astype(np.int64),
        0,
        _PROFILE_BANDS - 1,
    )
    h = grid.steps[band]
    d = np.diff(mz)
    if np.mean(np.abs(d - h) <= _PROFILE_GRID_TOLERANCE * h) < _PROFILE_REGULARITY:
        return None
    linked = d <= _PICK_GAP * h
    apex, baseline = significant_apexes(
        mz, y, grid.log_lo, grid.inv_step, grid.steps, _PICK_GAP, _PROFILE_APEX_SIGMA
    )
    if apex.size > _PROFILE_TOP_APEXES:
        top = np.argsort(-y[apex], kind="stable")[:_PROFILE_TOP_APEXES]
        apex, baseline = apex[top], baseline[top]
    steps = [float(h[i]) for i in apex]
    widths = [
        _half_width_steps(mz, y, linked, int(i), step, float(b))
        for i, b, step in zip(apex, baseline, steps)
    ]
    return widths, steps


def _classify(
    profiles: int, total: int, widths: list[float], steps: list[float], grid: GridModel
) -> Representation:
    """The representation of a probe in which ``profiles`` of ``total`` spectra are sampled profiles."""
    nan = float("nan")
    share = profiles / total if total else 0.0
    if share < _PROFILE_SHARE[0]:
        return Representation(PEAKS, share, nan, nan, grid, total)
    points = float(np.median(widths)) if widths else 0.0
    step = float(np.median(steps)) if steps else nan
    if share <= _PROFILE_SHARE[1]:
        return Representation(MIXED, share, points, step, grid, total)
    resolved = points >= _PROFILE_MIN_POINTS_PER_PEAK and step <= _PROFILE_MAX_STEP
    kind = PROFILE if resolved else UNRESOLVED
    return Representation(kind, share, points, step, grid, total)


def probe_representation(
    path: str,
    mz_dtype: type[np.generic],
    int_dtype: type[np.generic],
    mz_meta: np.ndarray,
    int_meta: np.ndarray,
) -> Representation:
    """Classify the spectra of one IBD from evenly spaced probe spectra (deterministic).

    A list of peaks has irregular m/z spacings, a sampled profile a regular grid. The sampling of a
    profile is judged from its largest apexes: the median full width at half maximum in grid steps
    and the grid step in Da.
    """
    indices = _probe_indices(mz_meta, int_meta)
    grid = (
        _grid_model(path, mz_dtype, int_dtype, mz_meta, int_meta, indices)
        if indices.size
        else None
    )
    if grid is None:
        return Representation(PEAKS, 0.0, float("nan"), float("nan"), None, 0)
    widths: list[float] = []
    steps: list[float] = []
    profiles = total = 0
    for mz, y in _iter_probe(path, mz_dtype, int_dtype, mz_meta, int_meta, indices):
        if mz.size < _PROFILE_MIN_POINTS or not mz[-1] > mz[0]:
            continue
        total += 1
        measured = _measure_spectrum(mz, y, grid)
        if measured is not None:
            profiles += 1
            widths += measured[0]
            steps += measured[1]
    return _classify(profiles, total, widths, steps, grid)


def needs_centroiding(rep: Representation, declared: str, filename: str) -> bool:
    """Apply the content-decides rule: True when the spectra are a resolved profile to be picked.

    Raises ValueError for a file that mixes both representations and for a declared profile too
    coarsely sampled to pick; warns where the header contradicts the content.
    """
    reporter = get_reporter()
    if rep.kind == MIXED:
        raise ValueError(
            f"{filename}: {rep.profile_share:.0%} of the probed spectra are sampled profiles and the "
            "rest are peak lists; files that mix both representations are not supported"
        )
    if rep.kind == PROFILE:
        if declared == "centroid":
            reporter.warning(
                f"{filename}: the header declares centroid spectra but the stored spectra are a "
                f"sampled profile ({rep.points_per_peak:.1f} points per peak); they are centroided"
            )
        return True
    if rep.kind == UNRESOLVED and declared == "profile":
        reason = (
            f"{rep.points_per_peak:.1f} points per peak (at least "
            f"{_PROFILE_MIN_POINTS_PER_PEAK:g} needed)"
            if rep.points_per_peak < _PROFILE_MIN_POINTS_PER_PEAK
            else f"a grid step of {rep.step:.3f} Da (at most {_PROFILE_MAX_STEP:g} Da allowed)"
        )
        raise ValueError(
            f"{filename}: the declared profile spectra have {reason}, too coarse to pick "
            "peaks reliably; export centroided spectra, or the profile at its native sampling"
        )
    if rep.kind == PEAKS and declared == "profile" and rep.spectra:
        reporter.warning(
            f"{filename}: the header declares profile spectra but the stored m/z values are not on a "
            "regular grid; they are read as centroids"
        )
    return False
