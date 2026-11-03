"""Tiled OME-TIFF pyramid: level 0 staged in zarr, 2x2 area levels, SubIFDs, lossless codecs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
import hashlib
import json
import os
import shutil

import dask.array as da
import numpy as np
import tifffile
import zarr

import focus.preprocessing.microscopy.technology as tech
from focus.io.provenance import (
    PIXEL_DIGEST_KEY,
    SUPERSEDED_SIGNATURE_KEY,
    planes_digest,
    read_ome_annotations,
    stat_signature,
)


# Tiles compressed per batch by tifffile's thread pool; bounds the writer's memory.
_WRITE_BUFFER = 64 * 2**20


def stage_levels(plane: da.Array, rgb: bool, workdir: str) -> list[zarr.Array[Any]]:
    """Write ``plane`` (Y, X, C) and its 2x2 area-averaged levels to zarr arrays in ``workdir``.

    RGB levels are stored ``(Y, X, 3)``; other levels ``(C, Y, X)``, one plane per channel, the
    layout of the output pages. Each level halves both sides (floor) until the image fits in one
    tile. Integer levels round the average to the nearest value.
    """
    array = plane if rgb else da.moveaxis(plane, -1, 0)
    levels = [_store(array, rgb, os.path.join(workdir, "0.zarr"))]
    while max(_hw(levels[-1], rgb)) > tech.TILE:
        previous = da.from_zarr(levels[-1])  # type: ignore[no-untyped-call]
        h, w = _hw(levels[-1], rgb)
        axes = (0, 1) if rgb else (1, 2)
        factors = {axes[0]: 2 if h > 1 else 1, axes[1]: 2 if w > 1 else 1}
        averaged = da.coarsen(
            np.mean, previous.astype(np.float32), factors, trim_excess=True
        )
        if np.issubdtype(previous.dtype, np.integer):
            averaged = da.rint(averaged)
        array = averaged.astype(previous.dtype)
        levels.append(_store(array, rgb, os.path.join(workdir, f"{len(levels)}.zarr")))
    return levels


def _hw(level: zarr.Array[Any], rgb: bool) -> tuple[int, int]:
    return (level.shape[0], level.shape[1]) if rgb else (level.shape[1], level.shape[2])


def _store(array: da.Array, rgb: bool, path: str) -> zarr.Array[Any]:
    chunks = (
        (tech.TILE, tech.TILE, array.shape[-1]) if rgb else (1, tech.TILE, tech.TILE)
    )
    target = zarr.open_array(
        path, mode="w", shape=array.shape, chunks=chunks, dtype=array.dtype
    )
    da.store(array.rechunk(chunks), cast(Any, target), lock=False)  # type: ignore[no-untyped-call]
    return target


def level0_digest(level: zarr.Array[Any], rgb: bool) -> str:
    """``planes_digest`` of level 0 in page order, streamed in row bands of one tile height."""
    digest = hashlib.sha256()
    h, w = _hw(level, rgb)
    shape = [h, w, level.shape[2]] if rgb else [h, w]
    for c in range(1 if rgb else level.shape[0]):
        digest.update(json.dumps([np.dtype(level.dtype).str, shape]).encode())
        for y in range(0, h, tech.TILE):
            band = level[y : y + tech.TILE] if rgb else level[c, y : y + tech.TILE]
            digest.update(np.ascontiguousarray(band).data)
    return digest.hexdigest()


def _tiles(level: zarr.Array[Any], rgb: bool) -> Iterator[np.ndarray]:
    """Tiles of every page of ``level`` in TIFF order (page, then row-major tiles)."""
    t = tech.TILE
    h, w = _hw(level, rgb)
    for c in range(1 if rgb else level.shape[0]):
        for y in range(0, h, t):
            for x in range(0, w, t):
                yield np.asarray(
                    level[y : y + t, x : x + t]
                    if rgb
                    else level[c, y : y + t, x : x + t]
                )


def write_pyramid(
    levels: list[zarr.Array[Any]],
    path: str,
    rgb: bool,
    description: str,
    codec: tuple[str, dict[str, object], int | None],
    pixel_size: np.ndarray | None,
) -> None:
    """Write the staged levels as one OME-TIFF series: level-0 pages with SubIFD levels."""
    compression, args, predictor = codec
    h0, w0 = _hw(levels[0], rgb)
    with tifffile.TiffWriter(path, bigtiff=True) as tif:
        for i, level in enumerate(levels):
            h, w = _hw(level, rgb)
            options: dict[str, Any] = dict(
                shape=level.shape,
                dtype=level.dtype,
                tile=(tech.TILE, tech.TILE),
                photometric="rgb" if rgb else "minisblack",
                compression=compression,
                compressionargs=args,
                predictor=predictor,
                metadata=None,
                buffersize=_WRITE_BUFFER,
            )
            if pixel_size is not None:
                # TIFF resolution tags in pixels per centimetre, so non-OME readers keep the scale.
                options["resolution"] = (
                    1e4 / (pixel_size[0] * w0 / w),
                    1e4 / (pixel_size[1] * h0 / h),
                )
                options["resolutionunit"] = "CENTIMETER"
            if i == 0:
                # UTF-8 bytes, as tifffile's own OME writer stores them: Bio-Formats does not
                # resolve XML character references (the µ of µm) in the TIFF description.
                tif.write(
                    _tiles(level, rgb),
                    description=description.encode("utf-8"),
                    subifds=len(levels) - 1,
                    **options,
                )
            else:
                tif.write(_tiles(level, rgb), subfiletype=1, **options)


def remove_workdir(workdir: str) -> None:
    """Delete the zarr staging directory."""
    shutil.rmtree(workdir, ignore_errors=True)


def previous_output(output_file: str) -> tuple[str, list[str | int] | None] | None:
    """``(pixel digest, superseded signature)`` of an existing output, or None when there is none.

    An output written before FOCUS stored provenance carries no digest: its level-0 pixels are
    digested here, and the file itself (its stat signature) is what a pixel-identical regeneration
    supersedes. An annotated output passes its own superseded signature on.
    """
    if not os.path.exists(output_file):
        return None
    annotations = read_ome_annotations(output_file) or {}
    if PIXEL_DIGEST_KEY in annotations:
        superseded = annotations.get(SUPERSEDED_SIGNATURE_KEY)
        return annotations[PIXEL_DIGEST_KEY], (
            json.loads(superseded) if superseded else None
        )
    try:
        return planes_digest(_level0_planes(output_file)), stat_signature(output_file)
    except Exception:
        return None


def _level0_planes(path: str) -> list[np.ndarray]:
    """Level-0 planes of an output in page order: the leading pages that share the first size."""
    planes = []
    with tifffile.TiffFile(path) as tif:
        size = tif.pages[0].shape[:2]
        for page in tif.pages:
            if page.shape[:2] != size:
                break
            planes.append(page.asarray())
    return planes
