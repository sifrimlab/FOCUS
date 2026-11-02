"""Smoothing behavioral scenarios."""

from typing import cast

from focus.spatial.units import SpatialUnits

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.spatial.clusters import label_coherence
from focus.spatial.units import plan_spatial_units
from focus.spatial.smoothing import smoothing_operator
from focus.preprocessing.transcriptomics.clustering import native_cluster_labels
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

    def test_neighbourhood_recovers_weak_domains_without_patches(self) -> None:
        """Verify neighbourhood recovers weak domains without patches."""
        xy, ij = lattice(80, 80, [16, 0], [0, 16])
        truth = ij[:, 1] >= 40
        means = np.full((len(xy), 200), 0.3)
        means[~truth, :30] += 0.25
        means[truth, 30:60] += 0.25
        X = sp.csr_matrix(np.random.default_rng(7).poisson(means).astype(np.float32))
        units = plan_spatial_units(xy)
        genes = [f"g{i}" for i in range(200)]
        plain, eligible, _ = native_cluster_labels(X, genes, units=units, rings=0)
        for rings in (1, 2):
            labels, eligible, info = native_cluster_labels(
                X, genes, units=units, rings=rings
            )
            assert info["n_clusters"] == 2
            assert adjusted_rand_score(truth[eligible], labels[eligible]) > 0.9
            assert info["label_coherence"] > cast(
                float, label_coherence(plain[eligible].astype(str), units, eligible)
            )
