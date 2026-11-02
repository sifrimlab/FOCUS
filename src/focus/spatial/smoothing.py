"""Construct sparse neighbourhood smoothing operators for temporary clustering units."""

from typing import cast

import numpy as np
import scipy.sparse as sp

from focus.spatial.units import SpatialUnits
from focus.spatial.settings import _NEIGHBOURHOOD_WEIGHT


def smoothing_operator(
    units: SpatialUnits, rings: int, keep: np.ndarray | None = None
) -> sp.csr_matrix:
    """Row-normalised neighbourhood mean over the kept units, shape (n_kept, n_kept).

    Each kept unit averages itself and its kept neighbours. Applied to a PCA embedding before the
    kNN graph, it lets weak units borrow signal from adjacent tissue, so labels follow spatial
    domains instead of per-unit noise. The neighbourhood is isotropic in physical space:

    - lattice layouts: units whose grid offset has a physical length of at most (rings + 0.5)
      times the longest grid step; for rings=1 that is 8 units on square grids and 6 on hexagonal
      ones. Lookup uses sorted grid keys, O(n log n).
    - irregular layouts: the 3 * rings * (rings + 1) nearest unit centroids (6 for rings=1).

    ``rings=0`` returns the identity. ``keep`` selects the units taking part (eligible units).
    """
    keep = (
        np.ones(units.n_units, dtype=bool)
        if keep is None
        else np.asarray(keep, dtype=bool)
    )
    kept = np.flatnonzero(keep)
    m = len(kept)
    if rings <= 0 or m < 2:
        return sp.identity(m, dtype=np.float32, format="csr")
    rows, cols = [np.arange(m)], [np.arange(m)]
    if units.grid is not None:
        steps = np.linalg.norm(cast(np.ndarray, units.basis), axis=1)
        limit = (rings + 0.5) * steps.max()
        reach = int(np.ceil(limit / steps.min()))
        grid = units.grid[kept] - units.grid[kept].min(axis=0) + reach
        width = int(grid[:, 1].max()) + reach + 1
        key = grid[:, 0] * width + grid[:, 1]
        order = np.argsort(key)
        ordered = key[order]
        for di in range(-reach, reach + 1):
            for dj in range(-reach, reach + 1):
                if (di, dj) == (0, 0) or np.linalg.norm(
                    np.array([di, dj]) @ units.basis
                ) > limit * (1 + 1e-9):
                    continue
                target = key + di * width + dj
                pos = np.minimum(np.searchsorted(ordered, target), m - 1)
                hit = ordered[pos] == target
                rows.append(np.flatnonzero(hit))
                cols.append(order[pos[hit]])
    else:
        from scipy.spatial import cKDTree

        k = min(3 * rings * (rings + 1), m - 1)
        centroids = units.centroids[kept]
        _, nb = cast(
            tuple[np.ndarray, np.ndarray],
            cKDTree(centroids).query(centroids, k=k + 1, workers=-1),
        )
        rows.append(np.repeat(np.arange(m), k))
        cols.append(nb[:, 1:].ravel())
    row_indices, column_indices = np.concatenate(rows), np.concatenate(cols)
    adjacency: sp.csr_matrix = sp.csr_matrix(
        (np.ones(len(row_indices), dtype=np.float32), (row_indices, column_indices)),
        shape=(m, m),
    )
    return cast(
        sp.csr_matrix,
        (sp.diags(1.0 / np.asarray(adjacency.sum(axis=1)).ravel()) @ adjacency).tocsr(),
    )


def spatial_embedding(
    pcs: np.ndarray, operator: sp.csr_matrix, weight: float = _NEIGHBOURHOOD_WEIGHT
) -> np.ndarray:
    """Clustering embedding of each unit: its own PCs and its neighbourhood mean, weighted.

    ``operator`` is a :func:`smoothing_operator`; ``weight`` is the share of the neighbourhood
    term (see ``_NEIGHBOURHOOD_WEIGHT``). Euclidean distances on the result are
    ``(1 - weight) * d_own^2 + weight * d_neighbourhood^2``.
    """
    return np.hstack(
        [np.sqrt(1 - weight) * pcs, np.sqrt(weight) * (operator @ pcs)]
    ).astype(np.float32)
