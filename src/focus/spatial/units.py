"""Plan temporary lattice blocks or Voronoi units without changing native observations."""

from numpy.typing import NDArray

from typing import cast
from focus.spatial.types import UnitInfo

from typing import NamedTuple

import numpy as np

from focus.spatial.settings import _LLOYD_ITERATIONS, _MIN_UNITS, _SPATIAL_CAP
from focus.spatial.lattice import _lattice_units, infer_lattice


def _unit_sizes_and_sums(
    flat: np.ndarray, weights: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray | None]:
    """Spot count and summed ``weights`` of every occupied unit id in ``flat``."""
    if flat.max() < 4 * len(flat) + 1_000_000:
        sizes = np.bincount(flat)
        keep = sizes > 0
        sums = None if weights is None else np.bincount(flat, weights=weights)[keep]
        return sizes[keep], sums
    _, inverse = np.unique(flat, return_inverse=True)
    return np.bincount(inverse), None if weights is None else np.bincount(
        inverse, weights=weights
    )


def _voronoi_units(
    coords: np.ndarray, m: int, seed: int, iterations: int = _LLOYD_ITERATIONS
) -> tuple[np.ndarray, int]:
    """Assign spots to ``m`` Voronoi tiles: seeded spot sample as seeds, then Lloyd iterations.

    Seeds follow spot density, and each Lloyd iteration moves a seed to the centroid of its tile,
    so tiles hold similar spot counts. Nothing about the tiling is periodic.
    """
    from scipy.spatial import cKDTree

    rng = np.random.default_rng(seed)
    seeds = coords[np.sort(rng.choice(len(coords), size=m, replace=False))]
    for iteration in range(iterations + 1):
        _, nearest = cKDTree(seeds).query(coords, workers=-1)
        if iteration == iterations:
            break
        size = np.bincount(nearest, minlength=m)
        filled = size > 0
        for axis in (0, 1):
            seeds[filled, axis] = (
                np.bincount(nearest, weights=coords[:, axis], minlength=m)[filled]
                / size[filled]
            )
    uniq, bin_ids = np.unique(nearest, return_inverse=True)
    return bin_ids.astype(np.intp), int(uniq.size)


class SpatialUnits(NamedTuple):
    """Clustering units of one sample (see :func:`plan_spatial_units`); never persisted."""

    bin_ids: (
        NDArray[np.intp] | None
    )  # (n_spots,) unit of each spot; None when every spot is its own unit
    n_units: int
    info: UnitInfo  # provenance
    centroids: NDArray[np.float32 | np.float64]  # (n_units, 2) unit centroid
    grid: (
        NDArray[np.int64] | None
    )  # (n_units, 2) unit lattice index; None for irregular layouts
    basis: NDArray[np.float64] | None  # (2, 2) physical step of one unit-grid index


def _unit_centroids(
    coords: np.ndarray, bin_ids: np.ndarray, n_units: int
) -> np.ndarray:
    """Average physical spot coordinates within each occupied clustering unit."""
    size = np.bincount(bin_ids, minlength=n_units)
    return np.column_stack(
        [
            np.bincount(bin_ids, weights=coords[:, i], minlength=n_units) / size
            for i in (0, 1)
        ]
    )


def plan_spatial_units(
    coords: np.ndarray,
    *,
    cap: int = _SPATIAL_CAP,
    counts: np.ndarray | None = None,
    min_median_counts: float | None = None,
    seed: int = 0,
) -> SpatialUnits:
    """Choose the units spot clustering runs on, bounding its cost without a periodic imprint.

    The layout is read from the coordinates (:func:`infer_lattice`). Spots are clustered as they
    are unless the sample has more than ``cap`` spots or (when ``counts`` and
    ``min_median_counts`` are given, ST sub-cellular) the median spot holds fewer than
    ``min_median_counts``. Otherwise spots are grouped into units; the caller sums each unit's
    expression (:func:`_bin_sum_matrix`) and every spot inherits its unit's label.

    - **lattice**: the unit of a spot is ``(i // k, j // k)`` on its lattice indices, a block of
      k x k sites (a parallelogram on hexagonal lattices). Every interior block holds exactly k^2
      sites, so unit occupancy cannot vary periodically, and the blocks follow the lattice, not the
      image axes. ``k`` is the smallest integer with at most ``cap`` occupied blocks whose median
      block count reaches ``min_median_counts``; the signal criterion stops before fewer than
      ``_MIN_UNITS`` blocks remain.
    - **voronoi** (no lattice: segmented cells, beads): ``m`` tiles from :func:`_voronoi_units`,
      ``m = min(cap, n / s)`` where ``s`` spots per tile is the estimate that reaches the
      median-count target (never fewer than ``_MIN_UNITS`` tiles).

    Fully deterministic for a given ``seed``. The returned geometry (unit grid and basis, or unit
    centroids) defines the neighbourhoods of :func:`smoothing_operator`.
    """
    coords = np.asarray(coords, dtype=np.float64)
    n = len(coords)
    signal = counts is not None and min_median_counts is not None and n > _MIN_UNITS
    counts = None if counts is None else np.asarray(counts, dtype=np.float64)
    need_cap = n > cap
    need_signal = signal and float(np.median(cast(np.ndarray, counts))) < cast(
        float, min_median_counts
    )
    limited = False
    lattice = infer_lattice(coords, seed=seed)

    if lattice is not None:
        idx, basis = lattice
        step = np.linalg.norm(basis, axis=1).tolist()
        k, driver = 1, []
        if need_cap:
            k = max(1, int(np.sqrt(n / cap)))
            while len(_unit_sizes_and_sums(_lattice_units(idx, k))[0]) > cap:
                k += 1
            driver.append("cap")
        if need_signal:
            k_cap = k
            while True:
                _, sums = _unit_sizes_and_sums(_lattice_units(idx, k), counts)
                if np.median(cast(np.ndarray, sums)) >= cast(float, min_median_counts):
                    break
                if (
                    len(_unit_sizes_and_sums(_lattice_units(idx, k + 1))[0])
                    < _MIN_UNITS
                ):
                    limited = True
                    break
                k += 1
            if k > k_cap:
                driver.append("signal")
        if k == 1:
            return SpatialUnits(
                None,
                n,
                {
                    "method": "native",
                    "layout": "lattice",
                    "step": step,
                    "n_units": n,
                    "signal_limited": limited,
                },
                coords,
                idx,
                basis,
            )
        width = int(idx[:, 1].max() // k) + 1
        uniq, bin_ids = np.unique(_lattice_units(idx, k), return_inverse=True)
        bin_ids = bin_ids.astype(np.intp)
        n_units = int(uniq.size)
        info: UnitInfo = {
            "method": "lattice",
            "layout": "lattice",
            "step": step,
            "k": k,
            "unit_size": [k * v for v in step],
            "n_units": n_units,
            "driver": "+".join(driver),
            "signal_limited": limited,
        }
        return SpatialUnits(
            bin_ids,
            n_units,
            info,
            _unit_centroids(coords, bin_ids, n_units),
            np.column_stack([uniq // width, uniq % width]),
            k * basis,
        )

    m, driver = n, []
    if need_cap:
        m = cap
        driver.append("cap")
    if need_signal:
        median = float(np.median(cast(np.ndarray, counts)))
        per_tile = (
            int(np.ceil(cast(float, min_median_counts) / median)) if median > 0 else n
        )
        m_signal = n // per_tile
        if m_signal < _MIN_UNITS:
            m_signal, limited = _MIN_UNITS, True
        if m_signal < m:
            m = m_signal
            driver.append("signal")
    if m >= n:
        return SpatialUnits(
            None,
            n,
            {
                "method": "native",
                "layout": "irregular",
                "n_units": n,
                "signal_limited": limited,
            },
            coords,
            None,
            None,
        )
    bin_ids, n_units = _voronoi_units(coords, m, seed)
    info = {
        "method": "voronoi",
        "layout": "irregular",
        "n_units": n_units,
        "lloyd_iterations": _LLOYD_ITERATIONS,
        "seed": seed,
        "driver": "+".join(driver),
        "signal_limited": limited,
    }
    return SpatialUnits(
        bin_ids, n_units, info, _unit_centroids(coords, bin_ids, n_units), None, None
    )


def describe_units(n_spots: int, info: UnitInfo) -> str:
    """One reporter line for a clustering unit plan."""
    if info["method"] == "native":
        return f"Clustering: {n_spots} spots at native resolution ({info['layout']} layout)"
    if info["method"] == "lattice":
        size = " x ".join(f"{v:g}" for v in np.round(info["unit_size"], 2))
        return (
            f"Clustering: {n_spots} spots -> {info['n_units']} lattice blocks "
            f"(k={info['k']}, {size}; {info['driver']})"
        )
    return f"Clustering: {n_spots} spots -> {info['n_units']} Voronoi supercells ({info['driver']})"
