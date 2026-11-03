"""Zeiss CZI reader on pylibCZIrw: region reads per plane, the largest scene, channel metadata."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
import xml.etree.ElementTree as ET

import dask.array as da
import numpy as np

from focus.preprocessing.microscopy.metadata import _czi_pixel_size
from focus.preprocessing.microscopy.source import (
    ChannelInfo,
    SourceImage,
    choose_series,
    hint_from_channels,
    rgb_channels,
)

# CZI pixel types: (numpy dtype, samples per pixel). Bgr types are reordered to RGB on read.
_PIXEL_TYPES = {
    "Gray8": (np.uint8, 1),
    "Gray16": (np.uint16, 1),
    "Gray32Float": (np.float32, 1),
    "Bgr24": (np.uint8, 3),
    "Bgr48": (np.uint16, 3),
    "Bgra32": (np.uint8, 3),
}
_CHUNK = 2048
# Lower resolutions are read with pylibCZIrw's zoom, which uses the file's own pyramid.
_LEVEL_ZOOM = 0.25
_MIN_LEVEL_SIDE = 256


class _CziPlanes:
    """``(T, Z, Y, X, C)`` region reader over one scene of an open CZI document."""

    def __init__(
        self,
        doc: Any,
        rect: Any,
        scene: int | None,
        sizes: list[tuple[int, int]],
        samples: int,
        dtype: type,
        zoom: float,
        lock: threading.Lock,
    ) -> None:
        """Bind the open document, the scene rectangle, the plane ranges and the zoom."""
        self.doc, self.rect, self.scene = doc, rect, scene
        self.samples, self.zoom, self.lock = samples, zoom, lock
        self.t0, self.z0, self.c0 = (start for start, _ in sizes)
        t, z, c = (size for _, size in sizes)
        h = max(1, round(rect.h * zoom))
        w = max(1, round(rect.w * zoom))
        self.shape = (t, z, h, w, c * samples)
        self.dtype = np.dtype(dtype)
        self.ndim = 5

    def _plane(
        self, t: int, z: int, c: int, roi: tuple[int, int, int, int]
    ) -> np.ndarray:
        plane = {"T": self.t0 + t, "Z": self.z0 + z, "C": self.c0 + c}
        with self.lock:
            data = self.doc.read(
                roi=roi,
                plane=plane,
                scene=self.scene,
                zoom=self.zoom if self.zoom != 1 else None,
            )
        if self.samples == 3:
            data = data[..., 2::-1]  # BGR to RGB; drops alpha of Bgra32
        return np.asarray(data, dtype=self.dtype)

    def __getitem__(self, key: tuple[slice | int, ...]) -> np.ndarray:
        """Read a block; dask passes slices, or integers once it fuses an index into the getter."""
        full = tuple(key) + (slice(None),) * (5 - len(key))
        squeeze = tuple(i for i, k in enumerate(full) if not isinstance(k, slice))
        slices = [
            k if isinstance(k, slice) else slice(int(k), int(k) + 1) for k in full
        ]
        return (
            self._read(slices).squeeze(axis=squeeze) if squeeze else self._read(slices)
        )

    def _read(self, key: list[slice]) -> np.ndarray:
        ts, zs, ys, xs, cs = (range(*k.indices(n)) for k, n in zip(key, self.shape))
        if self.zoom == 1:
            roi = (self.rect.x + xs.start, self.rect.y + ys.start, len(xs), len(ys))
            crop = (slice(None), slice(None))
        else:
            roi = (self.rect.x, self.rect.y, self.rect.w, self.rect.h)
            crop = (slice(ys.start, ys.stop), slice(xs.start, xs.stop))
        channels = sorted({c // self.samples for c in cs})
        out = np.empty((len(ts), len(zs), len(ys), len(xs), len(cs)), dtype=self.dtype)
        for i, t in enumerate(ts):
            for j, z in enumerate(zs):
                planes = []
                for c in channels:
                    data = self._plane(t, z, c, roi)
                    if self.zoom != 1:
                        data = _fit(data, self.shape[2], self.shape[3])
                    planes.append(data[crop[0], crop[1]])
                stack = np.concatenate(planes, axis=-1)
                first = channels[0] * self.samples
                out[i, j] = stack[..., [c - first for c in cs]]
        return out


def _fit(data: np.ndarray, h: int, w: int) -> np.ndarray:
    """Pad or trim a zoomed read to the declared level size (the zoom rounds per side)."""
    out = np.zeros((h, w, data.shape[-1]), dtype=data.dtype)
    hh, ww = min(h, data.shape[0]), min(w, data.shape[1])
    out[:hh, :ww] = data[:hh, :ww]
    return out


def open_czi(path: str) -> SourceImage:
    """Open the largest scene of a CZI file as lazy arrays."""
    from pylibCZIrw import czi as pyczi

    context = pyczi.open_czi(path)
    doc = context.__enter__()
    try:
        return _read(doc, path, lambda: context.__exit__(None, None, None))
    except BaseException:
        context.__exit__(None, None, None)
        raise


def _read(doc: Any, path: str, close: Callable[[], None]) -> SourceImage:
    box = doc.total_bounding_box
    sizes = [box.get(axis, (0, 1)) for axis in ("T", "Z", "C")]
    dtype, samples = _pixel_type(doc)
    scene, rect, notes = _scene(doc)
    lock = threading.Lock()
    levels: list[da.Array] = []
    zoom = 1.0
    while not levels or min(rect.h, rect.w) * zoom >= _MIN_LEVEL_SIDE:
        planes = _CziPlanes(doc, rect, scene, sizes, samples, dtype, zoom, lock)
        chunks = (
            (1, 1, _CHUNK, _CHUNK, samples) if zoom == 1 else (1, 1, -1, -1, samples)
        )
        meta: np.ndarray = np.empty((0,) * 5, dtype)
        array = da.from_array(planes, chunks=chunks, asarray=True, meta=meta)  # type: ignore[no-untyped-call]
        levels.append(array)
        zoom *= _LEVEL_ZOOM
    root = _parse(doc.raw_metadata)
    channels = _channels(root, sizes[2][1], samples)
    pixel_size = _czi_pixel_size(doc.raw_metadata)
    magnification, na = _objective(root)
    return SourceImage(
        path=path,
        format="czi",
        levels=levels,
        rgb=samples == 3,
        channels=channels,
        pixel_size=pixel_size,
        pixel_size_source="czi" if pixel_size is not None else "none",
        significant_bits=_int(_text(root, "ComponentBitCount")),
        objective_magnification=magnification,
        objective_na=na,
        series=int(scene or 0),
        notes=notes,
        type_hint=hint_from_channels(channels),
        _close=close,
    )


def _pixel_type(doc: Any) -> tuple[type, int]:
    """``(dtype, samples per pixel)`` shared by every channel."""
    types = {doc.pixel_types[c] for c in doc.pixel_types}
    if len(types) != 1:
        raise ValueError(f"CZI channels have different pixel types: {sorted(types)}")
    pixel_type = types.pop()
    if pixel_type not in _PIXEL_TYPES:
        raise ValueError(f"Unsupported CZI pixel type {pixel_type}")
    return _PIXEL_TYPES[pixel_type]


def _scene(doc: Any) -> tuple[int | None, Any, list[str]]:
    """``(scene index or None, bounding rectangle, notes)`` of the largest scene."""
    scenes = dict(doc.scenes_bounding_rectangle)
    if not scenes:
        return None, doc.total_bounding_rectangle, []
    keys = sorted(scenes)
    index, notes = choose_series(
        [(scenes[k].h, scenes[k].w) for k in keys], [None] * len(keys)
    )
    scene = keys[index]
    if len(keys) > 1 and not notes:
        notes = [f"{len(keys)} scenes in the file: scene {scene} is used"]
    return scene, scenes[scene], notes


def _parse(xml: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def _text(root: ET.Element | None, tag: str) -> str | None:
    if root is None:
        return None
    element = root.find(f".//{tag}")
    return element.text if element is not None else None


def _int(value: str | None) -> int | None:
    return int(value) if value and value.strip().isdigit() else None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _channels(root: ET.Element | None, count: int, samples: int) -> list[ChannelInfo]:
    """Channel metadata from ``Information/Image/Dimensions/Channels`` and display colours."""
    if samples == 3:
        return rgb_channels(ChannelInfo(name="RGB", contrast_method="Brightfield"))
    info = (
        root.findall(".//Information/Image/Dimensions/Channels/Channel")
        if root is not None
        else []
    )
    display = (
        root.findall(".//DisplaySetting/Channels/Channel") if root is not None else []
    )
    channels = []
    for i in range(count):
        element = info[i] if i < len(info) else None
        color = None
        if i < len(display):
            value = (display[i].findtext("Color") or "").lstrip("#")
            color = value[-6:].upper() if len(value) >= 6 else None
        if element is None:
            channels.append(ChannelInfo(name=f"Channel {i}", color=color))
            continue
        channels.append(
            ChannelInfo(
                name=element.get("Name") or f"Channel {i}",
                color=color,
                excitation_nm=_float(element.findtext("ExcitationWavelength")),
                emission_nm=_float(element.findtext("EmissionWavelength")),
                fluor=element.findtext("Fluor"),
                illumination=element.findtext("IlluminationType"),
                acquisition_mode=element.findtext("AcquisitionMode"),
                contrast_method=element.findtext("ContrastMethod"),
            )
        )
    return channels


def _objective(root: ET.Element | None) -> tuple[float | None, float | None]:
    if root is None:
        return None, None
    objective = root.find(".//Information/Instrument/Objectives/Objective")
    if objective is None:
        return None, None
    return _float(objective.findtext("NominalMagnification")), _float(
        objective.findtext("LensNA")
    )
