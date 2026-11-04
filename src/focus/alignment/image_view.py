"""Display view of a preprocessed image: one stored level rendered with its saved windows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import numpy as np
import tifffile
from PIL import Image

from focus.io.ome import hw_from_axes, read_display_settings, resolve_pyramid_levels

# The display level is the largest stored level within this many pixels and this many bytes of
# native samples (all channels), so wide multiplexed panels load a coarser level.
DISPLAY_MAX_PIXELS = 3000 * 3000
DISPLAY_MAX_BYTES = 256 * 2**20


def display_level(levels: Sequence[object], dtype: np.dtype, channels: int) -> int:
    """Index of the largest level within the pixel and byte caps (the coarsest otherwise)."""
    budget = min(
        DISPLAY_MAX_PIXELS,
        DISPLAY_MAX_BYTES // max(1, channels * np.dtype(dtype).itemsize),
    )
    for index, level in enumerate(levels):
        h, w = hw_from_axes(level.shape, getattr(level, "axes", ""))  # type: ignore[attr-defined]
        if h * w <= budget:
            return index
    return len(levels) - 1


def _hwc(data: np.ndarray, axes: str) -> np.ndarray:
    """``(H, W, C)`` view of a level read with its tifffile axes."""
    axes = axes.upper()
    if data.ndim == 2:
        return data[..., None]
    if len(axes) == 3 and axes[0] in "CSIQ":
        return np.moveaxis(data, 0, -1)
    return data


class ImageView:
    """The display level of a FOCUS image and the rendering stored with it (``focus_display``)."""

    def __init__(self, data: np.ndarray, settings: dict[str, object]) -> None:
        """Retain ``(H, W, C)`` native samples and the per-channel windows and colours."""
        self.data = data
        self.rgb = settings.get("mode") == "rgb"
        self.channels: list[dict[str, object]] = list(settings.get("channels") or [])  # type: ignore[call-overload]

    @property
    def visible(self) -> list[int]:
        """Channels shown by default."""
        return [i for i, ch in enumerate(self.channels) if ch.get("visible")]

    def metadata(self) -> list[dict[str, object]]:
        """Channel list for the GUI (composite images only; an RGB image has no toggles)."""
        if self.rgb:
            return []
        return [
            {
                "name": ch["name"],
                "color": ch["color"],
                "visible": bool(ch.get("visible")),
            }
            for ch in self.channels
        ]

    def _scaled(self, index: int) -> np.ndarray:
        """Channel ``index`` scaled to [0, 1] by its display window."""
        window = cast(Sequence[float], self.channels[index]["window"])
        low, high = float(window[0]), float(window[1])
        # NaN marks pixels no acquisition measured (Raman mosaics): shown black.
        plane = np.nan_to_num(self.data[..., index].astype(np.float32), nan=low)
        return cast(
            np.ndarray, np.clip((plane - low) / max(high - low, 1e-12), 0.0, 1.0)
        )

    def render(self, shown: Sequence[int] | None = None) -> np.ndarray:
        """RGB uint8 composite: each shown channel scaled by its window, tinted and added."""
        h, w = self.data.shape[:2]
        if self.rgb:
            out = np.stack([self._scaled(i) for i in range(3)], axis=-1)
            return cast(np.ndarray, np.rint(out * 255).astype(np.uint8))
        indices = (
            self.visible
            if shown is None
            else [i for i in shown if 0 <= i < len(self.channels)]
        )
        out = np.zeros((h, w, 3), dtype=np.float32)
        for i in indices:
            colour = str(self.channels[i]["color"])
            tint = np.array(
                [int(colour[k : k + 2], 16) / 255 for k in (0, 2, 4)], np.float32
            )
            out += self._scaled(i)[..., None] * tint
        return cast(np.ndarray, np.rint(np.clip(out, 0, 1) * 255).astype(np.uint8))

    def image(self, shown: Sequence[int] | None = None) -> Image.Image:
        """The composite as a PIL image for the PNG payload."""
        return Image.fromarray(self.render(shown))


def load_view(
    filename: str,
) -> tuple[ImageView | None, np.ndarray, str, tuple[int, int], tuple[int, int]]:
    """``(view or None, display-level data, axes, display (H, W), full (H, W))`` of an OME-TIFF.

    The view exists only for files carrying ``focus_display``; other files keep the legacy
    min-max conversion of the returned data.
    """
    settings = read_display_settings(filename)
    with tifffile.TiffFile(filename) as tif:
        levels = resolve_pyramid_levels(tif)
        full = hw_from_axes(levels[0].shape, getattr(levels[0], "axes", ""))
        dtype = np.dtype(getattr(levels[0], "dtype", np.uint8))
        channels = len(settings["channels"]) if settings else 3  # type: ignore[arg-type]
        level = levels[display_level(levels, dtype, channels)]
        axes = str(getattr(level, "axes", "") or "")
        data = level.asarray()
    shape = hw_from_axes(data.shape, axes)
    if settings is None:
        return None, data, axes, shape, full
    return ImageView(_hwc(data, axes), settings), data, axes, shape, full
