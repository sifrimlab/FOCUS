"""Lazy, axis-aware view of one microscopy source file and the metadata FOCUS carries forward."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
import os

import dask.array as da
import numpy as np

from focus.preprocessing.microscopy.metadata import _SUPPORTED_EXTENSIONS

# Canonical axis order of every SourceImage array: time, depth, rows, columns, channels.
AXES = "TZYXC"

# Series whose name contains one of these words are slide overviews, not the acquisition.
_AUXILIARY_SERIES = ("label", "macro", "thumbnail", "overview", "map", "preview")

BRIGHTFIELD = "brightfield"
FLUORESCENCE = "fluorescence"

# OME acquisition modes and contrast methods that identify a fluorescence or a transmitted-light
# acquisition (OME schema 2016-06 enumerations).
_FLUORESCENCE_MODES = {
    "laserscanningconfocalmicroscopy",
    "spinningdiskconfocal",
    "slitscanconfocal",
    "sweptfieldconfocal",
    "multiphotonmicroscopy",
    "structuredillumination",
    "totalinternalreflection",
    "tirf",
    "fluorescencelifetime",
    "spim",
    "sted",
    "palm",
    "storm",
    "fluorescence",
    "epifluorescence",
}
_BRIGHTFIELD_MODES = {"brightfield", "transmitted"}


@dataclass(frozen=True)
class ChannelInfo:
    """Acquisition metadata of one channel; every field is optional except the name."""

    name: str
    color: str | None = None  # "RRGGBB"
    excitation_nm: float | None = None
    emission_nm: float | None = None
    fluor: str | None = None
    illumination: str | None = None  # OME IlluminationType
    acquisition_mode: str | None = None  # OME AcquisitionMode
    contrast_method: str | None = None  # OME ContrastMethod


@dataclass
class SourceImage:
    """One image series of a source file as lazy ``(T, Z, Y, X, C)`` arrays plus its metadata.

    ``levels[0]`` is the full-resolution array; further entries are the source's own lower
    resolutions (same axes), used for tissue detection without reading every pixel.
    """

    path: str
    format: str
    levels: list[da.Array]
    rgb: bool
    channels: list[ChannelInfo]
    pixel_size: np.ndarray | None = None
    pixel_size_source: str = "none"
    significant_bits: int | None = None
    objective_magnification: float | None = None
    objective_na: float | None = None
    series: int = 0
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # (image type, evidence) read from the acquisition metadata, or None.
    type_hint: tuple[str, str] | None = None
    _close: Callable[[], None] | None = None

    @property
    def data(self) -> da.Array:
        """Full-resolution ``(T, Z, Y, X, C)`` array."""
        return self.levels[0]

    @property
    def dtype(self) -> np.dtype:
        """On-disk sample type, written unchanged to the output."""
        dtype: np.dtype = np.dtype(self.levels[0].dtype)
        return dtype

    @property
    def shape_yx(self) -> tuple[int, int]:
        """Full-resolution ``(rows, columns)``."""
        return int(self.data.shape[2]), int(self.data.shape[3])

    def close(self) -> None:
        """Release the file handles that back the lazy arrays."""
        if self._close is not None:
            self._close()
            self._close = None


def find_source_file(directory: str) -> str:
    """The single supported image file of a sample folder.

    Hidden files are ignored. ``FileNotFoundError`` when there is none, ``ValueError`` when there
    are several, so a sample never silently uses one of two acquisitions.
    """
    matches = sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if not name.startswith(".") and name.lower().endswith(_SUPPORTED_EXTENSIONS)
    )
    if not matches:
        raise FileNotFoundError(
            f"No supported image file ({', '.join(_SUPPORTED_EXTENSIONS)}) found in {directory}"
        )
    if len(matches) > 1:
        names = ", ".join(os.path.basename(m) for m in matches)
        raise ValueError(
            f"Several image files in {directory} ({names}): keep one image per sample folder"
        )
    return matches[0]


@contextmanager
def open_source(path: str) -> Iterator[SourceImage]:
    """Open ``path`` with the reader of its format; the file stays open inside the block."""
    lower = path.lower()
    if lower.endswith(".czi"):
        from focus.preprocessing.microscopy.czi_source import open_czi as opener
    elif lower.endswith(".nd2"):
        from focus.preprocessing.microscopy.nd2_source import open_nd2 as opener
    else:
        from focus.preprocessing.microscopy.tiff_source import open_tiff as opener
    source = opener(path)
    try:
        yield source
    finally:
        source.close()


def is_auxiliary_series(name: str | None) -> bool:
    """True for slide label, macro and thumbnail images stored beside the acquisition."""
    lower = (name or "").lower()
    return any(word in lower for word in _AUXILIARY_SERIES)


def choose_series(
    sizes: Sequence[tuple[int, int]], names: Sequence[str | None]
) -> tuple[int, list[str]]:
    """Index of the largest non-auxiliary series and a note per other acquisition-sized series.

    One rule for every format: auxiliary images (label, macro, thumbnail) are never chosen, and
    among the rest the series with the most pixels is the acquisition.
    """
    candidates = [i for i, name in enumerate(names) if not is_auxiliary_series(name)]
    if not candidates:
        candidates = list(range(len(sizes)))
    best = max(candidates, key=lambda i: sizes[i][0] * sizes[i][1])
    best_area = sizes[best][0] * sizes[best][1]
    others = [
        i
        for i in candidates
        if i != best and sizes[i][0] * sizes[i][1] >= 0.5 * best_area
    ]
    notes = []
    if others:
        notes.append(
            f"{len(candidates)} image series in the file: the largest (series {best}) is used"
        )
    return best, notes


def canonical_axes(array: da.Array, axes: str) -> tuple[da.Array, list[str]]:
    """Reorder an array with named axes to ``(T, Z, Y, X, C)``.

    ``S`` (samples) and ``C`` are merged into one channel axis. An unnamed axis (tifffile ``I``,
    ``Q`` and similar) becomes the channel axis when there is none, else depth, else time; each
    such decision is returned as a note.
    """
    notes: list[str] = []
    named = list(axes.upper())
    for i, axis in enumerate(named):
        if axis in "TZYXCS":
            continue
        for target in ("C", "Z", "T"):
            if target not in named and not (target == "C" and "S" in named):
                notes.append(
                    f"Unlabelled image axis of size {array.shape[i]} read as {target}"
                )
                named[i] = target
                break
        else:
            raise ValueError(f"Unsupported image axes {axes!r}")
    if "C" in named and "S" in named:
        c, s = named.index("C"), named.index("S")
        array = da.moveaxis(array, s, c + 1 if c < s else c)
        named.pop(s)
        c = named.index("C")
        shape = list(array.shape)
        merged = shape[:c] + [shape[c] * shape[c + 1]] + shape[c + 2 :]
        array = array.reshape(merged)  # type: ignore[no-untyped-call]
    named = ["C" if a == "S" else a for a in named]
    for axis in AXES:
        if axis not in named:
            array = array[..., None]
            named.append(axis)
    order = [named.index(axis) for axis in AXES]
    return da.transpose(array, order), notes


def hint_from_channels(channels: Sequence[ChannelInfo]) -> tuple[str, str] | None:
    """Image type stated by the channels' OME acquisition mode, contrast method or illumination."""
    for channel in channels:
        for field_name in ("acquisition_mode", "contrast_method", "illumination"):
            value = getattr(channel, field_name)
            if not value:
                continue
            key = str(value).replace(" ", "").replace("_", "").lower()
            if key in _FLUORESCENCE_MODES:
                return FLUORESCENCE, f"{field_name} {value}"
            if key in _BRIGHTFIELD_MODES:
                return BRIGHTFIELD, f"{field_name} {value}"
    for channel in channels:
        if channel.emission_nm or channel.fluor:
            return (
                FLUORESCENCE,
                "emission wavelength or fluorophore in the channel metadata",
            )
    return None


def rgb_channels(acquisition: ChannelInfo | None = None) -> list[ChannelInfo]:
    """Red, green and blue samples of an RGB image, sharing the acquisition fields."""
    shared: dict[str, Any] = {}
    if acquisition is not None:
        shared = dict(
            illumination=acquisition.illumination,
            acquisition_mode=acquisition.acquisition_mode,
            contrast_method=acquisition.contrast_method,
        )
    return [
        ChannelInfo(name=name, color=colour, **shared)
        for name, colour in (("Red", "FF0000"), ("Green", "00FF00"), ("Blue", "0000FF"))
    ]


def color_hex(rgb: Sequence[int] | None) -> str | None:
    """``"RRGGBB"`` of an ``(r, g, b)`` triple, or None."""
    if rgb is None:
        return None
    r, g, b = (int(v) & 0xFF for v in list(rgb)[:3])
    return f"{r:02X}{g:02X}{b:02X}"
