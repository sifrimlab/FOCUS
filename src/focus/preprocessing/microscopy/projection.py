"""Reduce time and depth to one plane and build bounded low-resolution canvases of it."""

from __future__ import annotations

import math
from typing import cast

import cv2
import dask.array as da
import numpy as np

import focus.preprocessing.microscopy.technology as tech
from focus.config.options import MicroscopyZProjection
from focus.preprocessing.microscopy.source import SourceImage

# Best-focus search runs on a canvas of at most this many pixels per plane.
_FOCUS_MAX_PIXELS = 2048 * 2048


def project(level: da.Array, rule: str, z_index: int | None) -> da.Array:
    """``(Y, X, C)`` plane of a ``(T, Z, Y, X, C)`` array: T=0, Z by ``rule``."""
    stack = level[0]
    if stack.shape[0] == 1:
        return cast(da.Array, stack[0])
    if rule == MicroscopyZProjection.MAX:
        return cast(da.Array, stack.max(axis=0))
    return cast(
        da.Array, stack[int(z_index if z_index is not None else stack.shape[0] // 2)]
    )


def plane_canvas(source: SourceImage, factor: float, select) -> np.ndarray:  # type: ignore[no-untyped-def]
    """Float32 ``(h, w, C)`` canvas of ``select(level)`` at ``factor`` level-0 pixels per pixel.

    The finest stored level that is not finer than needed is read; the remaining reduction is an
    area average, done chunk-wise on the lazy array (integer part) and with ``cv2.INTER_AREA``
    (fractional part), so memory stays bounded by the canvas and the source chunks.
    """
    h0, w0 = source.shape_yx
    target = (max(1, round(h0 / factor)), max(1, round(w0 / factor)))
    downsample = [h0 / level.shape[2] for level in source.levels]
    index = max(
        (i for i, d in enumerate(downsample) if d <= factor + 1e-9),
        key=lambda i: downsample[i],
    )
    plane = select(source.levels[index]).astype(np.float32)
    # A power of two divides the tile and chunk sizes of nearly every source, so the chunk-wise
    # average needs no rechunk (a rechunk across tile boundaries holds many tiles in memory).
    step = 2 ** int(math.floor(math.log2(max(1.0, factor / downsample[index]))))
    if step > 1:
        plane = da.coarsen(np.mean, plane, {0: step, 1: step}, trim_excess=True)
    canvas = np.asarray(plane.compute(), dtype=np.float32)
    if canvas.shape[:2] != target:
        canvas = _resize(canvas, target)
    return canvas


def _resize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Area-average resize of an (h, w, C) float32 array to ``size`` (rows, columns)."""
    channels = [
        cv2.resize(image[..., c], (size[1], size[0]), interpolation=cv2.INTER_AREA)
        for c in range(image.shape[-1])
    ]
    return np.stack(channels, axis=-1)


def detection_factor(shape_yx: tuple[int, int], pixel_um: float | None) -> float:
    """Level-0 pixels per detection-canvas pixel (at least 1).

    About ``DETECTION_UM_PER_PX`` per pixel, with at least ``DETECTION_MIN_SIDE_PX`` on the short
    side and at most ``DETECTION_MAX_PIXELS``; without a pixel size only the pixel cap applies.
    """
    h, w = shape_yx
    factor = 1.0
    if pixel_um:
        factor = min(
            tech.DETECTION_UM_PER_PX / pixel_um, min(h, w) / tech.DETECTION_MIN_SIDE_PX
        )
    return max(1.0, factor, math.sqrt(h * w / tech.DETECTION_MAX_PIXELS))


def best_focus_index(source: SourceImage) -> int:
    """Z plane with the highest variance of the Laplacian (Pertuz et al. 2013)."""
    h, w = source.shape_yx
    factor = max(1.0, math.sqrt(h * w / _FOCUS_MAX_PIXELS))
    depth = int(source.data.shape[1])
    scores = []
    for z in range(depth):
        canvas = plane_canvas(source, factor, lambda level, z=z: level[0, z])
        gray = canvas.mean(axis=-1)
        scores.append(float(cv2.Laplacian(gray, cv2.CV_32F).var()))
    return int(np.argmax(scores))


def resolve_planes(source: SourceImage, rule: str) -> tuple[int | None, str]:
    """Z index for ``rule`` (None for the maximum projection) and a report line."""
    t, z = int(source.data.shape[0]), int(source.data.shape[1])
    parts = []
    if t > 1:
        parts.append(f"{t} time points: T=0 is used")
    index: int | None = None
    if z > 1:
        if rule == MicroscopyZProjection.MAX:
            parts.append(f"{z} Z planes: maximum-intensity projection")
        elif rule == MicroscopyZProjection.BEST_FOCUS:
            index = best_focus_index(source)
            parts.append(f"{z} Z planes: best-focus plane Z={index}")
        else:
            index = z // 2
            parts.append(f"{z} Z planes: middle plane Z={index}")
    return index, "; ".join(parts)
