"""TIFF-family reader: OME-TIFF, qpTIFF, SVS, NDPI, SCN, BIF, ImageJ and plain TIFF."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import dask.array as da
import numpy as np
import tifffile
import zarr

from focus.preprocessing.microscopy.metadata import (
    _tiff_page_pixel_size,
    _valid_pixel_size,
)
from focus.preprocessing.microscopy.source import (
    BRIGHTFIELD,
    ChannelInfo,
    SourceImage,
    canonical_axes,
    choose_series,
    color_hex,
    hint_from_channels,
    rgb_channels,
)

# tifffile flags of whole-slide brightfield scanner formats.
_SLIDE_FLAGS = ("is_svs", "is_ndpi", "is_scn", "is_bif")
_RGB_PHOTOMETRIC = (tifffile.PHOTOMETRIC.RGB, tifffile.PHOTOMETRIC.YCBCR)
_UNIT_TO_UM = {"m": 1e6, "cm": 1e4, "mm": 1e3, "µm": 1.0, "um": 1.0, "nm": 1e-3}


def open_tiff(path: str) -> SourceImage:
    """Open the acquisition series of a TIFF-family file as lazy arrays."""
    tif = tifffile.TiffFile(path)
    try:
        return _read(tif, path)
    except BaseException:
        tif.close()
        raise


def _read(tif: tifffile.TiffFile, path: str) -> SourceImage:
    series_list = tif.series
    index, notes = choose_series(
        [_series_hw(s) for s in series_list], [s.name for s in series_list]
    )
    series = series_list[index]
    keyframe = series.keyframe
    levels, rgb = _levels(series, keyframe, notes)
    ome = _ome_image(tif, index)
    channels = _channels(tif, series, ome, int(levels[0].shape[-1]), rgb)
    pixel_size, size_source = _pixel_size(tif, keyframe, ome)
    magnification, na = _objective(tif, keyframe, ome)
    fmt, hint = _format_and_hint(tif, channels)
    return SourceImage(
        path=path,
        format=fmt,
        levels=levels,
        rgb=rgb,
        channels=channels,
        pixel_size=pixel_size,
        pixel_size_source=size_source,
        significant_bits=_significant_bits(ome),
        objective_magnification=magnification,
        objective_na=na,
        series=index,
        notes=notes,
        type_hint=hint,
        _close=tif.close,
    )


def _levels(
    series: tifffile.TiffPageSeries, keyframe: tifffile.TiffPage, notes: list[str]
) -> tuple[list[da.Array], bool]:
    """The series' stored resolutions as lazy ``(T, Z, Y, X, C)`` arrays; RGB without alpha."""
    levels = []
    for level in range(len(series.levels)):
        store = zarr.open(series.aszarr(level=level), mode="r")
        array = da.from_zarr(store)  # type: ignore[no-untyped-call]
        array, axis_notes = canonical_axes(array, series.levels[level].axes)
        levels.append(array)
    notes.extend(axis_notes)
    count = int(levels[0].shape[-1])
    rgb = keyframe.photometric in _RGB_PHOTOMETRIC and count in (3, 4)
    if rgb and count == 4:
        levels = [level[..., :3] for level in levels]
        notes.append("The alpha channel is not kept")
    return levels, rgb


def _format_and_hint(
    tif: tifffile.TiffFile, channels: list[ChannelInfo]
) -> tuple[str, tuple[str, str] | None]:
    """Format label and the image type the metadata or a scanner format states."""
    fmt = "ome-tiff" if tif.is_ome else "qptiff" if tif.is_qpi else "tiff"
    slide = next((flag for flag in _SLIDE_FLAGS if getattr(tif, flag, False)), None)
    if slide:
        fmt = slide[3:]
    hint = hint_from_channels(channels)
    if hint is None and slide:
        hint = (BRIGHTFIELD, f"whole-slide brightfield scanner format ({fmt})")
    return fmt, hint


def _series_hw(series: tifffile.TiffPageSeries) -> tuple[int, int]:
    """Rows and columns of a series from its axes."""
    axes = series.axes.upper()
    return int(series.shape[axes.index("Y")]), int(series.shape[axes.index("X")])


def _ome_image(tif: tifffile.TiffFile, index: int) -> ET.Element | None:
    """The OME ``Image`` element of series ``index``, or None."""
    if not tif.is_ome or not tif.ome_metadata:
        return None
    try:
        root = ET.fromstring(tif.ome_metadata)
    except ET.ParseError:
        return None
    images = [e for e in root if _tag(e) == "Image"]
    return images[index] if index < len(images) else None


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((e for e in element if _tag(e) == name), None)


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _ome_color(value: str | None) -> str | None:
    """OME ``Color`` (signed 32-bit RGBA integer) as ``"RRGGBB"``."""
    if value is None:
        return None
    try:
        rgba = int(value) & 0xFFFFFFFF
    except ValueError:
        return None
    return color_hex(((rgba >> 24) & 0xFF, (rgba >> 16) & 0xFF, (rgba >> 8) & 0xFF))


def _channels(
    tif: tifffile.TiffFile,
    series: tifffile.TiffPageSeries,
    ome: ET.Element | None,
    count: int,
    rgb: bool,
) -> list[ChannelInfo]:
    """Channel names and acquisition metadata from OME, qpTIFF or ImageJ; defaults otherwise."""
    pixels = _child(ome, "Pixels")
    if rgb:
        element = _child(pixels, "Channel")
        return rgb_channels(
            _ome_channel(element, "RGB") if element is not None else None
        )
    found: list[ChannelInfo] = []
    if pixels is not None:
        found = [
            _ome_channel(e, f"Channel {i}")
            for i, e in enumerate(e for e in pixels if _tag(e) == "Channel")
        ]
    elif tif.is_qpi:
        found = _qptiff_channels(series)
    elif tif.is_imagej:
        labels = (tif.imagej_metadata or {}).get("Labels")
        if isinstance(labels, (list, tuple)) and len(labels) == count:
            found = [ChannelInfo(name=str(label)) for label in labels]
    if len(found) != count:
        found = [ChannelInfo(name=f"Channel {i}") for i in range(count)]
    return found


def _ome_channel(element: ET.Element, default: str) -> ChannelInfo:
    return ChannelInfo(
        name=element.get("Name") or default,
        color=_ome_color(element.get("Color")),
        excitation_nm=_float(element.get("ExcitationWavelength")),
        emission_nm=_float(element.get("EmissionWavelength")),
        fluor=element.get("Fluor"),
        illumination=element.get("IlluminationType"),
        acquisition_mode=element.get("AcquisitionMode"),
        contrast_method=element.get("ContrastMethod"),
    )


def _qptiff_channels(series: tifffile.TiffPageSeries) -> list[ChannelInfo]:
    """Channel names (biomarker first) and colours from the qpTIFF page descriptions."""
    channels: list[ChannelInfo] = []
    for page in series.pages:
        description = getattr(page, "description", "") or ""
        try:
            root = ET.fromstring(description)
        except ET.ParseError:
            return []
        values = {_tag(e): (e.text or "").strip() for e in root.iter()}
        name = (
            values.get("Biomarker") or values.get("Name") or f"Channel {len(channels)}"
        )
        color = None
        if values.get("Color"):
            parts = re.split(r"[,\s]+", values["Color"])
            if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
                color = color_hex([int(p) for p in parts[:3]])
        channels.append(ChannelInfo(name=name, color=color))
    return channels


def _pixel_size(
    tif: tifffile.TiffFile, keyframe: tifffile.TiffPage, ome: ET.Element | None
) -> tuple[np.ndarray | None, str]:
    """Pixel size of the chosen series: OME first, then the page tags (Aperio, ImageJ, resolution)."""
    pixels = _child(ome, "Pixels")
    if pixels is not None:
        values: list[float] = []
        for axis in ("X", "Y"):
            length = _float(pixels.get(f"PhysicalSize{axis}"))
            factor = _UNIT_TO_UM.get(pixels.get(f"PhysicalSize{axis}Unit", "µm"))
            if length is not None and factor is not None:
                values.append(length * factor)
        size = _valid_pixel_size(values) if len(values) == 2 else None
        if size is not None:
            return size, "ome"
    return _tiff_page_pixel_size(tif, keyframe)


def _objective(
    tif: tifffile.TiffFile, keyframe: tifffile.TiffPage, ome: ET.Element | None
) -> tuple[float | None, float | None]:
    """Nominal objective magnification and numerical aperture, when recorded."""
    if ome is not None and tif.ome_metadata:
        settings = _child(ome, "ObjectiveSettings")
        objective_id = settings.get("ID") if settings is not None else None
        root = ET.fromstring(tif.ome_metadata)
        objectives = [e for e in root.iter() if _tag(e) == "Objective"]
        chosen = next(
            (e for e in objectives if e.get("ID") == objective_id),
            objectives[0] if len(objectives) == 1 else None,
        )
        if chosen is not None:
            return _float(chosen.get("NominalMagnification")), _float(
                chosen.get("LensNA")
            )
    if tif.is_svs:
        from tifffile.tifffile import svs_description_metadata

        info = svs_description_metadata(keyframe.description or "")
        return _float(str(info.get("AppMag"))) if "AppMag" in info else None, None
    return None, None


def _significant_bits(ome: ET.Element | None) -> int | None:
    pixels = _child(ome, "Pixels")
    value = pixels.get("SignificantBits") if pixels is not None else None
    return int(value) if value and value.isdigit() else None
