"""Merge adjacent analytical clusters according to their density valleys."""

from focus.spatial.types import MergeInfo
from focus.spatial.matrices import sparse_csr

import numpy as np
import scipy.sparse as sp

from focus.spatial.units import SpatialUnits
from focus.spatial.settings import _MERGE_VALLEY_RATIO, _VALLEY_BINS, _VALLEY_TAIL
from focus.spatial.smoothing import smoothing_operator


def label_coherence(
    labels: np.ndarray, units: SpatialUnits, keep: np.ndarray | None = None
) -> float | None:
    """Agreement of labels with their 1-ring neighbours, chance-corrected (0 = random, 1 = perfect).

    Descriptive provenance for the clustering, computed on the kept units; ``None`` when undefined
    (fewer than two units or a single label).
    """
    neighbours = smoothing_operator(units, 1, keep).tocoo()
    off = neighbours.row != neighbours.col
    if not off.any():
        return None
    observed = float(
        np.mean(labels[neighbours.row[off]] == labels[neighbours.col[off]])
    )
    _, freq = np.unique(labels, return_counts=True)
    chance = float(np.sum((freq / freq.sum()) ** 2))
    return None if chance >= 1 else (observed - chance) / (1 - chance)


def _valley_ratio(projection: np.ndarray, first: np.ndarray) -> float:
    """Density at the deepest point between two groups over the smaller of their peaks.

    ``projection`` holds the units of both groups on one axis and ``first`` marks the units of the
    first group. The density is a histogram over the central range of the projection (each tail
    beyond the ``_VALLEY_TAIL`` quantile left out, so a few outliers cannot squeeze the density
    into a few bins), smoothed with a Gaussian kernel at Silverman's normal-reference bandwidth
    ``0.9 * min(sd, IQR / 1.34) * n^(-1/5)``. The valley is the density minimum between the two
    group medians. The result is 1 for any unimodal density and tends to 0 for a clean gap
    between the groups.
    """
    from scipy.ndimage import gaussian_filter1d

    if first.all() or not first.any():
        return 1.0
    lo, hi = (
        float(v) for v in np.quantile(projection, [_VALLEY_TAIL, 1 - _VALLEY_TAIL])
    )
    if hi <= lo:
        return 1.0
    central = projection[(projection >= lo) & (projection <= hi)]
    q25, q75 = np.quantile(central, [0.25, 0.75])
    spread = float(central.std())
    if q75 > q25:
        spread = min(spread, float(q75 - q25) / 1.34)
    width = (hi - lo) / _VALLEY_BINS
    counts = np.histogram(central, _VALLEY_BINS, (lo, hi))[0].astype(np.float64)
    density = gaussian_filter1d(
        counts, 0.9 * spread * len(central) ** -0.2 / width, mode="constant"
    )
    a, b = sorted(
        int(np.clip((np.median(projection[side]) - lo) / width, 0, _VALLEY_BINS - 1))
        for side in (first, ~first)
    )
    valley = a + int(np.argmin(density[a : b + 1]))
    peak = min(density[: valley + 1].max(), density[valley:].max())
    return float(density[valley] / peak) if peak > 0 else 1.0


def within_precision(embedding: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """Inverse of the pooled within-cluster covariance of ``embedding``, Ledoit-Wolf shrunk.

    The covariance pools every cluster of ``codes`` around its own mean, so it rests on all units
    and stays stable for small clusters; the Ledoit-Wolf shrinkage toward the scaled identity keeps
    it invertible.
    """
    from sklearn.covariance import ledoit_wolf

    sizes = np.bincount(codes).astype(np.float64)
    means = np.zeros((len(sizes), embedding.shape[1]))
    np.add.at(means, codes, embedding)
    means /= sizes[:, None]
    covariance, _ = ledoit_wolf(embedding - means[codes], assume_centered=True)
    return np.asarray(np.linalg.pinv(covariance), dtype=np.float64)


def discriminant_axis(
    precision: np.ndarray, difference: np.ndarray
) -> np.ndarray | None:
    """Unit discriminant direction ``precision @ difference``, or None without separation."""
    axis = precision @ difference
    norm = np.linalg.norm(axis)
    return axis / norm if norm > 0 and np.isfinite(norm) else None


def merge_unsupported_clusters(
    embedding: np.ndarray,
    labels: np.ndarray,
    connectivities: sp.spmatrix,
    ratio: float = _MERGE_VALLEY_RATIO,
) -> tuple[np.ndarray, MergeInfo]:
    """Merge adjacent clusters that no density valley separates.

    Every pair of input clusters joined by the kNN graph ``connectivities`` (built on
    ``embedding``) gets its :func:`_valley_ratio` on its discriminant axis, the difference of the
    two means weighted by the inverse pooled within-cluster covariance (:func:`within_precision`).
    Two groups of input clusters are scored by the mean of the ratios of the input pairs across
    them, each weighted by its contact: the summed connectivity between the two input clusters.
    The pair of groups with the highest score is merged while the score is at least ``ratio``, as in
    Hennig's hierarchical merging of mixture components with the ridgeline ratio.

    The score rests on the input pairs, never on the union of two groups: in a union, a smaller
    group beside a larger one shows as a shoulder of its density rather than a separate peak, so
    a growing group would absorb its neighbours one by one. The contact weights keep two input
    clusters that touch through a few stray edges (the two ends of a merged chain) from blocking
    a merge that their broad contacts support.

    Returns
    -------
    tuple[np.ndarray, dict]
            Labels ``'0'``, ``'1'``, ... by decreasing group size, and provenance: the number of input
            clusters, the number of merges and the score of each merge.
    """
    embedding = np.asarray(embedding, dtype=np.float64)
    _, codes = np.unique(np.asarray(labels), return_inverse=True)
    k = int(codes.max()) + 1
    members = [np.flatnonzero(codes == c) for c in range(k)]
    sizes = np.bincount(codes, minlength=k).astype(np.float64)
    assignment = sp.csr_matrix(
        (np.ones(len(codes)), (codes, np.arange(len(codes)))), shape=(k, len(codes))
    )
    means = np.asarray(assignment @ embedding, dtype=np.float64) / sizes[:, None]
    contact = (assignment @ sparse_csr(connectivities) @ assignment.T).toarray()
    np.fill_diagonal(contact, 0)
    precision = (
        within_precision(embedding, codes) if k > 1 else np.eye(embedding.shape[1])
    )

    pair = np.zeros((k, k))
    for i, j in zip(*np.nonzero(np.triu(contact > 0))):
        axis = discriminant_axis(precision, means[j] - means[i])
        rows = np.concatenate([members[i], members[j]])
        pair[i, j] = pair[j, i] = (
            1.0
            if axis is None
            else _valley_ratio(embedding[rows] @ axis, np.arange(len(rows)) < sizes[i])
        )

    groups = [[c] for c in range(k)]
    merges = []
    while True:
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                weights = contact[np.ix_(groups[a], groups[b])]
                total = weights.sum()
                if total <= 0:
                    continue
                score = float(
                    (pair[np.ix_(groups[a], groups[b])] * weights).sum() / total
                )
                if score >= ratio and (best is None or score > best[0]):
                    best = (score, a, b)
        if best is None:
            break
        merges.append(round(best[0], 4))
        groups[best[1]] = groups[best[1]] + groups.pop(best[2])

    order = sorted(
        range(len(groups)), key=lambda g: (-sizes[groups[g]].sum(), min(groups[g]))
    )
    relabel = np.empty(k, dtype=np.int64)
    for rank, g in enumerate(order):
        relabel[groups[g]] = rank
    info: MergeInfo = {
        "n_leiden_clusters": k,
        "n_merges": len(merges),
        "merge_ratios": np.asarray(merges, dtype=np.float64),
    }
    return relabel[codes].astype(str), info
