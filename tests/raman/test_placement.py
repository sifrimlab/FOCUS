"""Placement of acquisitions: rotation, scale and shift recovered from the tissue images."""

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter, map_coordinates

from focus.preprocessing.raman import placement
from focus.spatial.spectral import nan_block_mean

PIXEL_UM = 0.6
FACTORS = (2, 8)  # 1.2 µm and 4.5 µm views, as the workflow stores them


def _field(seed: int, size: int = 4200) -> np.ndarray:
    """A textured specimen on a canvas larger than every image (fine texture and large shapes)."""
    rng = np.random.default_rng(seed)
    fine = gaussian_filter(rng.normal(size=(size, size)), 3)
    coarse = gaussian_filter(rng.normal(size=(size, size)), 40)
    return np.asarray(fine / fine.std() + 2 * coarse / coarse.std(), dtype=np.float32)


def _image(field: np.ndarray, matrix: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Pixels ``(c, r)`` of an image whose placement on the canvas is ``matrix`` (3x3)."""
    rows, cols = shape
    rr, cc = np.mgrid[0:rows, 0:cols].astype(np.float64)
    pts = matrix @ np.stack([cc.ravel(), rr.ravel(), np.ones(rr.size)])
    return np.asarray(map_coordinates(field, [pts[1], pts[0]], order=1)).reshape(shape)


def _prepared(name: str, image: np.ndarray, origin_um: tuple[float, float]) -> Any:
    """The parts of a ``Prepared`` acquisition that placement reads."""
    return SimpleNamespace(
        acquisition=SimpleNamespace(name=name),
        pixel_um=(PIXEL_UM, PIXEL_UM),
        origin_um=np.asarray(origin_um, dtype=np.float64),
        shape=image.shape,
        views={f: nan_block_mean(image, f) for f in FACTORS},
    )


def _similarity(degrees: float, scale: float, shift: tuple[float, float]) -> np.ndarray:
    t = np.radians(degrees)
    return np.array(
        [
            [scale * np.cos(t), -scale * np.sin(t), shift[0]],
            [scale * np.sin(t), scale * np.cos(t), shift[1]],
            [0.0, 0.0, 1.0],
        ]
    )


@pytest.mark.parametrize("degrees", [0.7, -12.0])
def test_rotation_scale_and_shift_are_recovered(degrees: float) -> None:
    """A re-mounted, slightly rescaled acquisition with other contrast lands where it belongs."""
    field = _field(0)
    on_canvas = np.array([[1, 0, 900.0], [0, 1, 900.0], [0, 0, 1]])
    reference = _image(field, on_canvas, (1600, 2400))
    truth = _similarity(degrees, 1.003, (350.0, 260.0))  # other px -> reference px
    rng = np.random.default_rng(1)
    other = _image(field, on_canvas @ truth, (1400, 2000))
    other = 0.5 * np.tanh(other / 3) + 2 + rng.normal(0, 0.05, other.shape)
    ref = _prepared("ref", reference, (0.0, 0.0))
    moving = _prepared("other", other.astype(np.float32), (-3000.0, 4000.0))

    found = placement.place(ref, moving, [(ref, placement.reference_placement())])

    assert found.method == "image correlation" and found.blocks >= 8
    assert abs(found.rotation_deg - degrees) < 0.05
    assert abs(found.scale_ratio - 1.003) < 0.001
    rr, cc = np.mgrid[100:1300:200, 100:1900:200]
    pts = np.stack([cc.ravel(), rr.ravel(), np.ones(rr.size)])
    error = np.linalg.norm((found.homogeneous @ pts - truth @ pts)[:2], axis=0)
    assert error.max() * PIXEL_UM < 1.0
    assert found.residual_um is not None and found.residual_um < 1.0


def test_stage_offsets_inherit_the_rotation_of_their_sitting() -> None:
    """An offset to an image of the same stage frame is composed with that image's placement."""
    turned = placement.Placement(
        placement._flat(_similarity(-0.65, 1.0, (500.0, 700.0))), "x", 0.0, 1.0
    )
    sibling: Any = SimpleNamespace(pixel_um=(PIXEL_UM, PIXEL_UM), origin_um=np.zeros(2))
    other: Any = SimpleNamespace(
        pixel_um=(PIXEL_UM, PIXEL_UM), origin_um=np.array([60.0, -30.0])
    )
    m = placement._stage_relative(other, sibling, turned)
    expected = turned.homogeneous @ np.array(
        [[1, 0, 60.0 / PIXEL_UM], [0, 1, -30.0 / PIXEL_UM], [0, 0, 1]]
    )
    np.testing.assert_allclose(m, expected)


def test_annotation_round_trips_through_the_registration_reader() -> None:
    """Six numbers, read back as the same affine map; legacy shift-scale values still parse."""
    from focus.registration.pixel_readout import parse_placement

    found = placement.Placement(
        placement._flat(_similarity(-0.65, 0.9997, (519.48, 707.58))), "x", 0.0, 1.0
    )
    np.testing.assert_allclose(
        parse_placement(found.annotation()), found.matrix, rtol=1e-9
    )
    assert parse_placement("150,100,1,1") == (1.0, 0.0, 150.0, 0.0, 1.0, 100.0)
