"""Display rendering stored beside the pixels: per-channel window, colour and default visibility."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

import focus.preprocessing.microscopy.technology as tech
from focus.preprocessing.microscopy.source import ChannelInfo

# Colours for channels without one in the metadata, in the ImageJ composite order.
_PALETTE = ("FF0000", "00FF00", "0000FF", "00FFFF", "FF00FF", "FFFF00", "FFFFFF")
# Nuclear counterstains are shown in blue whatever their position.
_NUCLEAR = ("dapi", "hoechst", "draq5", "nuclear")
# Emission wavelength (nm) upper bounds and their display colour. FOCUS design choice: the usual
# pseudo-colours of DAPI, FITC/AF488, Cy3/AF555, AF594 and Cy5/AF647.
_EMISSION_COLOURS = (
    (480, "0000FF"),
    (500, "00FFFF"),
    (550, "00FF00"),
    (590, "FFFF00"),
    (650, "FF0000"),
)
_FAR_RED = "FF00FF"
# Channels shown when the alignment view opens; the others are toggled on by the user.
DEFAULT_VISIBLE = 4


def channel_colours(channels: Sequence[ChannelInfo]) -> list[str]:
    """Colour per channel: metadata, else emission wavelength, else nuclear name, else palette."""
    colours: list[str | None] = []
    for channel in channels:
        colour = channel.color
        if colour is None and channel.emission_nm:
            colour = next(
                (c for limit, c in _EMISSION_COLOURS if channel.emission_nm < limit),
                _FAR_RED,
            )
        if colour is None and any(word in channel.name.lower() for word in _NUCLEAR):
            colour = "0000FF"
        colours.append(colour)
    palette = iter(c for c in _PALETTE * (len(channels) // len(_PALETTE) + 1))
    return [c if c is not None else next(palette) for c in colours]


def windows(
    canvas: np.ndarray, mask: np.ndarray, dtype: np.dtype, identity: bool
) -> list[tuple[float, float]]:
    """``(low, high)`` display window per channel in native values.

    ``identity`` (brightfield RGB) maps the full sample range. Otherwise 0.35% of the tissue
    pixels saturate at each end (all pixels when the mask is nearly empty).
    """
    dtype = np.dtype(dtype)
    channels = canvas.shape[-1]
    if identity and np.issubdtype(dtype, np.integer):
        top = float(np.iinfo(dtype).max)
        return [(0.0, top)] * channels
    values = (
        canvas[mask] if mask.sum() >= 0.01 * mask.size else canvas.reshape(-1, channels)
    )
    p = tech.DISPLAY_SATURATION_PERCENT
    low, high = np.percentile(values, (p, 100 - p), axis=0)
    out = []
    for lo, hi in zip(np.atleast_1d(low), np.atleast_1d(high)):
        if hi <= lo:
            hi = lo + 1.0
        out.append((float(lo), float(hi)))
    return out


def display_settings(
    channels: Sequence[ChannelInfo],
    window_values: Sequence[tuple[float, float]],
    rgb: bool,
) -> dict[str, object]:
    """JSON-ready rendering stored as ``focus_display``."""
    colours = channel_colours(channels)
    return {
        "mode": "rgb" if rgb else "composite",
        "channels": [
            {
                "name": channel.name,
                "color": colour,
                "window": [round(lo, 4), round(hi, 4)],
                "visible": rgb or i < DEFAULT_VISIBLE,
            }
            for i, (channel, colour, (lo, hi)) in enumerate(
                zip(channels, colours, window_values)
            )
        ],
    }
