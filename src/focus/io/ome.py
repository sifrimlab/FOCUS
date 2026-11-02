"""Resolve stored OME-TIFF levels, image axes and physical pixel sizes."""

from typing import cast

from collections.abc import Sequence
from tifffile import TiffFile
from focus.io.types import PyramidLevel

import os

import numpy as np


def hw_from_axes(shape: tuple[int, ...], axes: str | None) -> tuple[int, int]:
    """Return (height, width) = (Y, X) sizes from a shape tuple using its OME 'axes' string.

    Falls back to the last two dimensions (OME canonical order places Y, X last) when the
    axes string is absent or does not label both Y and X against ``shape``. Correct for both
    channel-first ('CYX', e.g. Raman) and channel-last ('YXC', e.g. RGB microscopy) layouts,
    so display->full-resolution scale factors are derived from the real spatial axes rather
    than a positional guess.
    """
    a = (axes or "").upper()
    if len(a) == len(shape) and "Y" in a and "X" in a:
        return int(shape[a.index("Y")]), int(shape[a.index("X")])
    return int(shape[-2]), int(shape[-1])


class _ChannelStack:
    """One pyramid level stored as consecutive single-channel series: exposes ``(C, H, W)``."""

    def __init__(self, series: Sequence[PyramidLevel]) -> None:
        """Retain ordered single-channel TIFF series without reading their pixels.

        Parameters
        ----------
        series : Sequence[PyramidLevel]
            Equal-shaped two-dimensional channel handles; the first supplies height/width."""
        self._series = series
        self.shape = (len(series),) + tuple(series[0].shape)
        self.axes = "CYX"

    def asarray(self) -> np.ndarray:
        """Read the represented TIFF image level into a NumPy array."""
        return np.stack([s.asarray() for s in self._series])


def _group_channel_series(series: Sequence[PyramidLevel]) -> list[PyramidLevel] | None:
    """Group a FOCUS multichannel pyramid (one 2-D series per channel per level) into levels.

    Returns None unless the series form runs of the same length C > 1 of equal 2-D shapes,
    with every run smaller than the previous one.
    """
    if any(len(s.shape) != 2 for s in series):
        return None
    runs: list[list[PyramidLevel]] = []
    for s in series:
        if runs and tuple(s.shape) == tuple(runs[-1][0].shape):
            runs[-1].append(s)
        else:
            runs.append([s])
    sizes = [tuple(r[0].shape) for r in runs]
    if len(runs[0]) < 2 or any(len(r) != len(runs[0]) for r in runs):
        return None
    if any(b[0] >= a[0] or b[1] >= a[1] for a, b in zip(sizes, sizes[1:])):
        return None
    return [_ChannelStack(r) for r in runs]


def resolve_pyramid_levels(tif: TiffFile) -> list[PyramidLevel]:
    """Return the OME-TIFF pyramid level handles of an open ``tifffile.TiffFile``, highest-res first.

    A level handle may be a ``TiffPageSeries`` (SubIFD ``series.levels`` or a FOCUS
    separate-series pyramid), a ``TiffPage`` (direct SubIFD pages), or a channel stack (a
    FOCUS multichannel pyramid, whose channels are separate single-plane series); all expose
    ``.shape``, ``.axes`` and ``.asarray()``. Index ``i`` is pyramid level ``i`` in every branch.
    """
    series0 = tif.series[0]
    if len(series0.levels) > 1:
        # SubIFD pyramid (e.g. qpTIFF, ASHLAR): series.levels already includes the base at index 0.
        return list(series0.levels)
    if tif.pages[0].pages:
        # Direct SubIFD pages exclude the base page itself, so prepend it.
        return cast(list[PyramidLevel], [tif.pages[0]] + list(tif.pages[0].pages))
    if len(tif.series) > 1:
        # FOCUS-written pyramid: one top-level series per level (RGB), or one per channel per
        # level (multichannel), base first.
        return _group_channel_series(list(tif.series)) or list(tif.series)
    return [series0]


def ome_tiff_pyramid_dims(filename: str) -> list[tuple[int, int]]:
    """Per-level ``(H, W)`` of an OME-TIFF pyramid, highest-res first, without loading pixels."""
    import tifffile

    if not os.path.exists(filename):
        raise FileNotFoundError(f"Image file not found: {filename}")
    with tifffile.TiffFile(filename) as tif:
        return [
            hw_from_axes(lvl.shape, getattr(lvl, "axes", ""))
            for lvl in resolve_pyramid_levels(tif)
        ]


def load_ome_tiff_level(filename: str, level: int = 0) -> np.ndarray:
    """Load one stored pyramid level of an OME-TIFF as an HWC float32 array in [0, 1].

    Integer data is rescaled by its dtype maximum. Coarser levels are read from the file
    (FOCUS preprocessing writes a 0.5**i pyramid); nothing is resampled here.
    """
    import tifffile

    if not os.path.exists(filename):
        raise FileNotFoundError(f"Image file not found: {filename}")
    with tifffile.TiffFile(filename) as tif:
        levels = resolve_pyramid_levels(tif)
        if level >= len(levels):
            raise IndexError(
                f"Requested pyramid level {level} but '{filename}' has only {len(levels)} level(s)."
            )
        # Level 0 of a single-series file is the whole series (tif.asarray); a channel stack
        # gathers its channels from separate series.
        image_data = (
            levels[level].asarray()
            if level > 0 or isinstance(levels[0], _ChannelStack)
            else tif.asarray()
        )

    # HWC layout: the channel dimension is the smallest one
    if image_data.ndim == 3 and np.argmin(image_data.shape) == 0:
        image_data = np.transpose(image_data, (1, 2, 0))

    if np.issubdtype(image_data.dtype, np.integer):
        image_data = image_data.astype(np.float32) / np.iinfo(image_data.dtype).max
    elif image_data.dtype != np.float32:
        image_data = image_data.astype(np.float32)
    return image_data


_LENGTH_UNIT_TO_UM = {"m": 1e6, "cm": 1e4, "mm": 1e3, "µm": 1.0, "um": 1.0, "nm": 1e-3}


def read_ome_physical_size_um(filename: str) -> np.ndarray | None:
    """Level-0 ``[x, y]`` pixel size in µm from an OME-TIFF's ``Pixels`` element, or None."""
    import xml.etree.ElementTree as ET
    import tifffile

    try:
        with tifffile.TiffFile(filename) as tif:
            xml = cast(tifffile.TiffPage, tif.pages[0]).description
        root = ET.fromstring(xml)
    except Exception:
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Pixels":
            continue
        size = []
        for axis in ("X", "Y"):
            value = element.get(f"PhysicalSize{axis}")
            unit = element.get(f"PhysicalSize{axis}Unit", "µm")
            if value is None or unit not in _LENGTH_UNIT_TO_UM:
                return None
            size.append(float(value) * _LENGTH_UNIT_TO_UM[unit])
        size_array = np.asarray(size, dtype=np.float64)
        return (
            size_array
            if np.all(np.isfinite(size_array)) and np.all(size_array > 0)
            else None
        )
    return None
