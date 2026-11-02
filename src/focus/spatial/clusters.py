"""Merge adjacent analytical clusters according to their density valleys."""

from focus.spatial.types import MergeInfo
from focus.spatial.matrices import sparse_csr

import numpy as np
import scipy.sparse as sp

from focus.spatial.units import SpatialUnits
from focus.spatial.settings import _MERGE_VALLEY_RATIO, _VALLEY_BINS
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
    first group. The density is a histogram smoothed with a Gaussian kernel at Silverman's
    normal-reference bandwidth ``0.9 * min(sd, IQR / 1.34) * n^(-1/5)``. The valley is the density
    minimum between the two group medians. The result is 1 for any unimodal density and tends to 0
    for a clean gap between the groups.
    """
    from scipy.ndimage import gaussian_filter1d

    n = len(projection)
    lo, hi = float(projection.min()), float(projection.max())
    if first.all() or not first.any() or hi <= lo:
        return 1.0
    q25, q75 = np.quantile(projection, [0.25, 0.75])
    spread = float(projection.std())
    if q75 > q25:
        spread = min(spread, float(q75 - q25) / 1.34)
    width = (hi - lo) / _VALLEY_BINS
    counts = np.histogram(projection, _VALLEY_BINS, (lo, hi))[0].astype(np.float64)
    density = gaussian_filter1d(counts, 0.9 * spread * n**-0.2 / width, mode="constant")
    a, b = sorted(
        min(_VALLEY_BINS - 1, int((np.median(projection[side]) - lo) / width))
        for side in (first, ~first)
    )
    valley = a + int(np.argmin(density[a : b + 1]))
    return float(
        density[valley] / min(density[: valley + 1].max(), density[valley:].max())
    )


def merge_unsupported_clusters(
    embedding: np.ndarray,
    labels: np.ndarray,
    connectivities: sp.spmatrix,
    ratio: float = _MERGE_VALLEY_RATIO,
) -> tuple[np.ndarray, MergeInfo]:
    """Merge adjacent clusters that no density valley separates.

    Two clusters are adjacent when the kNN graph ``connectivities`` (built on ``embedding``) joins
    them. The units of two groups are projected on the axis between the group centroids and their
    :func:`_valley_ratio` is computed. The adjacent pair of groups with the highest ratio is merged
    while that ratio is at least ``ratio``, and merged groups are tested on their union, as in
    Hennig's merging of mixture components on the discriminant projection. A merge is refused when
    an adjacent pair of the input clusters across the two groups had a ratio below ``ratio``, so a
    valley seen between the input clusters is not hidden by later merges.

    Returns
    -------
    tuple[np.ndarray, dict]
            Labels ``'0'``, ``'1'``, ... by decreasing group size, and provenance: the number of input
            clusters, the number of merges and the ratio of each merge.
    """
    _, codes = np.unique(np.asarray(labels), return_inverse=True)
    k = int(codes.max()) + 1
    members = [np.flatnonzero(codes == c) for c in range(k)]
    sizes = np.bincount(codes, minlength=k).astype(np.float64)
    assignment = sp.csr_matrix(
        (np.ones(len(codes)), (codes, np.arange(len(codes)))), shape=(k, len(codes))
    )
    sums = np.asarray(assignment @ embedding, dtype=np.float64)
    adjacent = (assignment @ sparse_csr(connectivities) @ assignment.T).toarray() > 0
    np.fill_diagonal(adjacent, False)

    def union_ratio(x: list[int], y: list[int]) -> float:
        """Measure the density valley separating two candidate cluster groups."""
        axis = (
            sums[y].sum(axis=0) / sizes[y].sum() - sums[x].sum(axis=0) / sizes[x].sum()
        )
        norm = np.linalg.norm(axis)
        if not norm > 0:
            return 1.0
        rows = np.concatenate([members[c] for c in x + y])
        return _valley_ratio(
            embedding[rows] @ (axis / norm), np.arange(len(rows)) < sizes[x].sum()
        )

    pair = np.full((k, k), np.nan)
    tested: dict[frozenset[tuple[int, ...]], float] = {}
    for i, j in zip(*np.nonzero(np.triu(adjacent))):
        pair[i, j] = pair[j, i] = tested[frozenset(((i,), (j,)))] = union_ratio(
            [i], [j]
        )
    groups = [[c] for c in range(k)]
    merges = []
    while True:
        best = None
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                x, y = groups[a], groups[b]
                across = pair[np.ix_(x, y)]
                if np.isnan(across).all() or np.nanmin(across) < ratio:
                    continue
                key = frozenset((tuple(sorted(x)), tuple(sorted(y))))
                if key not in tested:
                    tested[key] = union_ratio(x, y)
                if tested[key] >= ratio and (best is None or tested[key] > best[0]):
                    best = (tested[key], a, b)
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
