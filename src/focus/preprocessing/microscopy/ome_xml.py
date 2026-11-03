"""OME-XML of a preprocessed image: channels, objective, tissue ROI and FOCUS provenance."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

import numpy as np
from ome_types.model import (
    OME,
    ROI,
    Channel,
    Image,
    Instrument,
    InstrumentRef,
    Map,
    MapAnnotation,
    Objective,
    ObjectiveSettings,
    Pixels,
    Polygon,
    ROIRef,
    StructuredAnnotations,
    TiffData,
)

from focus.io.provenance import FOCUS_OME_NAMESPACE
from focus.preprocessing.microscopy.source import ChannelInfo

# Output sample type -> OME PixelType spelling.
OME_PIXEL_TYPE = {
    np.dtype(np.uint8): "uint8",
    np.dtype(np.uint16): "uint16",
    np.dtype(np.uint32): "uint32",
    np.dtype(np.int8): "int8",
    np.dtype(np.int16): "int16",
    np.dtype(np.int32): "int32",
    np.dtype(np.float32): "float",
    np.dtype(np.float64): "double",
}


def ome_pixel_type(dtype: np.dtype) -> str:
    """OME PixelType of a sample type; ValueError for types OME cannot store (64-bit integers)."""
    try:
        return OME_PIXEL_TYPE[np.dtype(dtype)]
    except KeyError:
        raise ValueError(
            f"Unsupported sample type {np.dtype(dtype).name} for OME-TIFF"
        ) from None


def _rgba(colour: str | None) -> int | None:
    """``"RRGGBB"`` as the signed 32-bit RGBA integer OME stores."""
    if not colour:
        return None
    value = (int(colour, 16) << 8) | 0xFF
    return value - (1 << 32) if value >= 1 << 31 else value


def _channel(
    index: int, channel: ChannelInfo, colour: str | None, samples: int
) -> Channel:
    """OME Channel; enumeration values OME does not define are left out instead of failing."""
    fields: dict[str, Any] = {
        "id": f"Channel:0:{index}",
        "name": channel.name,
        "samples_per_pixel": samples,
        "fluor": channel.fluor,
    }
    rgba = _rgba(colour)
    if rgba is not None:
        fields["color"] = rgba
    for key, wavelength in (
        ("excitation_wavelength", channel.excitation_nm),
        ("emission_wavelength", channel.emission_nm),
    ):
        if wavelength:
            fields[key] = float(wavelength)
            fields[f"{key}_unit"] = "nm"
    for key, value in (
        ("illumination_type", channel.illumination),
        ("acquisition_mode", channel.acquisition_mode),
        ("contrast_method", channel.contrast_method),
    ):
        if value and _valid(key, value):
            fields[key] = value
    return Channel.model_validate({k: v for k, v in fields.items() if v is not None})


def _valid(key: str, value: str) -> bool:
    """True when ``value`` is an OME enumeration value of the Channel field ``key``."""
    try:
        Channel.model_validate({"id": "Channel:0:0", key: value})
    except ValueError:
        return False
    return True


def _roi(contours: Sequence[np.ndarray]) -> ROI | None:
    """One ``tissue`` ROI whose polygons outline the detected tissue in level-0 pixels."""
    if not contours:
        return None
    shapes = [
        Polygon(
            id=f"Shape:{i}",
            text="tissue",
            points=" ".join(f"{x:g},{y:g}" for x, y in contour),
        )
        for i, contour in enumerate(contours)
    ]
    return ROI.model_validate({"id": "ROI:0", "name": "tissue", "union": shapes})


@dataclass(frozen=True)
class OmeImage:
    """What the OME-XML describes: the written level-0 image and its acquisition metadata."""

    name: str
    shape_yx: tuple[int, int]
    dtype: np.dtype
    rgb: bool
    channels: Sequence[ChannelInfo]
    colours: Sequence[str]
    pixel_size: np.ndarray | None
    significant_bits: int | None
    objective: tuple[float | None, float | None]
    contours: Sequence[np.ndarray]


def _planes(image: OmeImage) -> tuple[list[Channel], list[TiffData]]:
    """Channels and page mapping: one interleaved RGB plane, or one page per channel."""
    if image.rgb:
        acquisition = replace(
            image.channels[0],
            name="RGB",
            color=None,
            excitation_nm=None,
            emission_nm=None,
            fluor=None,
        )
        return [_channel(0, acquisition, None, 3)], [TiffData(ifd=0, plane_count=1)]
    channels = [
        _channel(i, c, image.colours[i], 1) for i, c in enumerate(image.channels)
    ]
    return channels, [
        TiffData(ifd=c, first_c=c, plane_count=1) for c in range(len(channels))
    ]


def _pixels(image: OmeImage) -> Pixels:
    """Pixels element of the level-0 image."""
    h, w = image.shape_yx
    channels, tiff_data = _planes(image)
    fields: dict[str, Any] = dict(
        id="Pixels:0",
        dimension_order="XYCZT",
        type=ome_pixel_type(image.dtype),
        size_x=w,
        size_y=h,
        size_z=1,
        size_c=3 if image.rgb else len(channels),
        size_t=1,
        interleaved=image.rgb,
        channels=channels,
        tiff_data_blocks=tiff_data,
    )
    if image.significant_bits:
        fields["significant_bits"] = int(image.significant_bits)
    if image.pixel_size is not None:
        fields.update(
            physical_size_x=float(image.pixel_size[0]),
            physical_size_x_unit="µm",
            physical_size_y=float(image.pixel_size[1]),
            physical_size_y_unit="µm",
        )
    return Pixels(**fields)


def build_ome_xml(image: OmeImage, annotations: dict[str, object]) -> str:
    """OME-XML for the level-0 image (pyramid levels are SubIFDs of its planes)."""
    fields: dict[str, Any] = dict(id="Image:0", name=image.name, pixels=_pixels(image))
    ome = OME()
    magnification, na = image.objective
    if magnification or na:
        objective = Objective(
            id="Objective:0", nominal_magnification=magnification, lens_na=na
        )
        ome.instruments = [Instrument(id="Instrument:0", objectives=[objective])]
        fields.update(
            instrument_ref=InstrumentRef(id="Instrument:0"),
            objective_settings=ObjectiveSettings(id="Objective:0"),
        )
    roi = _roi(image.contours)
    if roi is not None:
        ome.rois = [roi]
        fields["roi_refs"] = [ROIRef(id="ROI:0")]
    ome.images = [Image(**fields)]
    entries = [{"k": k, "value": str(v)} for k, v in annotations.items()]
    ome.structured_annotations = StructuredAnnotations(
        map_annotations=[
            MapAnnotation(
                id="Annotation:0",
                namespace=FOCUS_OME_NAMESPACE,
                value=cast(Map, {"ms": entries}),
            )
        ]
    )
    return ome.to_xml()
