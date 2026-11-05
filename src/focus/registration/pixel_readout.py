"""Pixel access shared by the image-target registrations: images, placements, crops and tiles.

A preprocessed image file holds one or several OME images. A Raman ``raman-2`` file holds one image
per acquisition, each with its channel names (wavenumbers) and a ``focus_placement``
``"x0,y0,sx,sy"`` that maps it into image 0's level-0 pixels. A microscopy file holds one image at
the identity placement. Anchor spots are located in image 0's level-0 pixels by the alignment.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
from typing import cast

import numpy as np
import tifffile
import zarr

from focus.io.ome import hw_from_axes, read_image_annotations
from focus.registration.anchor import AnchorArrays
from focus.spatial.footprint import AnchorGeometry

IDENTITY_PLACEMENT = (0.0, 0.0, 1.0, 1.0)


@dataclass(frozen=True)
class RamanTarget:
    """One OME image of an image file and its placement in image 0's level-0 pixels."""

    series: int
    name: str
    channels: list[str]
    wavenumbers: list[float] | None
    placement: tuple[float, float, float, float]


def raman_targets(filename: str) -> list[RamanTarget]:
    """Every image of a preprocessed OME-TIFF with its placement.

    Older single-image files (stitched by ASHLAR) and microscopy files yield one target at the
    identity placement, its channels named ``Channel_N`` when the file carries no names.
    """
    images = read_image_annotations(filename)
    targets = []
    for index, image in enumerate(images):
        values = cast(dict[str, str], image["annotations"])
        placement = IDENTITY_PLACEMENT
        if "focus_placement" in values:
            x0, y0, sx, sy = (float(v) for v in values["focus_placement"].split(","))
            placement = (x0, y0, sx, sy)
        wavenumbers = (
            [float(v) for v in json.loads(values["focus_wavenumbers_cm1"])]
            if "focus_wavenumbers_cm1" in values
            else None
        )
        targets.append(
            RamanTarget(
                series=index,
                name=str(values.get("focus_acquisition", image["name"])),
                channels=[str(c) for c in cast(list[str], image["channels"])],
                wavenumbers=wavenumbers,
                placement=placement,
            )
        )
    return targets


def image_dims(filename: str, series: int) -> tuple[int, int]:
    """``(H, W)`` of one image's level 0 without loading pixel data."""
    with tifffile.TiffFile(filename) as tif:
        level = tif.series[series].levels[0]
        return hw_from_axes(level.shape, getattr(level, "axes", ""))


def channel_count(filename: str, series: int) -> int:
    """Number of channels (``C`` or ``S`` axis) of one image's level 0."""
    with tifffile.TiffFile(filename) as tif:
        level = tif.series[series].levels[0]
        axes = str(getattr(level, "axes", "")).upper()
        return next((int(n) for a, n in zip(axes, level.shape) if a in "CS"), 1)


def load_crop(
    filename: str, series: int, crop: tuple[int, int, int, int]
) -> np.ndarray:
    """``(C, r1 - r0, c1 - c0)`` float32 level-0 pixels of one image (only the crop is read)."""
    c0, r0, c1, r1 = crop
    with tifffile.TiffFile(filename) as tif:
        level = tif.series[series].levels[0]
        axes = str(getattr(level, "axes", "")).upper()
        # The level-0 array, not the multiscale group of a pyramid series.
        store = zarr.open(tif.series[series].aszarr(level=0), mode="r")
        index: list[object] = []
        for axis in axes:
            if axis == "Y":
                index.append(slice(r0, r1))
            elif axis == "X":
                index.append(slice(c0, c1))
            elif axis in "CS":
                index.append(slice(None))
            else:
                index.append(0)
        data = np.asarray(store[tuple(index)], dtype=np.float32)  # type: ignore[index]
    kept = [a for a in axes if a in "CSYX"]
    if "C" not in kept and "S" not in kept:
        return data[np.newaxis]
    colour = kept.index("C") if "C" in kept else kept.index("S")
    return np.moveaxis(data, colour, 0)


def image_geometry(
    anchor: AnchorArrays, geom: AnchorGeometry, target: RamanTarget
) -> AnchorGeometry:
    """Anchor footprints in one image's pixels (image 0's geometry composed with the placement)."""
    if target.series == 0 and target.placement == IDENTITY_PLACEMENT:
        return geom
    x0, y0, sx, sy = target.placement
    mapped = (np.asarray(anchor.mapped, dtype=np.float64) - [x0, y0]) / [sx, sy]
    return AnchorGeometry.from_arrays(anchor.spatial, mapped, anchor.spot_size)


def footprint_crop(
    geom: AnchorGeometry, height: int, width: int
) -> tuple[int, int, int, int] | None:
    """Half-open ``(c0, r0, c1, r1)`` union of the footprints' pixel boxes; None when none reach."""
    boxes = geom.pixel_bboxes(height, width)
    reach = boxes[(boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])]
    if len(reach) == 0:
        return None
    return (
        int(reach[:, 0].min()),
        int(reach[:, 1].min()),
        int(reach[:, 2].max()),
        int(reach[:, 3].max()),
    )


def tiles(
    crop: tuple[int, int, int, int], size: int, align: int = 1
) -> Iterator[tuple[int, int, int, int]]:
    """Half-open tiles of ``crop``, row-major; tile corners on multiples of ``align`` pixels."""
    c0, r0, c1, r1 = crop
    size = max(align, size - size % align)
    start_c, start_r = c0 - c0 % align, r0 - r0 % align
    for top in range(start_r, r1, size):
        for left in range(start_c, c1, size):
            yield (
                max(left, c0),
                max(top, r0),
                min(left + size, c1),
                min(top + size, r1),
            )
