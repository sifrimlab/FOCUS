"""Plan behavioral scenarios."""

from focus.spatial.footprint import AnchorGeometry
from focus.registration.readout import WindowPlan

import numpy as np

from focus.registration.readout import (
    LevelGeometry,
    TokenGrid,
    plan_windows,
    readout_membership,
)
from focus.registration.windows import resolve_bg_color, token_background_mask
from tests.encoding.data import lattice_geometry


class TestPlan:
    """Behavioral contracts for plan."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.grid = TokenGrid(patch=16, base_side=224)
        self.dims = (1200, 1500)
        self.level = LevelGeometry(0, self.dims, self.dims, None)

    def check_plan(
        self, spatial: np.ndarray, mapped: np.ndarray, geom: AnchorGeometry
    ) -> WindowPlan:
        """Build a window plan, verify unique spot coverage and image bounds, then return it."""
        plan = plan_windows(geom, mapped, self.level, self.grid)
        seen = np.concatenate([c.spots for c in plan.chunks] + [plan.unplaced])
        assert sorted(seen.tolist()) == list(range(len(spatial)))
        h, w = self.dims
        for c in plan.chunks:
            ox, oy = c.origin
            assert 0 <= ox and ox + c.shape[1] <= w and 0 <= oy and oy + c.shape[0] <= h
            boxes = plan.boxes[c.spots]
            interior = (
                (boxes[:, 0] > 0)
                & (boxes[:, 1] > 0)
                & (boxes[:, 2] < w)
                & (boxes[:, 3] < h)
            )
            touching = (
                (ox == 0) | (oy == 0) | (ox + c.shape[1] == w) | (oy + c.shape[0] == h)
            )
            if not touching:
                margin = np.minimum.reduce(
                    [
                        boxes[:, 0] - ox,
                        boxes[:, 1] - oy,
                        ox + c.shape[1] - boxes[:, 2],
                        oy + c.shape[0] - boxes[:, 3],
                    ]
                )
                assert np.all(margin[interior] >= self.grid.context - 1e-6)
        return plan

    def test_point_spots_share_windows(self) -> None:
        """Verify point spots share windows."""
        spatial, mapped, geom = lattice_geometry(
            40, 8.0, 8.0, 0.5, (20.0, 30.0)
        )  # Visium HD 8 µm bins
        plan = self.check_plan(spatial, mapped, geom)
        assert len(plan.chunks) < len(spatial) / 10

    def test_large_spots_get_large_windows(self) -> None:
        """Verify large spots get large windows."""
        self.dims = (1600, 1600)
        self.level = LevelGeometry(0, self.dims, self.dims, None)
        spatial, mapped, geom = lattice_geometry(
            3, 200.0, 200.0, 0.5, (250.0, 250.0)
        )  # 400 px footprints, all inside
        plan = self.check_plan(spatial, mapped, geom)
        assert all(c.shape[0] >= 400 + 2 * self.grid.context for c in plan.chunks)

    def test_tiny_spot_reads_the_token_holding_its_centre(self) -> None:
        """Verify tiny spot reads the token holding its centre."""
        spatial, mapped, geom = lattice_geometry(
            3, 40.0, 2.0, 0.5, (300.3, 200.7)
        )  # 4 px footprints
        plan = plan_windows(geom, mapped, self.level, self.grid)
        chunk = plan.chunks[0]
        n_tokens = (chunk.shape[0] // 16) * (chunk.shape[1] // 16)
        member, keep = readout_membership(
            chunk, plan, geom, self.level, self.grid, np.zeros(n_tokens, bool)
        )
        assert keep.all()
        nx = chunk.shape[1] // 16
        for r, spot in enumerate(chunk.spots):
            cx, cy = mapped[spot]
            expected = int((cy - chunk.origin[1]) // 16) * nx + int(
                (cx - chunk.origin[0]) // 16
            )
            assert np.flatnonzero(member[r]).tolist() == [expected]

    def test_token_background_order_matches_the_patch_grid(self) -> None:
        """Verify token background order matches the patch grid."""
        window = np.ones((1, 48, 64, 3), np.float32)
        window[0, 16:32, 32:48] = 0.3  # token row 1, column 2
        bg = token_background_mask(window, 16, resolve_bg_color("white"))
        assert np.flatnonzero(~bg[0]).tolist() == [1 * 4 + 2]
