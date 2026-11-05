"""Lattice and footprint-token planning scenarios (pure NumPy)."""

import numpy as np
import pytest

from focus.registration.readout import (
    LevelGeometry,
    TokenGrid,
    axis_lattice,
    build_lattice,
    footprint_tokens,
    patch_anchor_xy,
    patch_links,
)
from focus.registration.windows import (
    resolve_bg_color,
    token_background_grid,
    token_background_mask,
)
from tests.encoding.data import lattice_geometry

GRID = TokenGrid(patch=16, base_side=224)
P = 16


class TestLattice:
    """Behavioral contracts for the window lattice."""

    def test_stride_is_half_a_window_in_whole_tokens(self) -> None:
        """The stride is half the window, rounded down to whole tokens, and at least one token."""
        assert GRID.stride == 112
        assert TokenGrid(patch=14, base_side=518).stride == 252
        assert TokenGrid(patch=16, base_side=16).stride == 16

    @pytest.mark.parametrize("dim", [224, 300, 1000, 1007, 1500])
    def test_windows_cover_every_whole_token_once_with_context(self, dim: int) -> None:
        """Every whole token is read by one window that contains it, well inside it away from borders."""
        ax = axis_lattice(dim, GRID)
        assert ax.side == 224 and ax.n_tokens == dim // P
        assert np.all(ax.origins % P == 0)
        assert ax.origins[0] == 0 and ax.origins[-1] + ax.side == ax.n_tokens * P
        steps = np.diff(ax.origins)
        assert np.all((steps > 0) & (steps <= GRID.stride))
        centre = (np.arange(ax.n_tokens) + 0.5) * P
        lo = ax.origins[ax.owner]
        margin = np.minimum(centre - lo, lo + ax.side - centre)
        assert np.all(margin > 0)
        interior = (centre >= GRID.base_side / 4) & (
            centre <= ax.n_tokens * P - GRID.base_side / 4
        )
        assert np.all(margin[interior] >= GRID.base_side / 4)

    def test_tokens_halfway_between_two_windows_go_to_the_lower_one(self) -> None:
        """A stride of 7 tokens leaves tokens exactly between two window centres."""
        ax = axis_lattice(1500, GRID)
        # Window centres at 112 and 224 px; token 10 is centred at 168 px, halfway.
        assert ax.owner[10] == 0 and ax.owner[11] == 1

    def test_short_axes_use_one_window_or_none(self) -> None:
        """An axis shorter than the window gets one window of its whole tokens; under a token, none."""
        ax = axis_lattice(100, GRID)
        assert (ax.origins.tolist(), ax.side, ax.n_tokens) == ([0], 96, 6)
        assert np.all(ax.owner == 0)
        empty = axis_lattice(10, GRID)
        assert (len(empty.origins), empty.side, empty.n_tokens) == (0, 0, 0)

    def test_token_window_indexes_the_token_inside_its_window(self) -> None:
        """A token's window-local index points back at the same global token."""
        lattice = build_lattice((1000, 1500), GRID)
        rng = np.random.default_rng(0)
        rows = rng.integers(0, lattice.token_shape[0], 200)
        cols = rng.integers(0, lattice.token_shape[1], 200)
        win, local = lattice.token_window(rows, cols)
        n_cols = lattice.window_shape[1] // P
        origin = lattice.origin(win)
        np.testing.assert_array_equal(origin[:, 0] // P + local % n_cols, cols)
        np.testing.assert_array_equal(origin[:, 1] // P + local // n_cols, rows)


class TestFootprintTokens:
    """Behavioral contracts for footprint token weights."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.dims = (1000, 1500)
        self.level = LevelGeometry(0, self.dims, self.dims, None)
        self.lattice = build_lattice(self.dims, GRID)
        self.no_bg = np.zeros(self.lattice.token_shape, dtype=bool)

    def tokens(self, mapped: np.ndarray, geom, bg=None):  # type: ignore[no-untyped-def]
        """Footprint tokens of a geometry on the fixture image."""
        return footprint_tokens(
            geom, mapped, self.level, self.lattice, self.no_bg if bg is None else bg
        )

    def test_a_footprint_equal_to_one_token_reads_only_it(self) -> None:
        """A 16 px footprint centred on a token reads that token with weight 1."""
        spatial, mapped, geom = lattice_geometry(4, 48.0, 16.0, 1.0, (200.0, 200.0))
        t = self.tokens(mapped, geom)
        assert np.bincount(t.spot).tolist() == [1] * len(spatial)
        np.testing.assert_allclose(t.weight, 1.0)
        np.testing.assert_allclose(t.total, 1.0)
        win, local = self.lattice.token_window(
            np.floor(mapped[t.spot, 1] / P), np.floor(mapped[t.spot, 0] / P)
        )
        np.testing.assert_array_equal(t.window, win)
        np.testing.assert_array_equal(t.local, local)

    def test_a_half_token_shift_splits_the_weight(self) -> None:
        """A 16 px footprint centred on a token corner reads four tokens with weight 1/4."""
        spatial, mapped, geom = lattice_geometry(4, 48.0, 16.0, 1.0, (208.0, 208.0))
        t = self.tokens(mapped, geom)
        assert np.bincount(t.spot).tolist() == [4] * len(spatial)
        np.testing.assert_allclose(t.weight, 0.25)
        np.testing.assert_allclose(t.total, 1.0)

    def test_a_tiny_footprint_reads_the_token_holding_its_centre(self) -> None:
        """A footprint that covers no sample point falls back to its centre token."""
        spatial, mapped, geom = lattice_geometry(3, 40.0, 2.0, 1.0, (300.3, 200.7))
        t = self.tokens(mapped, geom)
        assert np.bincount(t.spot).tolist() == [1] * len(spatial)
        np.testing.assert_allclose(t.weight, 1.0)
        win, local = self.lattice.token_window(
            np.floor(mapped[t.spot, 1] / P), np.floor(mapped[t.spot, 0] / P)
        )
        np.testing.assert_array_equal(t.window, win)
        np.testing.assert_array_equal(t.local, local)

    def test_background_and_outside_spots_read_nothing(self) -> None:
        """Background tokens are dropped; spots outside the image read nothing."""
        spatial, mapped, geom = lattice_geometry(3, 48.0, 16.0, 1.0, (-56.0, 200.0))
        outside = mapped[:, 0] < 0  # the first column of spots lies left of the image
        bg = self.no_bg.copy()
        target = int(np.flatnonzero(~outside)[0])
        bg[int(mapped[target, 1] // P), int(mapped[target, 0] // P)] = True
        t = self.tokens(mapped, geom, bg)
        assert np.all(t.total[outside] == 0) and t.total[target] == 0
        assert not np.isin(t.spot, np.flatnonzero(outside)).any()
        assert target not in t.spot
        rest = ~outside
        rest[target] = False
        np.testing.assert_allclose(t.total[rest], 1.0)

    def test_large_footprints_span_several_windows(self) -> None:
        """400 px footprints read all their tokens, from more than one window."""
        dims = (1600, 1600)
        level = LevelGeometry(0, dims, dims, None)
        lattice = build_lattice(dims, GRID)
        spatial, mapped, geom = lattice_geometry(2, 600.0, 400.0, 1.0, (304.0, 304.0))
        t = footprint_tokens(
            geom, mapped, level, lattice, np.zeros(lattice.token_shape, dtype=bool)
        )
        for spot in range(len(spatial)):
            assert len(np.unique(t.window[t.spot == spot])) > 1
        np.testing.assert_allclose(t.total, (400 / P) ** 2)
        assert np.all(np.diff(t.window) >= 0)

    def test_patches_link_the_window_reading_the_centre_token(self) -> None:
        """Live spots link the window that reads their centre token; others link nothing."""
        spatial, mapped, geom = lattice_geometry(6, 30.0, 16.0, 1.0, (100.0, 150.0))
        live = np.ones(len(spatial), dtype=bool)
        live[3] = False
        patch = patch_links(mapped, self.level, self.lattice, live)
        assert patch[3] == -1
        expected, _ = self.lattice.token_window(
            np.floor(mapped[:, 1] / P), np.floor(mapped[:, 0] / P)
        )
        np.testing.assert_array_equal(patch[live], expected[live])

    def test_patch_centres_map_back_to_the_anchor_frame(self) -> None:
        """Patch centres are window centres mapped back through the alignment."""
        offset = np.array([100.0, 150.0])
        spatial, mapped, geom = lattice_geometry(6, 30.0, 16.0, 0.5, offset)
        live = np.ones(len(spatial), dtype=bool)
        patch = patch_links(mapped, self.level, self.lattice, live)
        xy = patch_anchor_xy(geom, self.lattice, self.level, patch, mapped)
        expected = (self.lattice.centre(patch) - offset) * 0.5
        np.testing.assert_allclose(xy, expected, atol=1e-6)
        patch[0] = -1
        xy = patch_anchor_xy(geom, self.lattice, self.level, patch, mapped)
        assert np.isnan(xy[0]).all() and np.isfinite(xy[1:]).all()


def test_token_background_order_matches_the_patch_grid() -> None:
    """Verify token background order matches the patch grid."""
    window = np.ones((1, 48, 64, 3), np.float32)
    window[0, 16:32, 32:48] = 0.3  # token row 1, column 2
    bg = token_background_mask(window, 16, resolve_bg_color("white"))
    assert np.flatnonzero(~bg[0]).tolist() == [1 * 4 + 2]


def test_token_background_grid_matches_per_window_masks() -> None:
    """The whole-image grid, computed in strips, equals the mask of each window cut from it."""
    rng = np.random.default_rng(1)
    img = np.ones((200, 330, 3), np.float32)
    img[40:150, 60:250] = rng.random((110, 190, 3), dtype=np.float32)
    white = resolve_bg_color("white")
    grid = token_background_grid(img, 16, white, strip_tokens=3)
    assert grid.shape == (12, 20)
    whole = token_background_mask(img[None, :192, :320], 16, white).reshape(12, 20)
    np.testing.assert_array_equal(grid, whole)
