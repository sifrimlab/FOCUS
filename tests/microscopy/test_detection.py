"""Tissue detection on synthetic brightfield, fluorescence and TMA canvases (native sample values)."""

import numpy as np
import pytest

from focus.preprocessing.microscopy.tissue import detect_tissue

_SIZE = 1500
_TISSUE = (slice(500, 1000), slice(500, 1000))
# The median blur rounds the square's corners by a few canvas pixels; real tissue has none.
_INSIDE = (slice(510, 990), slice(510, 990))
_BACKGROUND = (slice(1200, None), slice(1200, None))
# Canvas pixel size of the detection resolution.
_UM = 8.0


def brightfield(seed: int = 0) -> np.ndarray:
    """H&E-like section on a white slide, 8-bit values."""
    rng = np.random.default_rng(seed)
    image = np.full((_SIZE, _SIZE, 3), 237.0, np.float32)
    image[_TISSUE] = [190, 115, 165]
    return image + rng.random(image.shape, dtype=np.float32) * 5


def fluorescence(top: float, channels: int = 3) -> np.ndarray:
    """Section bright on a dark background with peak ``top`` (16-bit values)."""
    rng = np.random.default_rng(1)
    image = rng.random((_SIZE, _SIZE, channels), dtype=np.float32) * 0.02 * top
    image[_TISSUE] = (np.array([0.8, 0.5, 0.3])[:channels] * top).astype(np.float32)
    return image


def test_brightfield_section() -> None:
    """The stained section is tissue and the glass is background (HSV saturation, Otsu)."""
    mask = detect_tissue(brightfield(), np.dtype(np.uint8), True, _UM)
    assert mask[_INSIDE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


def test_pigment_inside_tissue_is_kept() -> None:
    """Near-black pigment enclosed by tissue is kept by hole filling."""
    image = brightfield()
    image[700:800, 700:800] = 15
    assert (
        detect_tissue(image, np.dtype(np.uint8), True, _UM)[700:800, 700:800].mean()
        == 1.0
    )


def test_near_black_padding_is_background() -> None:
    """Unscanned padding with compression noise (8-bit 1 to 5) has a high saturation but stays background."""
    image = brightfield()
    image[:, :150] = np.random.default_rng(2).integers(1, 6, (_SIZE, 150, 3))
    mask = detect_tissue(image, np.dtype(np.uint8), True, _UM)
    assert mask[_INSIDE].mean() == 1.0 and mask[:, :150].mean() == 0.0


def test_single_channel_brightfield() -> None:
    """A grayscale brightfield scan uses inverted intensity."""
    image = brightfield()[..., :1].copy()
    image[_TISSUE] = 120
    mask = detect_tissue(image, np.dtype(np.uint8), True, _UM)
    assert mask[_INSIDE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


@pytest.mark.parametrize("top", [60000.0, 4000.0])
def test_three_channel_fluorescence(top: float) -> None:
    """Fluorescence, full range or 12-bit in a 16-bit container, thresholds the channel maximum."""
    mask = detect_tissue(fluorescence(top), np.dtype(np.uint16), False, _UM)
    assert mask[_INSIDE].mean() == 1.0 and mask[_BACKGROUND].mean() == 0.0


def test_dim_channel_counts_as_much_as_a_bright_one() -> None:
    """Each channel is scaled to its own percentile: tissue seen in a dim channel only is kept."""
    rng = np.random.default_rng(4)
    image = np.zeros((_SIZE, _SIZE, 2), np.float32)
    image[..., 0] = rng.random((_SIZE, _SIZE)) * 200
    image[..., 1] = rng.random((_SIZE, _SIZE)) * 20
    image[500:1000, 500:1000, 0] = 40000  # bright stain
    image[200:400, 200:400, 1] = 400  # dim stain, absent from the bright channel
    mask = detect_tissue(image, np.dtype(np.uint16), False, _UM)
    assert mask[210:390, 210:390].mean() == 1.0 and mask[_INSIDE].mean() == 1.0


def test_tma_cores_are_kept() -> None:
    """Small cores far below the debris area of a slide survive through the largest-piece cap."""
    image = np.full((3000, 3000, 3), 237.0, np.float32)
    yy, xx = np.mgrid[:3000, :3000]
    for cy in range(300, 3000, 600):
        for cx in range(300, 3000, 600):
            image[(yy - cy) ** 2 + (xx - cx) ** 2 < 20**2] = [180, 100, 155]
    mask = detect_tissue(image, np.dtype(np.uint8), True, _UM)
    assert mask[(yy - 300) ** 2 + (xx - 300) ** 2 < 15**2].mean() == 1.0
    assert mask[:200, :200].mean() == 0.0


def test_debris_below_the_area_threshold_is_dropped() -> None:
    """Next to a large section, a 0.026 mm^2 speck (below 0.05 mm^2) is removed."""
    image = brightfield()
    image[100:120, 100:120] = [190, 115, 165]
    assert (
        detect_tissue(image, np.dtype(np.uint8), True, _UM)[100:120, 100:120].mean()
        == 0.0
    )


def test_without_pixel_size_the_threshold_is_a_canvas_fraction() -> None:
    """Without a pixel size, pieces below 1% of the canvas are debris (capped by the largest piece)."""
    image = brightfield()
    image[100:120, 100:120] = [190, 115, 165]
    mask = detect_tissue(image, np.dtype(np.uint8), True, None)
    assert mask[_INSIDE].mean() == 1.0 and mask[100:120, 100:120].mean() == 0.0


def test_an_empty_image_is_kept_whole() -> None:
    """Without any tissue signal the whole image is tissue, so nothing is cropped away."""
    image = np.full((400, 400, 3), 237.0, np.float32)
    assert detect_tissue(image, np.dtype(np.uint8), True, _UM).all()
