"""Align sample mass scales and merge the samples' m/z features into the shared backbone."""

from dataclasses import dataclass

import numpy as np

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.settings import _SCALE_MIN_SHARED
from focus.preprocessing.msi.technology import (
    SIGMA_MAX_TOLERANCES,
    SIGMA_MIN_TOLERANCES,
)


def mass_scale_shifts(
    references: list[np.ndarray],
    window_ppm: float,
    fixed: np.ndarray | None = None,
    fixed_shifts: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Per sample, the constant ppm offset of its references from the samples' common mass scale.

    Each sample's references sit at its own mean calibrant masses, so they carry its calibration
    offset. For every pair of samples sharing at least `_SCALE_MIN_SHARED` references, the median ppm
    difference of the shared ones estimates the difference of the two offsets. The offsets are the
    least-squares solution of these pairwise differences, weighted by the number of shared references,
    per group of samples connected by such pairs: offset 0 on the group's `fixed` samples when it holds
    any (references already on the common scale), zero mean otherwise. A sample that shares references
    with no other sample keeps offset 0.

    Returns
    -------
    shifts : (n,) float64
            ppm offset of each sample; its references divided by (1 + shift * 1e-6) are on the common scale.
    connected : (n,) bool
            False for a sample that shares references with no other sample.
    """
    n = len(references)
    fixed = np.zeros(n, dtype=bool) if fixed is None else np.asarray(fixed, dtype=bool)
    known = (
        np.zeros(n, dtype=np.float64)
        if fixed_shifts is None
        else np.where(fixed, np.asarray(fixed_shifts, dtype=np.float64), 0.0)
    )
    references = [np.asarray(r, dtype=np.float64) for r in references]
    pairs = []  # (s, t, ppm of s relative to t, shared references)
    parent = list(range(n))

    def root(k: int) -> int:
        """Find the representative of a mass-scale connectivity group."""
        while parent[k] != k:
            k = parent[k]
        return k

    for s in range(n):
        for t in range(s + 1, n):
            a, b = references[s], references[t]
            if a.size == 0 or b.size == 0:
                continue
            nearest = b[np.abs(a[:, None] - b[None, :]).argmin(axis=1)]
            ppm = (a - nearest) / nearest * 1e6
            shared = np.abs(ppm) <= window_ppm
            if shared.sum() >= _SCALE_MIN_SHARED:
                pairs.append((s, t, float(np.median(ppm[shared])), int(shared.sum())))
                parent[root(s)] = root(t)

    shifts = known.copy()
    group = np.array([root(k) for k in range(n)])
    connected = np.array([np.sum(group == group[k]) > 1 for k in range(n)])
    for label in np.unique(group[connected]):
        members = np.flatnonzero(group == label)
        free = members[~fixed[members]]
        if free.size == 0:
            continue
        column = {int(k): j for j, k in enumerate(free)}
        rows, rhs = [], []
        for s, t, ppm, count in pairs:
            if group[s] != label:
                continue
            row = np.zeros(free.size)
            if s in column:
                row[column[s]] = 1.0
            if t in column:
                row[column[t]] = -1.0
            # c_s - c_t = ppm, with the shifts of fixed samples moved to the right-hand side.
            rows.append(np.sqrt(count) * row)
            rhs.append(
                np.sqrt(count) * (ppm - known[s] * fixed[s] + known[t] * fixed[t])
            )
        if not fixed[members].any():
            rows.append(np.ones(free.size))
            rhs.append(0.0)
        shifts[free] = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return shifts, connected


@dataclass
class SampleFeatures:
    """The kept m/z features of one sample and ion mode, in ascending m/z."""

    mz: np.ndarray  # float64
    presence: np.ndarray  # share of the sample's foreground spectra holding the feature
    sigma: np.ndarray  # robust peak spread, ppm (features.feature_widths)
    annotated: np.ndarray  # bool, matched by the lipid database


@dataclass
class Backbone:
    """The shared m/z features of one ion mode, in ascending m/z."""

    mz: np.ndarray  # float64
    sigma: np.ndarray  # Gaussian width of each feature, ppm
    annotated: np.ndarray  # bool, some sample feature of the group was annotated

    @classmethod
    def empty(cls) -> "Backbone":
        """A backbone without features."""
        return cls(
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            np.array([], dtype=bool),
        )


def split_groups(mz: np.ndarray, tolerance_ppm: float) -> list[tuple[int, int]]:
    """
    [start, stop) groups of ascending `mz` that each span at most `tolerance_ppm`.

    A group spanning more is split at its largest gap between consecutive values, recursively, until
    every group fits (MALDIquant `binPeaks`, with a span instead of a deviation from the mean). Every
    value lands in exactly one group.
    """
    groups: list[tuple[int, int]] = []
    stack = [(0, mz.size)] if mz.size else []
    while stack:
        start, stop = stack.pop()
        if (
            stop - start == 1
            or (mz[stop - 1] - mz[start]) / mz[start] * 1e6 <= tolerance_ppm
        ):
            groups.append((start, stop))
            continue
        split = start + 1 + int(np.argmax(np.diff(mz[start:stop])))
        stack.extend([(split, stop), (start, split)])
    return sorted(groups)


def merge_features(samples: list[SampleFeatures], mass_tolerance: float) -> Backbone:
    """
    Union of the samples' features with the features closer than the tolerance merged.

    The features are grouped by :func:`split_groups` at `mass_tolerance`. A group's m/z is the
    presence-weighted mean of its members, so every sample weighs alike whatever its number of
    spectra; its width is the presence-weighted root mean square of the members' widths and of their
    offsets from that mean, clipped to [SIGMA_MIN_TOLERANCES, SIGMA_MAX_TOLERANCES] tolerances; it is
    annotated when any member was.
    """
    if not samples:
        return Backbone.empty()
    mz = np.concatenate([s.mz for s in samples]).astype(np.float64)
    if mz.size == 0:
        return Backbone.empty()
    order = np.argsort(mz, kind="stable")
    mz = mz[order]
    weight = np.maximum(np.concatenate([s.presence for s in samples]), 1e-12)[order]
    sigma = np.concatenate([s.sigma for s in samples]).astype(np.float64)[order]
    annotated = np.concatenate([s.annotated for s in samples]).astype(bool)[order]
    groups = split_groups(mz, mass_tolerance)
    out_mz = np.empty(len(groups), dtype=np.float64)
    out_sigma = np.empty(len(groups), dtype=np.float64)
    out_annotated = np.empty(len(groups), dtype=bool)
    for g, (start, stop) in enumerate(groups):
        w = weight[start:stop]
        centre = float(np.sum(w * mz[start:stop]) / w.sum())
        offset = (mz[start:stop] - centre) / centre * 1e6
        out_mz[g] = centre
        out_sigma[g] = np.sqrt(
            np.sum(w * (sigma[start:stop] ** 2 + offset**2)) / w.sum()
        )
        out_annotated[g] = annotated[start:stop].any()
    out_sigma = np.clip(
        out_sigma,
        SIGMA_MIN_TOLERANCES * mass_tolerance,
        SIGMA_MAX_TOLERANCES * mass_tolerance,
    )
    return Backbone(out_mz, out_sigma, out_annotated)


class _BackboneBuilder:
    """
    m/z backbone of a dataset: the union of its samples' features, merged within the tolerance.

    Usage: construct with the ppm tolerance, hand over each sample's kept features per ion mode with
    :meth:`add`, then call :meth:`build`. Only the features' m/z, presence, width and annotation flag
    are retained, never a payload.
    """

    def __init__(self, mass_tolerance: float) -> None:
        """Set the ppm tolerance of the merge."""
        self._mass_tolerance = mass_tolerance
        self._samples: dict[str, list[SampleFeatures]] = {}

    def add(self, mode: str, features: SampleFeatures) -> None:
        """Retain one sample's kept features of one ion mode."""
        if features.mz.size:
            self._samples.setdefault(mode, []).append(features)

    def build(self) -> dict[str, Backbone]:
        """The backbone of every ion mode; empty for a mode without features."""
        return {
            mode: merge_features(self._samples.get(mode, []), self._mass_tolerance)
            for mode in MsiIonMode.list()
        }
