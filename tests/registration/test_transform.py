"""Transform behavioral scenarios."""

import pytest
from tests.assertions import assert_close

import numpy as np

from focus.spatial.transforms import fit_anchor_to_target
from tests.registration.data import square_lattice


class TestTransform:
    """Behavioral contracts for transform."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.src = square_lattice(30, 13.0, origin=(40.0, 25.0))
        self.spot = np.array([13.0, 13.0])

    def test_identity_and_translation(self) -> None:
        """Verify identity and translation."""
        t, valid = fit_anchor_to_target(
            self.src, self.src.astype(np.float32), self.spot
        )
        assert t.kind == "identity"
        assert valid.all()
        dst = (self.src + [10.25, -3.5]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        assert t.kind == "translation"
        np.testing.assert_allclose(t.to_anchor(dst), self.src, atol=1e-3)

    def test_similarity_with_reflection(self) -> None:
        """Verify similarity with reflection."""
        a = np.deg2rad(23)
        lin = (
            0.37
            * np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
            @ np.diag([1.0, -1.0])
        )
        dst = (self.src @ lin.T + [300.0, 900.0]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        assert t.kind == "homography"
        jac = t.jacobian(self.src[:1])[0]
        assert np.linalg.det(jac) < 0
        assert_close(np.sqrt(abs(np.linalg.det(jac))), 0.37, places=5)
        np.testing.assert_allclose(
            t.to_anchor(dst), self.src, atol=1e-6 * np.ptp(self.src)
        )

    def test_projective_jacobian(self) -> None:
        """Verify projective jacobian."""
        h = np.array([[1.1, 0.05, 20.0], [-0.03, 0.95, 5.0], [1e-4, 5e-5, 1.0]])
        hom = np.column_stack([self.src, np.ones(len(self.src))]) @ h.T
        dst = (hom[:, :2] / hom[:, 2:]).astype(np.float32)
        t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        assert t.kind == "homography"
        p = self.src[[17]]
        eps = 1e-4
        num = np.column_stack(
            [
                (t.to_target(p + [eps, 0]) - t.to_target(p - [eps, 0]))[0] / (2 * eps),
                (t.to_target(p + [0, eps]) - t.to_target(p - [0, eps]))[0] / (2 * eps),
            ]
        )
        np.testing.assert_allclose(t.jacobian(p)[0], num, rtol=1e-5)

    def test_nan_rows_are_excluded(self) -> None:
        """Verify nan rows are excluded."""
        dst = (self.src * 2.0 + 7.0).astype(np.float32)
        dst[::20] = np.nan
        t, valid = fit_anchor_to_target(self.src, dst, self.spot)
        assert int((~valid).sum()) == len(self.src[::20])
        assert t.kind == "homography"

    def test_inconsistent_coordinates_fall_back_to_local_affine(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify inconsistent coordinates fall back to local affine."""
        caplog.clear()
        rng = np.random.default_rng(1)
        dst = self.src * 2.0 + rng.normal(0, 0.2 * 13.0, self.src.shape)
        with caplog.at_level("WARNING", logger="focus.registration._footprint"):
            t, _ = fit_anchor_to_target(self.src, dst, self.spot)
        assert t.kind == "local_affine"
        rows = np.arange(len(self.src))
        np.testing.assert_allclose(t.to_target(self.src, rows), dst, atol=1e-9)
        np.testing.assert_allclose(t.to_anchor(dst, rows), self.src, atol=1e-9)
        assert caplog.records

    def test_too_few_points(self) -> None:
        """Verify too few points."""
        with pytest.raises(ValueError):
            fit_anchor_to_target(self.src[:2], self.src[:2], self.spot)
