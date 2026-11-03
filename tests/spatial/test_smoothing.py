"""Smoothing behavioral scenarios."""

from focus.spatial.units import SpatialUnits

import numpy as np

from focus.spatial.clusters import label_coherence
from focus.spatial.units import plan_spatial_units
from focus.spatial.smoothing import smoothing_operator
from tests.spatial.data import lattice


class TestSmoothing:
    """Behavioral contracts for smoothing."""

    def neighbours_of_interior(
        self, xy: np.ndarray, rings: int = 1
    ) -> tuple[np.ndarray, SpatialUnits]:
        """Return neighbour offsets and the unit plan for a central spot; verify row-normalized smoothing."""
        units = plan_spatial_units(xy)
        op = smoothing_operator(units, rings)
        np.testing.assert_allclose(np.asarray(op.sum(axis=1)).ravel(), 1, rtol=1e-6)
        center = np.argmin(np.linalg.norm(xy - xy.mean(axis=0), axis=1))
        row = op.getrow(center)
        return xy[row.indices] - xy[center], units

    def test_lattice_neighbourhoods_are_isotropic(self) -> None:
        """Verify lattice neighbourhoods are isotropic."""
        square, _ = lattice(40, 40, [16, 0], [0, 16], angle_deg=0.65)
        offsets, units = self.neighbours_of_interior(square)
        assert len(offsets) == 9
        hexagonal, _ = lattice(40, 40, [100, 0], [50, 100 * np.sqrt(3) / 2])
        offsets, _ = self.neighbours_of_interior(hexagonal)
        assert len(offsets) == 7
        np.testing.assert_allclose(
            sorted(np.linalg.norm(offsets, axis=1))[1:], 100, rtol=1e-6
        )
        rectangular, _ = lattice(40, 40, [10, 0], [0, 5])
        offsets, _ = self.neighbours_of_interior(rectangular)
        for d in offsets:
            assert np.any(np.all(np.isclose(offsets, -d), axis=1))
        assert np.linalg.norm(offsets, axis=1).max() <= 15 + 1e-6
        assert smoothing_operator(units, 0).nnz == units.n_units

    def test_irregular_neighbourhoods_use_nearest_units(self) -> None:
        """Verify irregular neighbourhoods use nearest units."""
        xy = np.random.default_rng(3).uniform(0, 1000, (2_000, 2))
        offsets, units = self.neighbours_of_interior(xy)
        assert len(offsets) == 7
        keep = np.arange(units.n_units) % 2 == 0
        assert smoothing_operator(units, 1, keep).shape == (keep.sum(), keep.sum())

    def test_label_coherence_is_chance_corrected(self) -> None:
        """Contiguous halves score near 1, shuffled labels near 0, one label is undefined."""
        xy, ij = lattice(40, 40, [16, 0], [0, 16])
        units = plan_spatial_units(xy)
        halves = (ij[:, 1] >= 20).astype(str)
        assert label_coherence(halves, units) > 0.9
        shuffled = np.random.default_rng(0).permutation(halves)
        assert abs(label_coherence(shuffled, units) or 0.0) < 0.1
        assert label_coherence(np.zeros(len(xy)).astype(str), units) is None
