"""Contrast statistics and tissue detection on synthetic brightfield, fluorescence and TMA slides."""

import numpy as np
import pytest

from focus.preprocessing.microscopy import color
from focus.preprocessing.microscopy.tissue import TissueMask

_SIZE = 1500
_TISSUE = (slice(500, 1000), slice(500, 1000))
_BACKGROUND = (slice(1200, None), slice(1200, None))


def detect(image: np.ndarray) -> np.ndarray:
    """Tissue mask of `image` with the default coverage and clip settings."""
    return TissueMask(None)._detect_tissue_mask(image.copy())  # type: ignore[arg-type]


def enhanced(image: np.ndarray) -> np.ndarray:
    """Default colour enhancement (gamma 0.45, 0.35% saturation, subsampled statistics)."""
    return color.enhance_contrast(
        color.gamma_correction(image.copy()), 0.35, max_stat_pixels=9_000_000
    )


def brightfield(seed: int = 0) -> np.ndarray:
    """H&E-like section on a white slide."""
    rng = np.random.default_rng(seed)
    image = np.full((_SIZE, _SIZE, 3), 0.93, np.float32)
    image[_TISSUE] = [0.75, 0.45, 0.65]
    return image + rng.random(image.shape, dtype=np.float32) * 0.02


def fluorescence(top: float, channels: int = 3) -> np.ndarray:
    """Section bright on a dark background, scaled to `top` (a low-range scan when small)."""
    rng = np.random.default_rng(1)
    image = rng.random((_SIZE, _SIZE, channels), dtype=np.float32) * 0.02 * top
    image[_TISSUE] = (np.array([0.8, 0.5, 0.3])[:channels] * top).astype(np.float32)
    return image


@pytest.mark.parametrize("shape", [(4000, 4500, 3), (3000, 3000, 3)])
def test_subsampled_percentiles_cover_every_channel(
    shape: tuple[int, int, int],
) -> None:
    """A stride sharing a factor with the channel count (6 and 3 here) must not sample one channel."""
    rng = np.random.default_rng(3)
    image = np.full(shape, 0.95, np.float32)
    image[1000:2500, 1000:2500] = [0.80, 0.45, 0.70]
    image += rng.random(shape, dtype=np.float32) * 0.02
    low, high = np.percentile(image[image > 0], (0.35, 99.65))
    exact = np.clip((image - low) / (high - low), 0, 1)
    fast = color.enhance_contrast(image.copy(), 0.35, max_stat_pixels=9_000_000)
    np.testing.assert_allclose(fast, exact, atol=1e-3)


@pytest.mark.parametrize("prepare", [lambda x: x, enhanced])
def test_brightfield_section(prepare) -> None:  # type: ignore[no-untyped-def]
    """The section is tissue and the slide is background, raw or enhanced."""
    mask = detect(prepare(brightfield()))
    assert mask[_TISSUE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


def test_pigment_inside_tissue_is_kept() -> None:
    """Near-black pigment enclosed by tissue is not padding."""
    image = brightfield()
    image[700:800, 700:800] = 0.06
    assert detect(image)[700:800, 700:800].mean() == 1.0


@pytest.mark.parametrize("prepare", [lambda x: x, enhanced])
def test_near_black_padding_is_background(prepare) -> None:  # type: ignore[no-untyped-def]
    """Unscanned padding with compression noise (8-bit 1 to 5) stays background."""
    image = brightfield()
    noise = np.random.default_rng(2).integers(1, 6, (_SIZE, 150, 1)) / 255
    image[:, :150] = noise.astype(np.float32)
    mask = detect(prepare(image))
    assert mask[_TISSUE].mean() == 1.0 and mask[:, :150].mean() == 0.0


@pytest.mark.parametrize(
    ("top", "prepare"),
    [
        (0.9, lambda x: x),
        (0.9, enhanced),
        (4000 / 65535, lambda x: x),
        (4000 / 65535, enhanced),
    ],
)
def test_three_channel_fluorescence(top: float, prepare) -> None:  # type: ignore[no-untyped-def]
    """3-channel fluorescence, full range or 12-bit in a 16-bit container, is not inverted."""
    mask = detect(prepare(fluorescence(top)))
    assert mask[_TISSUE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


def test_single_channel_low_range_fluorescence() -> None:
    """A promoted single-channel scan keeps the dark-background branch."""
    mask = detect(fluorescence(0.05, channels=1))
    assert mask[_TISSUE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


def test_tma_cores_are_kept() -> None:
    """Cores far below min_object_coverage of the slide survive the area filter."""
    image = np.full((3000, 3000, 3), 0.93, np.float32)
    yy, xx = np.mgrid[:3000, :3000]
    for cy in range(300, 3000, 600):
        for cx in range(300, 3000, 600):
            image[(yy - cy) ** 2 + (xx - cx) ** 2 < 45**2] = [0.7, 0.4, 0.6]
    mask = detect(image)
    assert mask[(yy - 300) ** 2 + (xx - 300) ** 2 < 40**2].mean() == 1.0
    assert mask[:200, :200].mean() == 0.0


def test_debris_below_coverage_is_dropped() -> None:
    """Next to a large section, specks below min_object_coverage are still removed."""
    image = brightfield()
    image[100:140, 100:140] = [0.75, 0.45, 0.65]
    assert detect(image)[100:140, 100:140].mean() == 0.0
