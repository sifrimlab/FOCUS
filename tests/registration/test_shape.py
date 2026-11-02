"""Shape behavioral scenarios."""

from tests.assertions import assert_close

import numpy as np

from focus.spatial.footprint_shape import classify_lattice, footprint_shape
from tests.registration.data import square_lattice, hex_lattice


class TestShape:
    """Behavioral contracts for shape."""

    def test_hexagonal_lattice_gives_ellipse(self) -> None:
        """Verify hexagonal lattice gives ellipse."""
        shape = footprint_shape(
            hex_lattice(angle=np.deg2rad(3)), np.array([55.0, 55.0])
        )
        assert (shape.lattice, shape.kind) == ("hexagonal", "ellipse")
        assert_close(shape.sigma, 27.5)

    def test_rotated_square_lattice(self) -> None:
        """Verify rotated square lattice."""
        angle = np.deg2rad(0.65)
        shape = footprint_shape(square_lattice(angle=angle), np.array([8.0, 8.0]))
        assert (shape.lattice, shape.kind) == ("square", "rectangle")
        assert_close(shape.angle, angle, places=6)

    def test_rectangular_raster_and_exact_axes(self) -> None:
        """Verify rectangular raster and exact axes."""
        i, j = np.meshgrid(np.arange(20), np.arange(30))
        pts = np.column_stack([i.ravel() * 10.0, j.ravel() * 5.0])
        shape = footprint_shape(pts, np.array([10.0, 5.0]))
        assert (shape.lattice, shape.angle) == ("rectangular", 0.0)
        assert footprint_shape(square_lattice(), np.array([10.0, 10.0])).angle == 0.0

    def test_irregular_points(self) -> None:
        """Verify irregular points."""
        pts = np.random.default_rng(2).uniform(0, 1000, (500, 2))
        shape = footprint_shape(pts, np.array([20.0, 20.0]))
        assert (shape.lattice, shape.kind, shape.angle) == (
            "irregular",
            "rectangle",
            0.0,
        )
        assert classify_lattice(None) == ("irregular", 0.0)
