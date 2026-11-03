"""Opt-in crop to the tissue bounding box and background fill, applied lazily to level 0."""

from __future__ import annotations

from typing import cast

import dask.array as da
import numpy as np

import focus.preprocessing.microscopy.technology as tech


def margin_px(pixel_um: float | None) -> int:
    """Crop margin in level-0 pixels: 100 µm, or 250 px without a pixel size."""
    if pixel_um:
        return int(round(tech.CROP_MARGIN_UM / pixel_um))
    return tech.CROP_MARGIN_PX


def crop_box(
    mask: np.ndarray, factor: float, shape_yx: tuple[int, int], margin: int
) -> tuple[int, int, int, int]:
    """``(x0, y0, x1, y1)`` (end exclusive) of the tissue bounding box plus ``margin``, clamped.

    Canvas pixel ``i`` covers level-0 pixels ``[i * factor, (i + 1) * factor)``.
    """
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    h, w = shape_yx
    if rows.size == 0:
        return 0, 0, w, h
    y0 = max(0, int(np.floor(rows[0] * factor)) - margin)
    y1 = min(h, int(np.ceil((rows[-1] + 1) * factor)) + margin)
    x0 = max(0, int(np.floor(cols[0] * factor)) - margin)
    x1 = min(w, int(np.ceil((cols[-1] + 1) * factor)) + margin)
    return x0, y0, x1, y1


def mask_block(
    mask: np.ndarray, factor: float, rows: tuple[int, int], cols: tuple[int, int]
) -> np.ndarray:
    """Nearest-neighbour level-0 view of the canvas mask over ``rows`` x ``cols`` (end exclusive)."""
    r = np.minimum((np.arange(*rows) / factor).astype(np.int64), mask.shape[0] - 1)
    c = np.minimum((np.arange(*cols) / factor).astype(np.int64), mask.shape[1] - 1)
    return mask[np.ix_(r, c)]


def fill_background(
    plane: da.Array,
    mask: np.ndarray,
    factor: float,
    offset: tuple[int, int],
    value: float,
) -> da.Array:
    """``plane`` (Y, X, C) with every non-tissue pixel set to ``value``, block by block."""
    fill = np.asarray(value).astype(plane.dtype)

    def apply(block: np.ndarray, block_info: dict | None = None) -> np.ndarray:  # type: ignore[type-arg]
        (y0, y1), (x0, x1), _ = block_info[0]["array-location"]  # type: ignore[index]
        tissue = mask_block(
            mask,
            factor,
            (y0 + offset[1], y1 + offset[1]),
            (x0 + offset[0], x1 + offset[0]),
        )
        out = block.copy()
        out[~tissue] = fill
        return out

    blocks = da.map_blocks(apply, plane, dtype=plane.dtype)  # type: ignore[no-untyped-call]
    return cast(da.Array, blocks)
