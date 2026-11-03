"""Synthetic microscopy inputs shared by the preprocessing tests."""

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import cast

import numpy as np
import tifffile

from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.sample import MicroscopyImage

NAMES = ["DAPI", "CD3", "CD8", "PanCK"]


def settings(**changes: object) -> ImageSettings:
    """Defaults (auto, lossless, nothing removed) with ``changes`` applied."""
    values: dict[str, object] = dict(
        image_type="auto",
        z_projection="auto",
        crop_to_tissue=False,
        remove_background=False,
        pixel_size=None,
    )
    values.update(changes)
    return ImageSettings(**values)  # type: ignore[arg-type]


def fluorescence(channels: int = 4, shape: tuple[int, int] = (900, 1100)) -> np.ndarray:
    """``(C, Y, X)`` uint16 section on a dark background, 12-bit values."""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    tissue = ((yy - shape[0] / 2) / (shape[0] * 0.3)) ** 2 + (
        (xx - shape[1] / 2) / (shape[1] * 0.3)
    ) ** 2 < 1
    out = rng.integers(60, 100, (channels, *shape)).astype(np.uint16)
    for c in range(channels):
        out[c][tissue] += np.uint16(600 * (c + 1))
    return out


def brightfield(shape: tuple[int, int] = (900, 1100)) -> np.ndarray:
    """``(Y, X, 3)`` uint8 H&E-like section on glass."""
    rng = np.random.default_rng(1)
    image = np.full((*shape, 3), (238, 236, 240), np.uint8)
    image[200:700, 300:800] = (170, 90, 175)
    return np.clip(image.astype(int) + rng.integers(-6, 6, image.shape), 0, 255).astype(
        np.uint8
    )


def write(root: Path, name: str, data: np.ndarray, **kwargs: object) -> MicroscopyImage:
    """Write one sample file and return its MicroscopyImage."""
    folder = root / "s" / "img"
    folder.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(folder / name, data, ome=True, **kwargs)  # type: ignore[arg-type]
    return MicroscopyImage(str(root), "s", "img")


def ome_root(path: str) -> ET.Element:
    """Parsed OME-XML of an output."""
    with tifffile.TiffFile(path) as tif:
        return ET.fromstring(cast(tifffile.TiffPage, tif.pages[0]).description)


def tags(root: ET.Element, name: str) -> list[ET.Element]:
    """Every element named ``name`` (namespace ignored)."""
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == name]
