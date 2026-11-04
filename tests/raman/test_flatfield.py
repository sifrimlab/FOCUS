"""Flat-field estimation and its check against the tile overlaps."""

import numpy as np
from scipy.ndimage import gaussian_filter

from focus.preprocessing.raman import flatfield


def _scene(seed: int, shape: tuple[int, int], sparse: bool = True) -> np.ndarray:
    rng = np.random.default_rng(seed)
    texture = gaussian_filter(rng.random(shape), 3) * 4
    if not sparse:
        return texture * 40 + 2
    return np.clip(texture - 1.2, 0, None) * 40 + 2


class TestFlatField:
    """A shared illumination profile is recovered and checked independently."""

    def test_recovers_a_known_profile(self) -> None:
        """Smooth vignetting is recovered from tiles of varying specimen."""
        h = w = 256
        yy, xx = np.mgrid[0:h, 0:w]
        truth = 1.0 - 0.8 * (((xx - 140) / w) ** 2 + ((yy - 110) / h) ** 2)
        truth /= truth.mean()
        rng = np.random.default_rng(0)
        tiles = np.stack(
            [
                rng.poisson(_scene(i, (h, w)) * truth).astype(np.float32)
                for i in range(60)
            ]
        )
        # 256 px of 2 µm: the tile spans 512 µm, as a 1024 px SRS tile of 0.57 µm does.
        estimate = flatfield.estimate(tiles, pixel_um=2.0)
        assert abs(estimate.mean() - 1.0) < 1e-5
        assert np.corrcoef(estimate.ravel(), truth.ravel())[0, 1] > 0.98

    def test_overlap_check_measures_the_edge_ratio(self) -> None:
        """Strips of the same scene seen at opposite tile edges give the illumination ratio."""
        h, w, ov = 220, 240, 40
        ramp = np.tile(np.linspace(0.7, 1.3, w), (h, 1)).astype(np.float32)
        scene = _scene(1, (h, 3 * w), sparse=False)
        tiles, positions = [], []
        for k in range(3):
            x = k * (w - ov)
            tiles.append(scene[:, x : x + w] * ramp)
            positions.append((x, 0))
        check = flatfield.overlap_check(
            np.stack(tiles),
            np.array(positions, float),
            [(0, 1), (1, 2)],
            ramp / ramp.mean(),
        )
        assert check.pairs[0] == 2
        assert abs(check.measured[0] - check.modelled[0]) < 0.02
        assert check.measured[0] > 1.3

    def test_channel_mean_ignores_saturated_samples(self) -> None:
        """A saturated channel does not enter the mean; a fully saturated pixel keeps 255."""
        tile = np.array([[[10, 255]], [[20, 255]], [[255, 255]]], dtype=np.uint8)
        np.testing.assert_allclose(flatfield.channel_mean(tile, 255), [[15.0, 255.0]])
