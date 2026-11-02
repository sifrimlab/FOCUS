"""Synthetic data and shared setup for spatial scenarios."""

from typing import cast

from numpy.typing import ArrayLike
import scipy.sparse as sp

import numpy as np


def lattice(
    n0: int,
    n1: int,
    a0: ArrayLike,
    a1: ArrayLike,
    angle_deg: float = 0.0,
    origin: tuple[float, float] = (1234.5, -987.6),
) -> tuple[np.ndarray, np.ndarray]:
    """Spots i * a0 + j * a1, rotated; returns coords and the true (i, j)."""
    i, j = np.mgrid[:n0, :n1]
    ij = np.column_stack([i.ravel(), j.ravel()])
    a = np.deg2rad(angle_deg)
    rotation = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return (ij @ np.vstack([a0, a1])) @ rotation.T + origin, ij


def same_partition(a: np.ndarray, b: np.ndarray) -> bool:
    """Return whether two label arrays define the same partition, ignoring category names."""
    pairs = set(zip(a.tolist(), b.tolist()))
    return len(pairs) == len(set(a.tolist())) == len(set(b.tolist()))


def knn(points: np.ndarray, k: int = 15) -> sp.csr_matrix:
    """Return the symmetrized CSR k-nearest-neighbour graph of the supplied points."""
    from sklearn.neighbors import kneighbors_graph

    graph = kneighbors_graph(points, k)
    return cast(sp.csr_matrix, (graph + graph.T).tocsr())


def domains_under_a_field(
    n: int = 120, seed: int = 1
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Three column domains under a smooth random field; Leiden at 0.5 cuts the field into an extra cluster."""
    from scipy.ndimage import gaussian_filter

    xy, ij = lattice(n, n, [16, 0], [0, 16])
    domain = np.select([ij[:, 1] < n // 3, ij[:, 1] < 2 * n // 3], [0, 1], 2)
    rng = np.random.default_rng(seed)
    field = gaussian_filter(rng.normal(size=(n, n)), 10).ravel()
    means = np.full((len(xy), 300), 0.4)
    for d in range(3):
        means[domain == d, 30 * d : 30 * d + 30] += 0.5
    means[:, 100:160] *= np.exp(0.35 * field / field.std())[:, None]
    return xy, domain, rng.poisson(means).astype(np.float32)
