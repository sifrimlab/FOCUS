"""Tissue detection on the low-resolution canvas: saturation for brightfield, intensity for fluorescence."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
import skimage.filters as filters
import skimage.morphology as morphology
from scipy.ndimage import binary_fill_holes

import focus.preprocessing.microscopy.technology as tech
from focus.runtime.reporter_context import get_reporter


def to_unit(canvas: np.ndarray, dtype: np.dtype) -> np.ndarray:
    """Canvas values scaled to [0, 1] by the sample type's range (floats by their own maximum)."""
    dtype = np.dtype(dtype)
    if np.issubdtype(dtype, np.integer):
        return canvas / float(np.iinfo(dtype).max)
    peak = float(canvas.max()) if canvas.size else 0.0
    return canvas / peak if peak > 0 else canvas


def has_dark_background(gray: np.ndarray) -> bool:
    """True when the border frame (outer 5%) is darker than the image and than its bright content.

    A scan filled edge to edge with tissue gives equal medians and reports False.
    """
    h, w = gray.shape[:2]
    k = max(1, round(0.05 * min(h, w)))
    border = np.concatenate(
        [gray[:k].ravel(), gray[-k:].ravel(), gray[:, :k].ravel(), gray[:, -k:].ravel()]
    )
    border_level = float(np.median(border))
    return border_level < float(np.median(gray)) and border_level < (
        tech.DARK_BORDER_RATIO * float(np.percentile(gray, 99))
    )


def _border_padding(rgb8: np.ndarray) -> np.ndarray:
    """Near-black regions (all channels at most BLACK_LEVEL) connected to the image border."""
    near_black = np.all(rgb8 <= tech.BLACK_LEVEL, axis=-1)
    if not near_black.any():
        return cast(np.ndarray, near_black)
    _, labels = cv2.connectedComponents(near_black.astype(np.uint8), connectivity=8)
    border = np.unique(
        np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    )
    return cast(np.ndarray, np.isin(labels, border[border != 0]))


def _odd(pixels: float) -> int:
    return max(3, int(round(pixels)) | 1)


def brightfield_signal(unit: np.ndarray, um_per_px: float) -> tuple[np.ndarray, int]:
    """8-bit tissue signal of a brightfield canvas and the threshold that separates tissue.

    RGB: HSV saturation, median-blurred, Otsu (CLAM). Stain is saturated, glass and padding are
    not; border-connected near-black padding is set to 0 because compression noise in it has a
    high saturation. One channel: inverted intensity with Otsu.
    """
    rgb8 = np.clip(np.rint(unit * 255), 0, 255).astype(np.uint8)
    if rgb8.shape[-1] >= 3:
        signal = cv2.cvtColor(np.ascontiguousarray(rgb8[..., :3]), cv2.COLOR_RGB2HSV)[
            ..., 1
        ]
        signal[_border_padding(rgb8[..., :3])] = 0
    else:
        gray = rgb8[..., 0].copy()
        gray[_border_padding(rgb8[..., :1])] = 255
        signal = cv2.bitwise_not(gray)
    blurred = cv2.medianBlur(signal, _odd(tech.MEDIAN_BLUR_UM / um_per_px))
    threshold, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return blurred, int(threshold)


def fluorescence_signal(
    canvas: np.ndarray, um_per_px: float
) -> tuple[np.ndarray, float]:
    """Tissue signal of a fluorescence canvas and its triangle threshold.

    Each channel is scaled to its own 99.9th percentile, so a dim stain counts as much as a bright
    one; the channel maximum is smoothed and thresholded with the triangle method, which suits a
    histogram dominated by a dark background peak (Zack et al. 1977).
    """
    peaks = np.percentile(
        canvas.reshape(-1, canvas.shape[-1]),
        tech.FLUORESCENCE_CHANNEL_PERCENTILE,
        axis=0,
    )
    peaks[peaks <= 0] = 1.0
    signal = np.clip(canvas / peaks, 0, 1).max(axis=-1).astype(np.float32)
    sigma = tech.FLUORESCENCE_SMOOTH_UM / um_per_px
    signal = cv2.GaussianBlur(signal, (0, 0), sigmaX=max(0.5, sigma))
    threshold = filters.threshold_triangle(signal, nbins=256)  # type: ignore[no-untyped-call]
    return signal, float(threshold)


def clean_mask(raw: np.ndarray, um_per_px: float, pixel_known: bool) -> np.ndarray:
    """Close gaps, drop specks, fill holes and keep the pieces above the debris area."""
    radius = max(1, int(round(tech.CLOSING_UM / um_per_px / 2)))
    mask = cv2.morphologyEx(
        raw.astype(np.uint8),
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1,) * 2),
    ).astype(bool)
    mask = morphology.remove_small_objects(mask, max_size=tech.SPECK_PIXELS)
    mask = binary_fill_holes(mask)
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return cast(np.ndarray, mask)
    areas = [cv2.contourArea(c) for c in contours]
    if pixel_known:
        minimum = tech.MIN_TISSUE_AREA_UM2 / um_per_px**2
    else:
        minimum = tech.MIN_TISSUE_FRACTION_NO_SIZE * mask.size
    threshold = min(minimum, tech.LARGEST_FRACTION * max(areas))
    kept = [c for c, a in zip(contours, areas) if a >= threshold]
    out = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(out, kept, contourIdx=-1, color=1, thickness=cv2.FILLED)
    return out.astype(bool)


def detect_tissue(
    canvas: np.ndarray,
    dtype: np.dtype,
    brightfield: bool,
    um_per_px: float | None,
) -> np.ndarray:
    """Boolean tissue mask of a float32 canvas in native values.

    ``um_per_px`` is the canvas pixel size; without one the kernels assume the 8 µm/px design
    resolution and the debris threshold is a fraction of the canvas.
    """
    scale = um_per_px or tech.DETECTION_UM_PER_PX
    if brightfield:
        signal, threshold = brightfield_signal(to_unit(canvas, dtype), scale)
        raw = signal > threshold
    else:
        signal, cut = fluorescence_signal(canvas, scale)
        raw = signal > cut
    mask = clean_mask(raw, scale, um_per_px is not None)
    if not mask.any():
        get_reporter().warning("No tissue detected: the whole image is kept as tissue")
        mask = np.ones(canvas.shape[:2], dtype=bool)
    return mask


def contours_level0(
    mask: np.ndarray, factor: float, offset: tuple[int, int]
) -> list[np.ndarray]:
    """External tissue outlines as ``(n, 2)`` (x, y) level-0 coordinates of the output image.

    Outlines are simplified to one canvas pixel, then scaled by ``factor`` and shifted by the crop
    ``offset`` so they match the written pixels.
    """
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    out = []
    for contour in contours:
        simplified = (
            cv2.approxPolyDP(contour, 1.0, True).reshape(-1, 2).astype(np.float64)
        )
        if len(simplified) < 3:
            continue
        points = (simplified + 0.5) * factor - np.asarray(offset, dtype=np.float64)
        out.append(np.round(points, 1))
    return out
