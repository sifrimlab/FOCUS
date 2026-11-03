"""Microscopy method version and supported source-file extensions."""

from numpy.typing import ArrayLike
import nd2

import xml.etree.ElementTree as ET
import numpy as np
import tifffile


# Supported input file extensions, ordered by priority
_SUPPORTED_EXTENSIONS = (
    ".ome.tiff",
    ".ome.tif",
    ".qptiff",
    ".tiff",
    ".tif",
    ".czi",
    ".nd2",
)


# Bump whenever the same input and settings produce a different output image.
METHOD_VERSION = "microscopy-2"


# Plausible microscope pixel sizes; anything outside is a placeholder (e.g. a 72/300 dpi default).
_PIXEL_SIZE_RANGE_UM = (0.01, 100.0)

_UNIT_TO_UM = {
    "m": 1e6,
    "cm": 1e4,
    "mm": 1e3,
    "micron": 1.0,
    "microns": 1.0,
    "micrometer": 1.0,
    "µm": 1.0,
    "\u03bcm": 1.0,
    "um": 1.0,
    "nm": 1e-3,
}

# TIFF ResolutionUnit tag values (tifffile RESUNIT) -> µm per unit
_RESUNIT_TO_UM = {2: 25400.0, 3: 1e4, 4: 1e3, 5: 1.0}


def _valid_pixel_size(size: ArrayLike | None) -> np.ndarray | None:
    """``[x, y]`` µm per pixel as float64, or None when missing or implausible."""
    try:
        size = np.asarray(size, dtype=np.float64).ravel()
    except (TypeError, ValueError):
        return None
    lo, hi = _PIXEL_SIZE_RANGE_UM
    if (
        size.size != 2
        or not np.all(np.isfinite(size))
        or np.any(size < lo)
        or np.any(size > hi)
    ):
        return None
    return size


def _tiff_page_pixel_size(
    tif: tifffile.TiffFile, page: tifffile.TiffPage
) -> tuple[np.ndarray | None, str]:
    """Pixel size of one TIFF page: Aperio description, ImageJ unit, or resolution tags."""
    description = page.description or ""
    if page.is_svs or description.startswith("Aperio"):
        from tifffile.tifffile import svs_description_metadata

        mpp = svs_description_metadata(description).get("MPP")
        if mpp is not None:
            return _valid_pixel_size([mpp, mpp]), "aperio"

    x_tag, y_tag = page.tags.get("XResolution"), page.tags.get("YResolution")
    if x_tag is None or y_tag is None:
        return None, "none"
    x_res, y_res = (
        v[0] / v[1] if isinstance(v, tuple) else float(v)
        for v in (x_tag.value, y_tag.value)
    )
    if x_res <= 0 or y_res <= 0:
        return None, "none"

    if page.is_imagej:
        unit = str((tif.imagej_metadata or {}).get("unit", "")).strip().lower()
        if unit in _UNIT_TO_UM:
            return _valid_pixel_size(
                [_UNIT_TO_UM[unit] / x_res, _UNIT_TO_UM[unit] / y_res]
            ), "imagej"

    unit_tag = page.tags.get("ResolutionUnit")
    um_per_unit = _RESUNIT_TO_UM.get(int(unit_tag.value) if unit_tag is not None else 2)
    if um_per_unit is None:
        return None, "none"
    return _valid_pixel_size(
        [um_per_unit / x_res, um_per_unit / y_res]
    ), "tiff_resolution"


def _czi_pixel_size(metadata_xml: str) -> np.ndarray | None:
    """``Scaling/Items/Distance[@Id='X'|'Y']/Value`` (metres) of a CZI metadata XML, in µm."""
    try:
        root = ET.fromstring(metadata_xml)
    except ET.ParseError:
        return None
    values = {}
    for distance in root.iter("Distance"):
        axis = distance.get("Id")
        value = distance.find("Value")
        if axis in ("X", "Y") and value is not None and value.text:
            values[axis] = float(value.text) * 1e6
    if "X" not in values or "Y" not in values:
        return None
    return _valid_pixel_size([values["X"], values["Y"]])


def _nd2_pixel_size(f: nd2.ND2File) -> np.ndarray | None:
    """``voxel_size()`` of an open ND2 file, only when its X/Y axes are calibrated."""
    channels = getattr(getattr(f, "metadata", None), "channels", None) or []
    calibrated = (
        getattr(getattr(channels[0], "volume", None), "axesCalibrated", None)
        if channels
        else None
    )
    if not calibrated or not all(calibrated[:2]):
        return None
    voxel = f.voxel_size()
    return _valid_pixel_size([voxel.x, voxel.y])
