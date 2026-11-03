"""Nikon ND2 reader on the ``nd2`` package: lazy frames, channel and objective metadata."""

from __future__ import annotations

from typing import Any

import dask.array as da

from focus.preprocessing.microscopy.metadata import _nd2_pixel_size
from focus.preprocessing.microscopy.source import (
    BRIGHTFIELD,
    FLUORESCENCE,
    ChannelInfo,
    SourceImage,
    canonical_axes,
    color_hex,
    hint_from_channels,
    rgb_channels,
)


def open_nd2(path: str) -> SourceImage:
    """Open an ND2 file; positions other than the first are reported and not read."""
    import nd2

    f = nd2.ND2File(path)
    try:
        return _read(f, path)
    except BaseException:
        f.close()
        raise


def _read(f: Any, path: str) -> SourceImage:
    array, notes = _array(f)
    rgb = bool(f.is_rgb)
    meta_channels = list(getattr(f.metadata, "channels", None) or [])
    channels = _channels(meta_channels, int(array.shape[-1]), rgb)
    microscope = meta_channels[0].microscope if meta_channels else None
    pixel_size = _nd2_pixel_size(f)
    return SourceImage(
        path=path,
        format="nd2",
        levels=[array],
        rgb=rgb,
        channels=channels,
        pixel_size=pixel_size,
        pixel_size_source="nd2" if pixel_size is not None else "none",
        significant_bits=getattr(
            getattr(f, "attributes", None), "bitsPerComponentSignificant", None
        ),
        objective_magnification=getattr(microscope, "objectiveMagnification", None),
        objective_na=getattr(microscope, "objectiveNumericalAperture", None),
        notes=notes,
        type_hint=_hint(channels, microscope),
        _close=f.close,
    )


def _array(f: Any) -> tuple[da.Array, list[str]]:
    """The first stage position as a lazy ``(T, Z, Y, X, C)`` array, with notes."""
    sizes = dict(f.sizes)
    array = f.to_dask()
    axes = "".join(sizes)
    notes: list[str] = []
    if "P" in sizes:
        position = axes.index("P")
        array = da.take(array, [0], axis=position).squeeze(axis=position)
        axes = axes.replace("P", "")
        if sizes["P"] > 1:
            notes.append(
                f"{sizes['P']} stage positions in the file: position 0 is used"
            )
    array, axis_notes = canonical_axes(array, axes)
    return array, notes + axis_notes


def _hint(channels: list[ChannelInfo], microscope: Any) -> tuple[str, str] | None:
    """Image type from the channel metadata, else from the ND2 modality flags."""
    hint = hint_from_channels(channels)
    flags = [str(x).lower() for x in (getattr(microscope, "modalityFlags", None) or [])]
    if hint is None and any("fluorescence" in x for x in flags):
        hint = (FLUORESCENCE, "ND2 modality flags " + ", ".join(flags))
    elif hint is None and any("brightfield" in x for x in flags):
        hint = (BRIGHTFIELD, "ND2 modality flags " + ", ".join(flags))
    return hint


def _channels(meta_channels: list[Any], count: int, rgb: bool) -> list[ChannelInfo]:
    if rgb:
        return rgb_channels()
    if len(meta_channels) != count:
        return [ChannelInfo(name=f"Channel {i}") for i in range(count)]
    channels = []
    for i, channel in enumerate(meta_channels):
        meta = channel.channel
        color = getattr(meta, "color", None)
        # nd2 exposes the colour as an object with r, g, b attributes.
        rgb_values = (
            (color.r, color.g, color.b)
            if color is not None and hasattr(color, "r")
            else None
        )
        channels.append(
            ChannelInfo(
                name=meta.name or f"Channel {i}",
                color=color_hex(rgb_values),
                excitation_nm=meta.excitationLambdaNm or None,
                emission_nm=meta.emissionLambdaNm or None,
            )
        )
    return channels
