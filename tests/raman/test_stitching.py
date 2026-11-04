"""Tile registration and nearest-centre assembly."""

import numpy as np
from scipy.ndimage import gaussian_filter

from focus.preprocessing.raman import stitching


def _grid(
    seed: int, empty_left: bool = False
) -> tuple[list[np.ndarray], np.ndarray, np.ndarray, tuple[int, int]]:
    rng = np.random.default_rng(seed)
    h = w = 256
    step = 205
    truth = np.array(
        [
            (c * step + rng.integers(-4, 5) + r * 3, r * step + rng.integers(-4, 5))
            for r in range(3)
            for c in range(4)
        ]
    )
    truth -= truth.min(axis=0)
    scene = gaussian_filter(
        rng.random((truth[:, 1].max() + h, truth[:, 0].max() + w)), 2
    )
    if empty_left:
        scene[:, :300] = 0.0
    tiles = [
        scene[y : y + h, x : x + w] + rng.normal(0, 0.003, (h, w)) for x, y in truth
    ]
    stage = truth + rng.integers(-6, 7, truth.shape)
    return tiles, truth, stage.astype(float), (h, w)


class TestRegistration:
    """Measured overlaps correct the stage; blank overlaps keep it."""

    def test_recovers_true_positions(self) -> None:
        """Stage errors of several pixels are removed exactly."""
        tiles, truth, stage, shape = _grid(1)
        result = stitching.register(lambda i: tiles[i], stage, shape, pixel_um=0.5)
        assert result.accepted == len(result.pairs)
        relative = result.positions - result.positions[0]
        np.testing.assert_array_equal(relative, truth - truth[0])

    def test_blank_overlaps_fall_back_to_the_stage(self) -> None:
        """Overlaps without texture are rejected; textured tiles are still exact."""
        tiles, truth, stage, shape = _grid(2, empty_left=True)
        result = stitching.register(lambda i: tiles[i], stage, shape, pixel_um=0.5)
        assert 0 < result.accepted < len(result.pairs)
        textured = truth[:, 0] >= 300

        def rel(p: np.ndarray) -> np.ndarray:
            out: np.ndarray = p[textured] - p[textured].mean(axis=0)
            return out

        assert np.abs(rel(result.positions) - rel(truth)).max() <= 0.5


class TestAssembly:
    """Every covered pixel has exactly one source tile."""

    def test_ownership_partitions_the_mosaic(self) -> None:
        """Masks never overlap and cover every pixel some tile measures."""
        positions = np.array([[0, 0], [90, 3], [2, 95], [93, 97]])
        shape = (100, 100)
        masks = stitching.ownership(positions, shape)
        count = np.zeros(stitching.mosaic_shape(positions, shape), dtype=int)
        covered = np.zeros_like(count, dtype=bool)
        for (x, y), mask in zip(positions, masks):
            count[y : y + 100, x : x + 100] += mask
            covered[y : y + 100, x : x + 100] = True
        assert count.max() == 1
        np.testing.assert_array_equal(count == 1, covered)

    def test_uncovered_pixels_are_nan(self) -> None:
        """A gap between tiles stays NaN; measured values are copied unchanged."""
        positions = np.array([[0, 0], [120, 0]])
        masks = stitching.ownership(positions, (50, 100))
        mosaic = stitching.assemble_channel(
            lambda k: np.full((50, 100), k + 1.0, np.float32),
            positions,
            masks,
            (50, 100),
        )
        assert mosaic.shape == (50, 220)
        assert np.isnan(mosaic[:, 100:120]).all()
        assert (mosaic[:, :100] == 1).all() and (mosaic[:, 120:] == 2).all()
