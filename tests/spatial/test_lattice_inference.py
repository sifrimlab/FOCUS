"""LatticeInference behavioral scenarios."""

from typing import cast

import numpy as np

from focus.spatial.lattice import infer_lattice
from tests.spatial.data import lattice


class TestLatticeInference:
    """Behavioral contracts for lattice inference."""

    def assert_relabelling(self, idx: np.ndarray, truth: np.ndarray) -> None:
        """idx is an integer affine relabelling of truth (axis swap/flip and shift)."""
        design = np.column_stack([truth, np.ones(len(truth))])
        fit = np.linalg.lstsq(design, idx, rcond=None)[0]
        np.testing.assert_allclose(design @ fit, idx, atol=1e-6)
        np.testing.assert_allclose(fit[:2], np.round(fit[:2]), atol=1e-9)
        assert abs(round(np.linalg.det(fit[:2]))) == 1

    def test_rotated_square_lattice_with_holes(self) -> None:
        """Verify rotated square lattice with holes."""
        xy, ij = lattice(400, 300, [16, 0], [0, 16], angle_deg=0.65)
        keep = np.random.default_rng(0).random(len(xy)) < 0.8
        idx, basis = cast(tuple[np.ndarray, np.ndarray], infer_lattice(xy[keep]))
        self.assert_relabelling(idx, ij[keep])
        np.testing.assert_allclose(np.linalg.norm(basis, axis=1), [16, 16], rtol=1e-6)

    def test_hexagonal_lattice_and_rectangular_raster(self) -> None:
        """Verify hexagonal lattice and rectangular raster."""
        xy, ij = lattice(60, 80, [100, 0], [50, 100 * np.sqrt(3) / 2], angle_deg=3)
        self.assert_relabelling(
            cast(tuple[np.ndarray, np.ndarray], infer_lattice(xy))[0], ij
        )
        xy, ij = lattice(300, 200, [10, 0], [0, 5])
        idx, basis = cast(tuple[np.ndarray, np.ndarray], infer_lattice(xy))
        self.assert_relabelling(idx, ij)
        np.testing.assert_allclose(
            sorted(np.linalg.norm(basis, axis=1)), [5, 10], rtol=1e-6
        )

    def test_irregular_jittered_and_duplicated_layouts_are_rejected(self) -> None:
        """Verify irregular jittered and duplicated layouts are rejected."""
        rng = np.random.default_rng(1)
        assert infer_lattice(rng.uniform(0, 5000, (50_000, 2))) is None
        xy, _ = lattice(200, 200, [10, 0], [0, 10])
        assert infer_lattice(xy + rng.uniform(-6, 6, xy.shape)) is None
        assert infer_lattice(np.vstack([xy, xy[:10]])) is None
