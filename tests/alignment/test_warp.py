"""Landmark warp: fit, inverse, folds, exclusions, serialisation and browser parity.

Regenerate the browser parity fixture after a deliberate change to the warp maths with

    python -m tests.alignment.test_warp
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from focus.spatial.thin_plate import (
    ResidualMap,
    active_anchors,
    apply_homography,
    border_anchors,
    leave_one_out,
)
from focus.spatial.warp import LandmarkWarp

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "gui_src"
    / "alignment"
    / "tests"
    / "fixtures"
    / "warp_parity.json"
)

H = np.array([[0.9, 0.1, 30.0], [-0.08, 1.1, -12.0], [1e-5, -2e-5, 1.0]])


def _landmarks(
    n: int, noise: float = 15.0, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """``n`` landmark pairs placed by ``H`` and perturbed by a smooth-ish random residual."""
    rng = np.random.default_rng(seed)
    src = rng.uniform(0, 1000, (n, 2))
    dst = apply_homography(H, src) + rng.normal(0, noise, (n, 2))
    return src, dst


class TestResidual:
    """The residual model per landmark count."""

    def test_no_landmarks_is_the_matrix(self) -> None:
        """With no landmark the warp is exactly the homography."""
        pts = np.random.default_rng(0).uniform(0, 1000, (500, 2))
        w = LandmarkWarp(H)
        assert w.residual.kind == "identity"
        assert not w.is_nonrigid
        np.testing.assert_array_equal(w.to_fixed(pts), apply_homography(H, pts))

    @pytest.mark.parametrize("n, kind", [(1, "translation"), (2, "similarity")])
    def test_one_and_two_landmarks_are_exact_and_rigid(self, n: int, kind: str) -> None:
        """1 and 2 landmarks are hit exactly by a translation / similarity."""
        src, dst = _landmarks(n)
        w = LandmarkWarp(H, src, dst)
        assert w.residual.kind == kind
        assert not w.is_nonrigid
        np.testing.assert_allclose(w.to_fixed(src), dst, atol=1e-9)

    def test_collinear_landmarks_fall_back_to_similarity(self) -> None:
        """Three landmarks on a line do not determine a spline."""
        src = np.array([[0.0, 0.0], [100.0, 100.0], [300.0, 300.0]])
        w = LandmarkWarp(H, src, apply_homography(H, src) + [5.0, -3.0])
        assert w.residual.kind == "similarity"

    def test_unsmoothed_spline_interpolates(self) -> None:
        """With smoothing 0 the spline passes through every landmark."""
        src, dst = _landmarks(12)
        w = LandmarkWarp(H, src, dst, smoothing=0.0)
        assert w.is_nonrigid
        np.testing.assert_allclose(w.to_fixed(src), dst, atol=1e-8)

    def test_smoothing_trades_fit_for_regularity(self) -> None:
        """More smoothing leaves larger landmark residuals."""
        src, dst = _landmarks(12)
        errs = [
            np.abs(LandmarkWarp(H, src, dst, smoothing=s).to_fixed(src) - dst).max()
            for s in (0.0, 1e-2, 1.0)
        ]
        assert errs[0] < errs[1] < errs[2]

    def test_spline_reproduces_an_affine_residual(self) -> None:
        """An affine residual is fitted by the spline's affine part with zero bending."""
        src, _ = _landmarks(10)
        a = np.array([[1.05, 0.02], [-0.03, 0.97]])
        dst = apply_homography(H, src) @ a.T + [4.0, -7.0]
        w = LandmarkWarp(H, src, dst, smoothing=0.0)
        np.testing.assert_allclose(np.abs(w.residual.weights).max(), 0.0, atol=1e-9)
        pts = np.random.default_rng(3).uniform(0, 1000, (200, 2))
        np.testing.assert_allclose(
            w.to_fixed(pts), apply_homography(H, pts) @ a.T + [4.0, -7.0], atol=1e-8
        )

    def test_jacobian_matches_finite_differences(self) -> None:
        """The analytic Jacobian of the full warp matches central differences."""
        src, dst = _landmarks(12)
        w = LandmarkWarp(H, src, dst)
        p = np.random.default_rng(4).uniform(0, 1000, (20, 2))
        e = 1e-5
        num = np.stack(
            [
                (w.to_fixed(p + [e, 0]) - w.to_fixed(p - [e, 0])) / (2 * e),
                (w.to_fixed(p + [0, e]) - w.to_fixed(p - [0, e])) / (2 * e),
            ],
            axis=2,
        )
        np.testing.assert_allclose(w.jacobian(p), num, atol=1e-6)


class TestInverse:
    """The exact inverse used to compose guided alignments."""

    @pytest.mark.parametrize("n", [0, 1, 2, 12])
    def test_round_trip(self, n: int) -> None:
        """``from_fixed(to_fixed(x)) == x`` to well below a pixel."""
        src, dst = _landmarks(n)
        w = LandmarkWarp(H, src, dst)
        pts = np.random.default_rng(5).uniform(0, 1000, (5000, 2))
        back = w.from_fixed(w.to_fixed(pts))
        assert np.isfinite(back).all()
        np.testing.assert_allclose(back, pts, atol=1e-5)

    def test_full_resolution_round_trip(self) -> None:
        """``inverse_full`` undoes ``map_full`` including the fixed-frame scale."""
        src, dst = _landmarks(8)
        w = LandmarkWarp(H, src, dst, fixed_scale=np.array([4.0, 4.5]))
        pts = np.random.default_rng(6).uniform(0, 1000, (1000, 2))
        np.testing.assert_allclose(w.inverse_full(w.map_full(pts)), pts, atol=1e-5)

    def test_nan_in_nan_out(self) -> None:
        """Unplaced points stay unplaced."""
        w = LandmarkWarp(H, *_landmarks(6))
        out = w.from_fixed(np.array([[np.nan, 1.0], [100.0, 200.0]]))
        assert np.isnan(out[0]).all() and np.isfinite(out[1]).all()


class TestDamage:
    """Folds and exclusion polygons."""

    def test_crossed_landmarks_fold(self) -> None:
        """Two neighbouring landmarks swapped across each other fold the tissue between them."""
        src = np.array(
            [[0, 0], [1000, 0], [1000, 1000], [0, 1000], [450, 500], [550, 500]],
            dtype=float,
        )
        eye = np.eye(3)
        dst = src.copy()
        dst[4], dst[5] = src[5] + [60, 0], src[4] - [60, 0]
        w = LandmarkWarp(eye, src, dst, smoothing=0.0)
        assert w.folded(np.array([[500.0, 500.0]]))[0]
        assert not w.folded(np.array([[50.0, 50.0]]))[0]

    def test_smooth_warp_does_not_fold(self) -> None:
        """Ordinary landmark noise does not fold the tissue."""
        w = LandmarkWarp(H, *_landmarks(12, noise=5.0))
        grid = np.stack(
            np.meshgrid(np.linspace(0, 1000, 40), np.linspace(0, 1000, 40)), -1
        )
        assert not w.folded(grid.reshape(-1, 2)).any()

    def test_exclusions_map_to_nan(self) -> None:
        """Moving-layer and fixed-layer polygons both unplace the points they cover."""
        eye = np.eye(3)
        moving = [[[0, 0], [10, 0], [10, 10], [0, 10]]]
        fixed = [[[100, 100], [110, 100], [110, 110], [100, 110]]]
        w = LandmarkWarp.from_browser(
            eye.reshape(-1, order="F"),
            exclusions={"moving": moving, "fixed": fixed},
            fixed_scale=(2.0, 2.0),
        )
        pts = np.array([[5.0, 5.0], [105.0, 105.0], [50.0, 50.0]])
        out = w.map_full(pts)
        assert np.isnan(out[0]).all() and np.isnan(out[1]).all()
        np.testing.assert_allclose(out[2], [100.0, 100.0])
        back = w.inverse_full(np.array([[10.0, 10.0], [210.0, 210.0], [100.0, 100.0]]))
        assert np.isnan(back[0]).all() and np.isnan(back[1]).all()
        np.testing.assert_allclose(back[2], [50.0, 50.0])

    def test_degenerate_polygons_are_ignored(self) -> None:
        """Polygons with fewer than three vertices exclude nothing."""
        w = LandmarkWarp.from_browser(
            np.eye(3).reshape(-1), exclusions={"moving": [[[0, 0], [10, 10]]]}
        )
        assert not w.has_exclusions


class TestOutlineAnchors:
    """The moving layer's outline holds, so a landmark bends only the tissue around it."""

    OUTLINE = border_anchors([0.0, 0.0], [1000.0, 1000.0])

    def test_one_pin_bends_locally(self) -> None:
        """Dragging one pin moves its neighbourhood; the outline stays on the matrix."""
        src = np.array([[500.0, 500.0]])
        dst = apply_homography(H, src) + [40.0, -25.0]
        w = LandmarkWarp(H, src, dst, smoothing=0.0, anchors=self.OUTLINE)
        assert w.is_nonrigid
        np.testing.assert_allclose(w.to_fixed(src), dst, atol=1e-8)
        np.testing.assert_allclose(
            w.to_fixed(self.OUTLINE), apply_homography(H, self.OUTLINE), atol=1e-8
        )
        near = np.array([[520.0, 480.0]])
        far = np.array([[60.0, 940.0]])

        def shift(p: np.ndarray) -> float:
            """How far the warp moves a point off the matrix alone."""
            return float(np.linalg.norm(w.to_fixed(p) - apply_homography(H, p)))

        assert shift(near) > 30.0
        assert shift(far) < 10.0
        assert not w.folded(np.array([[250.0, 250.0], [750.0, 750.0]])).any()

    def test_a_pin_near_the_border_releases_the_anchor_there(self) -> None:
        """A landmark close to an outline anchor can move that part of the border."""
        src = np.array([[980.0, 30.0]])  # next to the top-right corner
        held = active_anchors(self.OUTLINE, src)
        assert len(held) == len(self.OUTLINE) - 1
        assert not any(np.allclose(a, [1000.0, 0.0]) for a in held)
        assert len(active_anchors(self.OUTLINE, np.zeros((0, 2)))) == 0

    def test_anchors_survive_serialisation_and_rescaling(self) -> None:
        """Stored warps keep their outline; rescaling the fixed frame leaves it unchanged."""
        src = np.array([[500.0, 500.0], [200.0, 700.0]])
        dst = apply_homography(H, src) + [[10.0, 5.0], [-6.0, 3.0]]
        w = LandmarkWarp.from_browser(
            H.reshape(-1, order="F"),
            {
                "src": src.tolist(),
                "dst": dst.tolist(),
                "anchors": self.OUTLINE.tolist(),
            },
            0.0,
            None,
            (2.0, 2.0),
        )
        again = LandmarkWarp.from_json(w.to_json())
        np.testing.assert_array_equal(again.anchors, self.OUTLINE)
        pts = np.random.default_rng(9).uniform(0, 1000, (50, 2))
        np.testing.assert_allclose(
            again.rescaled((4.0, 4.0)).map_full(pts), w.map_full(pts), rtol=1e-9
        )
        np.testing.assert_allclose(w.from_fixed(w.to_fixed(pts)), pts, atol=1e-5)


class TestQualityControl:
    """Leave-one-out residuals flag the inconsistent landmark."""

    def test_outlier_has_the_largest_error(self) -> None:
        """A landmark pinned to the wrong structure, among consistent ones, stands out."""
        g = np.linspace(0, 1000, 5)
        src = np.stack(np.meshgrid(g, g), -1).reshape(-1, 2)
        bend = 20.0 * np.sin(src / 400.0)  # smooth, section-like deformation
        dst = apply_homography(H, src) + bend
        centre = 12  # the middle of the 5x5 grid
        dst[centre] += [90.0, -70.0]
        loo = leave_one_out(H, src, dst, 1e-3)
        assert int(np.argmax(loo)) == centre


class TestSerialisation:
    """Persisted warps reload to the same map."""

    def test_json_round_trip(self) -> None:
        """``from_json(to_json())`` reproduces the warp and its exclusions."""
        src, dst = _landmarks(9)
        w = LandmarkWarp.from_browser(
            H.reshape(-1, order="F"),
            {"src": src.tolist(), "dst": dst.tolist()},
            0.05,
            {"moving": [[[0, 0], [5, 0], [5, 5]]], "fixed": []},
            (2.0, 3.0),
        )
        again = LandmarkWarp.from_json(w.to_json())
        pts = np.random.default_rng(7).uniform(0, 1000, (100, 2))
        np.testing.assert_array_equal(again.map_full(pts), w.map_full(pts))
        assert again.smoothing == 0.05
        assert len(again.moving_exclusions) == 1

    def test_browser_matrix_is_column_major(self) -> None:
        """The gl-matrix serialisation is read column-first."""
        w = LandmarkWarp.from_browser(H.reshape(-1, order="F"))
        np.testing.assert_array_equal(w.matrix, H)

    def test_mismatched_landmarks_are_rejected(self) -> None:
        """Source and destination lists must pair up."""
        with pytest.raises(ValueError):
            LandmarkWarp.from_browser(
                np.eye(3).reshape(-1), {"src": [[0, 0]], "dst": []}
            )


# --- Browser parity ---------------------------------------------------------------------------


_PARITY_SETUPS = [
    (0, 1e-3, False),
    (1, 1e-3, False),
    (2, 1e-3, False),
    (3, 0.0, False),
    (7, 1e-3, False),
    (15, 0.1, False),
    (1, 1e-3, True),
    (2, 0.0, True),
    (6, 1e-3, True),
]


def _parity_case(
    n: int, smoothing: float, anchored: bool, query: np.ndarray
) -> dict[str, object]:
    """One case: ``n`` landmarks, with or without the outline anchors of a 1000 x 1000 layer."""
    src, dst = _landmarks(n, seed=100 + n)
    anchors = (
        border_anchors([0.0, 0.0], [1000.0, 1000.0]) if anchored else np.zeros((0, 2))
    )
    w = LandmarkWarp(H, src, dst, smoothing=smoothing, anchors=anchors)
    resid = w.residual
    loo = leave_one_out(H, src, dst, smoothing, anchors)
    return {
        "name": f"{n} landmarks, smoothing {smoothing}"
        + (", outline anchors" if anchored else ""),
        "anchors": anchors.tolist(),
        "matrix_column_major": H.reshape(-1, order="F").tolist(),
        "landmarks": [{"src": s.tolist(), "dst": d.tolist()} for s, d in zip(src, dst)],
        "smoothing": smoothing,
        "query": query.tolist(),
        "kind": resid.kind,
        "mapped": w.to_fixed(query).tolist(),
        "jacobian_det": np.linalg.det(
            resid.jacobian(apply_homography(H, query))
        ).tolist(),
        "leave_one_out": [None if not np.isfinite(v) else float(v) for v in loo],
    }


def build_parity_cases() -> list[dict[str, object]]:
    """Deterministic cases evaluated by both implementations."""
    query = np.random.default_rng(42).uniform(-100, 1100, (25, 2))
    return [_parity_case(n, sm, anch, query) for n, sm, anch in _PARITY_SETUPS]


def test_browser_parity_fixture_is_current() -> None:
    """The fixture the browser tests read equals what this implementation computes."""
    stored = json.loads(FIXTURE.read_text())
    fresh = build_parity_cases()
    assert [c["name"] for c in stored] == [c["name"] for c in fresh]
    for s, f in zip(stored, fresh):
        assert s["kind"] == f["kind"]
        np.testing.assert_allclose(
            np.asarray(s["mapped"]), np.asarray(f["mapped"]), rtol=1e-9, atol=1e-7
        )
        np.testing.assert_allclose(
            np.asarray(s["jacobian_det"]),
            np.asarray(f["jacobian_det"]),
            rtol=1e-9,
            atol=1e-9,
        )


def test_residual_map_fit_is_scale_free() -> None:
    """Scaling all coordinates scales the fitted map identically (smoothing has no units)."""
    src, dst = _landmarks(10)
    p = apply_homography(H, src)
    a = ResidualMap.fit(p, dst, 1e-2)
    b = ResidualMap.fit(p * 7.0, dst * 7.0, 1e-2)
    q = np.random.default_rng(8).uniform(0, 1000, (50, 2))
    np.testing.assert_allclose(b(q * 7.0), a(q) * 7.0, rtol=1e-9)


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(build_parity_cases(), indent=1) + "\n")
    print(f"wrote {FIXTURE}")
