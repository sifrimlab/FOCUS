"""Align sample mass scales and build the shared density-based m/z feature backbone."""

import numpy as np

from scipy.ndimage import gaussian_filter1d

from focus.config.options import MsiIonMode
from focus.preprocessing.msi.settings import (
    _FEATURE_BINS_PER_TOLERANCE,
    _FEATURE_KERNEL_FWHM,
    _SCALE_MIN_SHARED,
)
from focus.preprocessing.msi.features import (
    feature_bin_width,
    find_features,
    peak_histogram,
)
from focus.preprocessing.msi.density import density_feature_ranges


def mass_scale_shifts(
    references: list[np.ndarray], window_ppm: float, fixed: np.ndarray | None = None
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

    shifts = np.zeros(n, dtype=np.float64)
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
            rows.append(np.sqrt(count) * row)
            rhs.append(np.sqrt(count) * ppm)
        if not fixed[members].any():
            rows.append(np.ones(free.size))
            rhs.append(0.0)
        shifts[free] = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)[0]
    return shifts, connected


class _BackboneBuilder:
    """
    m/z backbone of a dataset: the ions found in its samples, grouped across samples.

    Usage: construct with the ppm tolerance and the frequency threshold, feed one sample's
    (recalibrated) payload at a time with :meth:`add_sample`, then call :meth:`build`.

    Per sample (:meth:`add_sample`), every peak is counted on the feature grid (:func:`peak_histogram`)
    and the sample's features are found (:func:`find_features`): the maxima of its peak density that
    rise above the local background, so noise makes no feature and every recurring ion does, whatever
    its intensity. A feature is kept when its presence is at least `frequency_threshold` times that of
    the sample's most present feature. Only the kept features' m/z and presence are retained.

    :meth:`build` groups the samples' features: their presences are summed on the feature grid (every
    sample weighs alike, a presence being a share of that sample's spectra) and smoothed with the
    feature kernel. Each maximum of this density is one backbone feature; it collects the sample
    features up to the lowest point between it and each neighbouring maximum, and at most one tolerance
    from it. Its m/z is the presence-weighted mean m/z of those sample features.
    """

    def __init__(self, mass_tolerance: int, frequency_threshold: float) -> None:
        """Set the ppm feature grid and sample-frequency threshold before adding payloads.

        Parameters
        ----------
        mass_tolerance : int
            Grouping/matching tolerance in ppm, used to compute the logarithmic bin width.
        frequency_threshold : float
            Minimum presence relative to a sample's most frequent feature."""
        self._mass_tolerance = mass_tolerance
        self._frequency_threshold = frequency_threshold
        self._log_bin_width = feature_bin_width(mass_tolerance)
        # Per ion mode, one (m/z, presence) entry per contributing sample.
        self._samples: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {}

    def add_sample(
        self, sample_mz: dict[str, list[np.ndarray]]
    ) -> dict[str, tuple[int, int]]:
        """
        Find one sample's features and keep the frequent ones; the payload is only read, never retained.
        Returns, per ion mode, the number of features found and kept.
        """
        summary = {}
        for mode, spectra in sample_mz.items():
            bins, counts, mz_sums = peak_histogram(spectra, self._log_bin_width)
            features = find_features(
                bins, counts, mz_sums, len(spectra), self._mass_tolerance
            )
            kept = (
                features.presence >= self._frequency_threshold * features.presence.max()
                if features.mz.size
                else np.zeros(0, dtype=bool)
            )
            summary[mode] = (int(features.mz.size), int(kept.sum()))
            if kept.any():
                self._samples.setdefault(mode, []).append(
                    (features.mz[kept], features.presence[kept])
                )
        return summary

    def build(self) -> dict[str, np.ndarray]:
        """Ascending feature m/z per ion mode; an empty array for a mode without features."""
        backbone = {}
        for mode in MsiIonMode.list():
            samples = self._samples.get(mode, [])
            backbone[mode] = (
                self._group(samples) if samples else np.array([], dtype=np.float64)
            )
        return backbone

    def _group(self, samples: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
        """Return the feature group matching the requested mass and its tolerance."""
        mz = np.concatenate([m for m, _ in samples])
        weight = np.maximum(np.concatenate([w for _, w in samples]), 1e-12)
        if mz.size == 0:
            return np.array([], dtype=np.float64)
        position = np.floor(np.log(mz) / self._log_bin_width).astype(np.int64)
        position -= position.min()
        reach = _FEATURE_BINS_PER_TOLERANCE
        density = gaussian_filter1d(
            np.bincount(position, weights=weight).astype(np.float64),
            _FEATURE_KERNEL_FWHM * reach / (2 * np.sqrt(2 * np.log(2))),
            mode="constant",
            cval=0.0,
            truncate=4.0,
        )
        padded = np.concatenate(([0.0], density, [0.0]))
        maxima = np.flatnonzero(
            (padded[1:-1] > padded[:-2]) & (padded[1:-1] >= padded[2:]) & (density > 0)
        )
        left, right = density_feature_ranges(density, maxima, reach)
        feature = np.searchsorted(left, position, side="right") - 1
        inside = (feature >= 0) & (position <= right[np.maximum(feature, 0)])
        total = np.bincount(
            feature[inside], weights=weight[inside], minlength=maxima.size
        )
        total_mz = np.bincount(
            feature[inside], weights=(weight * mz)[inside], minlength=maxima.size
        )
        return np.sort(total_mz[total > 0] / total[total > 0])
