"""Resolve ``image_type``: configuration, then acquisition metadata, then layout, then polarity."""

from __future__ import annotations

import numpy as np

from focus.config.options import MicroscopyImageType
from focus.preprocessing.microscopy.projection import detection_factor, plane_canvas
from focus.preprocessing.microscopy.source import SourceImage
from focus.preprocessing.microscopy.tissue import has_dark_background, to_unit


def resolve_image_type(
    source: SourceImage, requested: str, pixel_um: float | None
) -> tuple[str, str, bool]:
    """``(image type, evidence, inferred)``; ``inferred`` marks the polarity fallback.

    Order: the configured value; the acquisition metadata (OME acquisition mode, contrast method
    or illumination, CZI/ND2 channel metadata, whole-slide scanner formats); the layout (several
    non-RGB channels are fluorescence); the background polarity of a low-resolution view.
    """
    hint = source.type_hint
    if requested != MicroscopyImageType.AUTO:
        evidence = "set in the configuration"
        if hint is not None and hint[0] != requested:
            evidence += f" (the metadata indicates {hint[0]}: {hint[1]})"
        return requested, evidence, False
    if hint is not None:
        return hint[0], hint[1], False
    channels = int(source.data.shape[-1])
    if not source.rgb and channels >= 2:
        return MicroscopyImageType.FLUORESCENCE, f"{channels} separate channels", False
    dark = _dark_background(source, pixel_um)
    layout = "RGB image" if source.rgb else "single-channel image"
    if dark:
        return (
            MicroscopyImageType.FLUORESCENCE,
            f"{layout} with a dark background",
            True,
        )
    return MicroscopyImageType.BRIGHTFIELD, f"{layout} with a bright background", True


def _dark_background(source: SourceImage, pixel_um: float | None) -> bool:
    """Polarity probe on the middle plane of a low-resolution view."""
    factor = detection_factor(source.shape_yx, pixel_um)
    middle = int(source.data.shape[1]) // 2
    canvas = plane_canvas(source, factor, lambda level: level[0, middle])
    luma = to_unit(canvas, source.dtype).mean(axis=-1)
    return has_dark_background(np.asarray(luma, dtype=np.float32))
