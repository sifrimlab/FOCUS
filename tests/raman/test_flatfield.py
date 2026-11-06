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


class TestDetectorOffset:
    """One illumination profile shades every channel above an unshaded detector offset."""

    @staticmethod
    def _tiles(offset: float, shading: float, channels: int = 6, seed: int = 0):  # type: ignore[no-untyped-def]
        """``(T, C, 64, 64)`` uint8-like tiles: offset + profile x signal, and the profile."""
        h = w = 64
        yy, xx = np.mgrid[0:h, 0:w] / h
        profile = 1.0 + shading * (xx - 0.5) - shading * ((yy - 0.5) ** 2)
        profile /= profile.mean()
        levels = np.linspace(0.5, 20.0, channels)
        rng = np.random.default_rng(seed)
        tiles = []
        for t in range(40):
            density = _scene(t, (h, w), sparse=False) / 40
            signal = levels[:, None, None] * density[None]
            tiles.append(rng.poisson(offset + profile * signal).astype(np.float32))
        return np.stack(tiles), profile

    def test_the_offset_is_recovered(self) -> None:
        """The amplitude of the shading is linear in the channel level above the offset."""
        tiles, _ = self._tiles(offset=7.0, shading=0.6)
        blocks = np.stack(
            [flatfield.channel_blocks(t, 255, 4) for t in tiles]
        )  # 16 x 16 blocks of 4 px
        fit = flatfield.detector_offset(blocks, block_um=4.0)
        assert abs(fit.counts - 7.0) < 0.5 and fit.r2 > 0.99
        assert fit.residual < 3 * fit.noise

    def test_the_corrected_channels_are_flat(self) -> None:
        """(raw - offset) / profile removes the shading from every channel, dim or bright."""
        tiles, truth = self._tiles(offset=7.0, shading=0.6)
        blocks = np.stack([flatfield.channel_blocks(t, 255, 4) for t in tiles])
        fit = flatfield.detector_offset(blocks, block_um=4.0)
        means = tiles.mean(axis=1)
        profile = flatfield.estimate(means - fit.counts, pixel_um=4.0)
        assert np.corrcoef(profile.ravel(), truth.ravel())[0, 1] > 0.98
        corrected = (tiles - fit.counts) / profile
        for c in range(tiles.shape[1]):
            plane = corrected[:, c].mean(axis=0)
            left, right = plane[:, :16].mean(), plane[:, -16:].mean()
            assert abs(right / left - 1) < 0.05

    def test_no_offset_without_shading_or_channels(self) -> None:
        """Flat illumination, or a single channel, cannot separate an offset: 0 is kept."""
        tiles, _ = self._tiles(offset=7.0, shading=0.0)
        blocks = np.stack([flatfield.channel_blocks(t, 255, 4) for t in tiles])
        assert flatfield.detector_offset(blocks, block_um=4.0).counts == 0.0
        single = blocks[:, :1]
        fit = flatfield.detector_offset(single, block_um=4.0)
        assert fit.counts == 0.0 and "channels" in fit.note
