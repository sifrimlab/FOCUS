"""Browser confirmations with landmarks: stored coordinates, persisted warps and registration."""

from __future__ import annotations

import os
import tempfile

import anndata
import numpy as np

from focus.alignment.result import parse_alignment_result
from focus.alignment.types import LandmarkPairs
from focus.alignment.warps import (
    WARPS_KEY,
    browser_state,
    confirmed_coordinates,
    read_stored_warp,
)
from focus.registration.anchor import read_anchor_arrays
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.thin_plate import apply_homography
from focus.spatial.warp import LandmarkWarp, stored_warp_is_nonrigid
from tests.registration.data import square_lattice

H = np.array([[1.2, 0.05, 40.0], [-0.04, 1.15, 25.0], [0.0, 0.0, 1.0]])
SCALE_YX = np.array([4.0, 3.0])  # display -> full resolution, [y, x]


def _landmarks() -> LandmarkPairs:
    """Pins over the lattice, each nudged so the residual is a smooth bend."""
    g = np.linspace(20, 380, 4)
    src = np.stack(np.meshgrid(g, g), -1).reshape(-1, 2)
    dst = apply_homography(H, src) + 6.0 * np.sin(src[:, ::-1] / 90.0)
    return LandmarkPairs(src=src.tolist(), dst=dst.tolist())


class TestConfirmedCoordinates:
    """What the alignment step stores for each kind of confirmation."""

    def setup_method(self) -> None:
        """A 40 x 40 spot lattice in native units."""
        self.spots = square_lattice(40, 10.0).astype(np.float32)
        self.matrix = H.reshape(-1, order="F").tolist()

    def test_matrix_only_is_unchanged(self) -> None:
        """Without landmarks the result equals the original projective path, bit for bit."""
        coords, warp = confirmed_coordinates(
            {"transform_matrix": self.matrix}, None, self.spots, SCALE_YX
        )
        legacy = parse_alignment_result(
            {"transform_matrix": self.matrix}, None, self.spots
        )
        assert legacy is not None and coords is not None and warp is not None
        legacy[:, 0] *= SCALE_YX[1]
        legacy[:, 1] *= SCALE_YX[0]
        np.testing.assert_array_equal(coords, legacy)
        assert warp.n_landmarks == 0

    def test_landmarks_bend_the_map_and_hit_the_pins(self) -> None:
        """Landmarks move spots off the homography; pinned positions land on their targets."""
        lm = _landmarks()
        coords, warp = confirmed_coordinates(
            {"transform_matrix": self.matrix, "landmarks": lm, "smoothing": 0.0},
            None,
            self.spots,
            SCALE_YX,
        )
        assert coords is not None and warp is not None and warp.is_nonrigid
        rigid = apply_homography(H, self.spots.astype(np.float64)) * [3.0, 4.0]
        assert np.abs(coords - rigid).max() > 1.0
        np.testing.assert_allclose(
            warp.map_full(np.array(lm["src"])),
            np.array(lm["dst"]) * [3.0, 4.0],
            atol=1e-6,
        )

    def test_exclusions_unplace_spots(self) -> None:
        """Spots under a moving-layer exclusion are stored as NaN."""
        hole = [[95.0, 95.0], [205.0, 95.0], [205.0, 205.0], [95.0, 205.0]]
        coords, _ = confirmed_coordinates(
            {"transform_matrix": self.matrix, "exclusions": {"moving": [hole]}},
            None,
            self.spots,
            SCALE_YX,
        )
        assert coords is not None
        inside = (
            (self.spots[:, 0] >= 95)
            & (self.spots[:, 0] <= 205)
            & (self.spots[:, 1] >= 95)
            & (self.spots[:, 1] <= 205)
        )
        assert np.isnan(coords[inside]).all()
        assert np.isfinite(coords[~inside]).all()


class TestPersistence:
    """The warp survives the aligned file and restarts the browser where it was left."""

    def test_stored_warp_round_trip_and_rescale(self) -> None:
        """A stored warp reloads, and is re-expressed for a fixed layer shown at a new level."""
        warp = LandmarkWarp.from_browser(
            H.reshape(-1, order="F"), _landmarks(), 0.0, None, (3.0, 4.0)
        )
        adata = anndata.AnnData(np.zeros((2, 1), dtype=np.float32))
        adata.uns[WARPS_KEY] = {"HE": warp.to_json()}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "aligned.h5ad")
            adata.write_h5ad(path)
            again = read_stored_warp(path, "HE")
            assert read_stored_warp(path, "MSI") is None
        assert again is not None
        pts = square_lattice(10, 37.0)
        np.testing.assert_array_equal(again.map_full(pts), warp.map_full(pts))
        # The browser now shows the fixed layer at half the previous display resolution.
        state = browser_state(again, np.array([8.0, 6.0]))
        assert state is not None
        moved = LandmarkWarp.from_browser(
            state["matrix"],  # type: ignore[arg-type]
            state["landmarks"],  # type: ignore[arg-type]
            0.0,
            None,
            (6.0, 8.0),
        )
        np.testing.assert_allclose(moved.map_full(pts), warp.map_full(pts), rtol=1e-9)


class TestRegistrationGeometry:
    """Registration consumes landmark-warped coordinates through the local affine map."""

    def test_local_affine_tracks_the_warp(self) -> None:
        """The per-spot affine reproduces the warp within a small fraction of a spot."""
        spots = square_lattice(40, 10.0)
        warp = LandmarkWarp.from_browser(
            H.reshape(-1, order="F"), _landmarks(), 0.0, None, (1.0, 1.0)
        )
        mapped = warp.map_full(spots).astype(np.float32)
        geom = AnchorGeometry.from_arrays(
            spots, mapped, np.array([10.0, 10.0]), nonrigid=True
        )
        assert geom.transform.kind == "local_affine"
        rows = np.arange(len(spots))
        offsets = np.array([2.0, -3.0])
        exact = warp.map_full(spots + offsets)
        approx = geom.transform.to_target(spots + offsets, rows)
        assert np.abs(approx - exact).max() < 0.01 * 10.0 * 1.2
        back = geom.transform.to_anchor(exact, rows)
        assert np.abs(back - (spots + offsets)).max() < 0.01 * 10.0

    def test_anchor_arrays_flag_nonrigid_warps(self) -> None:
        """``read_anchor_arrays`` reports whether the stored warp is non-rigid."""
        spots = square_lattice(5, 10.0).astype(np.float32)
        rigid = LandmarkWarp(H)
        bent = LandmarkWarp.from_browser(H.reshape(-1, order="F"), _landmarks(), 0.0)
        assert not stored_warp_is_nonrigid(rigid.to_json())
        assert stored_warp_is_nonrigid(bent.to_json())
        assert not stored_warp_is_nonrigid("not json")
        adata = anndata.AnnData(np.zeros((len(spots), 1), dtype=np.float32))
        adata.obsm["spatial"] = spots
        adata.obsm["HE_spatial"] = bent.map_full(spots).astype(np.float32)
        adata.obsm["MSI_spatial"] = spots
        adata.uns[WARPS_KEY] = {"HE": bent.to_json(), "MSI": rigid.to_json()}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "aligned.h5ad")
            adata.write_h5ad(path)
            he = read_anchor_arrays(path, "HE")
            msi = read_anchor_arrays(path, "MSI")
        assert he is not None and he.nonrigid
        assert msi is not None and not msi.nonrigid
