"""Pixel access shared by the image-target registrations: images, placements, crops and tiles.

A preprocessed image file holds one or several OME images. A Raman file holds one image per
acquisition, each with its channel names (wavenumbers) and a ``focus_placement`` that maps it into
image 0's level-0 pixels: ``"a,b,tx,c,d,ty"`` (``raman-3``, an affine map: image pixel
``(col, row)`` lies at image-0 pixel ``(a col + b row + tx, c col + d row + ty)``), or
``"x0,y0,sx,sy"`` in older files (shift and scale only). A microscopy file holds one image at the
identity placement. Anchor spots are located in image 0's level-0 pixels by the alignment.

``raman-3`` images store linear intensities; ``focus_spot_offset`` and
``focus_spot_normalization`` say how averaged spectra (spot means, bins) are to be processed. Older
files stored processed pixels, so nothing is left to apply.
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
from focus.spatial import spectral
from focus.spatial.footprint import AnchorGeometry

Placement = tuple[float, float, float, float, float, float]
# ``(a, b, tx, c, d, ty)``: image pixel (col, row) -> image-0 pixel (a col + b row + tx, ...).
IDENTITY_PLACEMENT: Placement = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


def parse_placement(text: str) -> Placement:
    """The affine placement of a ``focus_placement`` value (6 numbers, or legacy 4)."""
    values = [float(v) for v in text.split(",")]
    if len(values) == 6:
        a, b, tx, c, d, ty = values
        return (a, b, tx, c, d, ty)
    if len(values) == 4:
        x0, y0, sx, sy = values
        return (sx, 0.0, x0, 0.0, sy, y0)
    raise ValueError(f"focus_placement must have 4 or 6 values, got {text!r}")


def placement_matrix(placement: Placement) -> np.ndarray:
    """``(2, 3)`` affine matrix of a placement."""
    return np.asarray(placement, dtype=np.float64).reshape(2, 3)


def placement_scale(placement: Placement) -> float:
    """Linear scale of a placement (image-0 pixels per image pixel)."""
    m = placement_matrix(placement)
    return float(np.sqrt(abs(np.linalg.det(m[:, :2]))))


def format_placement(placement: Placement) -> str:
    """``focus_placement`` text of a placement."""
    return ",".join(f"{v:.10g}" for v in placement)


@dataclass(frozen=True)
class RamanTarget:
    """One OME image of an image file and its placement in image 0's level-0 pixels.

    ``spot_offset`` and ``spot_normalization`` are the processing of averaged spectra
    (:func:`focus.spatial.spectral.spot_spectra`); ``"none"`` for files whose pixels were already
    processed.
    """

    series: int
    name: str
    channels: list[str]
    wavenumbers: list[float] | None
    placement: Placement
    spot_offset: str = spectral.NO_OFFSET
    spot_normalization: str = spectral.NO_NORMALIZATION

    def spot_spectra(self, spectra: np.ndarray) -> np.ndarray:
        """``spectra`` with this image's spot-level offset removal and normalisation."""
        return spectral.spot_spectra(spectra, self.spot_offset, self.spot_normalization)


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
            placement = parse_placement(values["focus_placement"])
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
                spot_offset=values.get("focus_spot_offset", spectral.NO_OFFSET),
                spot_normalization=values.get(
                    "focus_spot_normalization", spectral.NO_NORMALIZATION
                ),
            )
        )
    return targets


def target_signature(targets: list[RamanTarget]) -> list[list[object]]:
    """Placement and spot processing of every image, for registration cache keys.

    The pixel digest of a FOCUS file does not cover the annotations, so a changed placement would
    otherwise reuse outputs read through the old one.
    """
    return [
        [t.series, format_placement(t.placement), t.spot_offset, t.spot_normalization]
        for t in targets
    ]


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
    m = placement_matrix(target.placement)
    shifted = np.asarray(anchor.mapped, dtype=np.float64) - m[:, 2]
    mapped = np.linalg.solve(m[:, :2], shifted.T).T
    return AnchorGeometry.from_arrays(
        anchor.spatial, mapped, anchor.spot_size, anchor.nonrigid
    )


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
