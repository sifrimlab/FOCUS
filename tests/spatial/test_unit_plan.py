"""UnitPlan behavioral scenarios."""

from typing import cast

import numpy as np

from focus.spatial.settings import _MIN_UNITS
from focus.spatial.units import _voronoi_units, plan_spatial_units
from tests.spatial.data import lattice, same_partition


class TestUnitPlan:
    """Behavioral contracts for unit plan."""

    def test_cap_blocks_hold_k_squared_sites_in_any_pose(self) -> None:
        """Verify cap blocks hold k squared sites in any pose."""
        xy, _ = lattice(700, 500, [16, 0], [0, 16], angle_deg=0.65)
        units = plan_spatial_units(xy, cap=250_000)
        assert (units.info["method"], units.info["k"], units.info["driver"]) == (
            "lattice",
            2,
            "cap",
        )
        np.testing.assert_allclose(units.info["unit_size"], [32, 32], rtol=1e-6)
        assert units.n_units == 350 * 250
        assert np.all(np.bincount(cast(np.ndarray, units.bin_ids)) == 4)
        theta = np.deg2rad(37)
        rotation = np.array(
            [[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]]
        )
        moved = plan_spatial_units(xy @ rotation.T + [5e4, -3e3], cap=250_000)
        assert same_partition(
            cast(np.ndarray, moved.bin_ids), cast(np.ndarray, units.bin_ids)
        )

    def test_only_tissue_edge_blocks_are_partial(self) -> None:
        """Verify only tissue edge blocks are partial."""
        xy, _ = lattice(700, 700, [16, 0], [0, 16], origin=(0, 0))
        center, radius = np.array([350, 350]) * 16.0, 330 * 16.0
        disc = np.linalg.norm(xy - center, axis=1) < radius
        units = plan_spatial_units(xy[disc], cap=100_000)
        k = units.info["k"]
        sizes = np.bincount(cast(np.ndarray, units.bin_ids))
        assert sizes.max() <= k * k
        partial = sizes[cast(np.ndarray, units.bin_ids)] < k * k
        assert np.linalg.norm(
            xy[disc][partial] - center, axis=1
        ).min() > radius - 2 * k * 16 * np.sqrt(2)

    def test_hexagonal_blocks_have_equal_occupancy(self) -> None:
        """Verify hexagonal blocks have equal occupancy."""
        xy, _ = lattice(600, 500, [100, 0], [50, 100 * np.sqrt(3) / 2])
        units = plan_spatial_units(xy, cap=250_000)
        assert (units.info["method"], units.info["k"]) == ("lattice", 2)
        assert np.all(np.bincount(cast(np.ndarray, units.bin_ids)) == 4)

    def test_signal_target_grows_blocks_and_rich_spots_stay_native(self) -> None:
        """Verify signal target grows blocks and rich spots stay native."""
        xy, _ = lattice(300, 300, [2, 0], [0, 2])
        counts = np.random.default_rng(1).poisson(3, len(xy))
        units = plan_spatial_units(xy, counts=counts, min_median_counts=50)
        assert (units.info["method"], units.info["k"], units.info["driver"]) == (
            "lattice",
            5,
            "signal",
        )
        assert (
            np.median(np.bincount(cast(np.ndarray, units.bin_ids), weights=counts))
            >= 50
        )
        native = plan_spatial_units(xy, counts=counts * 0 + 100, min_median_counts=50)
        assert native.bin_ids is None
        assert (native.info["method"], native.info["layout"]) == ("native", "lattice")

    def test_signal_target_keeps_a_minimum_number_of_units(self) -> None:
        """Verify signal target keeps a minimum number of units."""
        xy, _ = lattice(300, 300, [2, 0], [0, 2])
        units = plan_spatial_units(xy, counts=np.zeros(len(xy)), min_median_counts=50)
        assert units.info["signal_limited"]
        assert units.info["k"] == 9
        assert units.n_units >= _MIN_UNITS

    def test_voronoi_supercells_are_deterministic_bounded_and_balanced(self) -> None:
        """Verify voronoi supercells are deterministic bounded and balanced."""
        xy = np.random.default_rng(2).uniform(0, 5000, (300_000, 2))
        first = plan_spatial_units(xy, cap=20_000, seed=3)
        again = plan_spatial_units(xy, cap=20_000, seed=3)
        np.testing.assert_array_equal(first.bin_ids, again.bin_ids)
        assert (first.info["method"], first.info["layout"]) == ("voronoi", "irregular")
        assert first.n_units <= 20_000

        def cv(ids: np.ndarray | None) -> float:
            """Return the coefficient of variation of occupied-unit spot counts for the supplied unit IDs."""
            values = cast(np.ndarray, ids)
            return float(np.bincount(values).std() / np.bincount(values).mean())

        assert cv(first.bin_ids) < 0.4
        assert cv(first.bin_ids) < 0.75 * cv(
            _voronoi_units(xy, 20_000, 3, iterations=0)[0]
        )

    def test_sparse_irregular_spots_get_supercells_from_the_signal_target(self) -> None:
        """Verify sparse irregular spots get supercells from the signal target."""
        xy = np.random.default_rng(4).uniform(0, 3000, (40_000, 2))
        units = plan_spatial_units(xy, counts=np.full(len(xy), 5), min_median_counts=50)
        assert (units.info["method"], units.info["driver"]) == ("voronoi", "signal")
        assert units.n_units <= 4_000
