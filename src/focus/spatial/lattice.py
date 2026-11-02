"""Infer acquisition lattice bases and map native spots to integer lattice cells."""

from typing import cast

import numpy as np


def _unique_rows(idx: np.ndarray) -> bool:
    """Check whether every nonnegative integer lattice coordinate is unique."""
    span = idx.max(axis=0) + 1
    return np.unique(idx[:, 0] * span[1] + idx[:, 1]).size == len(idx)


def _direction_gap(a: np.ndarray, b: float) -> np.ndarray:
    """Smallest angle (radians) between undirected directions ``a`` and ``b``, all in [0, pi)."""
    return np.abs(((a - b + np.pi / 2) % np.pi) - np.pi / 2)


def infer_lattice(
    coords: np.ndarray, sample: int = 20_000, seed: int = 0
) -> tuple[np.ndarray, np.ndarray] | None:
    """Integer lattice indices of every spot from the coordinates alone, or ``None`` if irregular.

    Works for square lattices at any rotation (Visium HD), hexagonal lattices (standard Visium,
    expressed in a 60 degree basis) and rectangular rasters (MSI), with tissue holes. No metadata
    beyond the coordinates is needed; ``spot_size`` is a footprint and can differ from the pitch.

    1. On a seeded sample of at most ``sample`` spots, the 8 nearest displacements of each spot
       give the basis: ``a`` is the median vector of the dominant direction among nearest-shell
       displacements (length <= 1.2 x median nearest distance); ``b`` is the shortest displacement
       cluster more than 25 degrees away from ``a`` (length <= 3 x the nearest distance).
    2. Starting from the spot closest to the median position, spots within 20 steps are rounded
       to indices and the basis and origin are refitted by least squares; the radius triples until
       it holds every spot. Refitting on a growing region keeps a small basis error from
       accumulating into rounding mistakes thousands of steps away.
    3. The lattice is accepted only if the 99th percentile residual is <= 0.1 step, the maximum
       residual is <= 0.45 step (rounding stays unambiguous, so a few displaced spots do not
       reject the lattice), and every spot has its own lattice site.

    Returns
    -------
    tuple[np.ndarray, np.ndarray] | None
            ``(idx, basis)``: (n, 2) int64 indices starting at 0, and the (2, 2) basis whose row ``i``
            is the physical displacement of one step along ``idx[:, i]`` (``coords ~ idx @ basis + o``).
    """
    from scipy.spatial import cKDTree

    coords = np.asarray(coords, dtype=np.float64)
    n = len(coords)
    if n < 16 or not np.isfinite(coords).all():
        return None
    rng = np.random.default_rng(seed)
    probe = coords[rng.choice(n, min(sample, n), replace=False)]
    dist, nb = cast(tuple[np.ndarray, np.ndarray], cKDTree(coords).query(probe, k=9))
    vec = (coords[nb[:, 1:]] - probe[:, None, :]).reshape(-1, 2)
    length = dist[:, 1:].ravel()
    nearest = float(np.median(dist[:, 1]))
    if not nearest > 0:
        return None
    angle = np.mod(np.arctan2(vec[:, 1], vec[:, 0]), np.pi)

    def axis(candidates: np.ndarray) -> tuple[np.ndarray, float]:
        """Estimate a lattice basis direction from the selected neighbor displacements."""
        hist, edges = np.histogram(angle[candidates], bins=360, range=(0, np.pi))
        mode = edges[hist.argmax()] + np.pi / 720
        v = vec[candidates & (_direction_gap(angle, mode) < np.deg2rad(5))]
        return np.median(
            v * np.sign(v @ [np.cos(mode), np.sin(mode)])[:, None], axis=0
        ), mode

    a, mode = axis(length <= 1.2 * nearest)
    other = (_direction_gap(angle, mode) > np.deg2rad(25)) & (length <= 3 * nearest)
    if not other.any():
        return None
    b, _ = axis(other & (length <= 1.15 * np.quantile(length[other], 0.02)))
    basis = np.vstack([a, b])
    if abs(np.linalg.det(basis)) < 1e-3 * nearest**2:
        return None
    origin = coords[
        np.argmin(np.linalg.norm(coords - np.median(coords, axis=0), axis=1))
    ]
    radius = 20 * nearest
    while True:
        inside = np.linalg.norm(coords - origin, axis=1) <= radius
        idx = np.rint(np.linalg.solve(basis.T, (coords[inside] - origin).T).T)
        design = np.column_stack([idx, np.ones(len(idx))])
        if np.linalg.matrix_rank(design) == 3:
            fit = np.linalg.lstsq(design, coords[inside], rcond=None)[0]
            basis, origin = fit[:2], fit[2]
        if inside.all():
            break
        radius *= 3
    idx = np.rint(np.linalg.solve(basis.T, (coords - origin).T).T)
    residual = (
        np.linalg.norm(coords - (idx @ basis + origin), axis=1)
        / np.linalg.norm(basis, axis=1).min()
    )
    idx = idx.astype(np.int64)
    idx -= idx.min(axis=0)
    if (
        np.quantile(residual, 0.99) > 0.1
        or residual.max() > 0.45
        or not _unique_rows(idx)
    ):
        return None
    return idx, basis


def _lattice_units(idx: np.ndarray, k: int) -> np.ndarray:
    """Flat id of the k x k lattice block holding each spot (blocks anchored at index 0)."""
    q = idx // k
    return cast(np.ndarray, q[:, 0] * (int(q[:, 1].max()) + 1) + q[:, 1])
