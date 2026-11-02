"""Sparse membership and weights for spot and pixel footprints in the anchor frame."""

from typing import cast
from focus.spatial.matrices import empty_csr

import numpy as np
import scipy.sparse
from scipy.spatial import cKDTree

from focus.spatial.footprint import AnchorGeometry
from focus.spatial.footprint_shape import pixel_centres

_BLOCK_PAIRS = 4_000_000  # candidate (anchor, element) pairs evaluated at once


# --------------------------------------------------------------------------- membership


def _weights(geom: AnchorGeometry, d2: np.ndarray, weights: str) -> np.ndarray:
    """Compute the configured weights from normalized footprint distances."""
    if weights == "ones":
        return np.ones(len(d2), dtype=np.float32)
    return np.exp(-d2 / (2.0 * geom.shape.sigma**2)).astype(np.float32)


def footprint_membership(
    geom: AnchorGeometry, target_xy: np.ndarray, weights: str = "gaussian"
) -> scipy.sparse.csr_matrix:
    """
    ``(N_anchor, N_target)`` sparse operator of the target points inside each footprint.

    Candidates are found in the target frame with a KD-tree (radius covering the whole mapped
    footprint); membership and distance are decided in the anchor frame. ``weights`` is
    ``"gaussian"`` (unnormalised Gaussian of the anchor-frame distance) or ``"ones"``.
    Anchors with no member are all-zero rows.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    n_anchor, n_target = len(geom.centres), len(target_xy)
    finite = np.flatnonzero(np.isfinite(target_xy).all(axis=1))
    radii = geom.search_radii()
    usable = np.flatnonzero(geom.valid & np.isfinite(radii))
    if finite.size == 0 or usable.size == 0:
        return empty_csr((n_anchor, n_target), dtype=np.float32)

    tree = cKDTree(target_xy[finite])
    centres = geom.target_centres()
    rows_out, cols_out, data_out = [], [], []
    block = 2048
    for start in range(0, usable.size, block):
        sel = usable[start : start + block]
        hits = [
            np.asarray(h, dtype=np.intp)
            for h in tree.query_ball_point(centres[sel], r=radii[sel], workers=-1)
        ]
        counts = np.fromiter((h.size for h in hits), dtype=np.intp, count=len(hits))
        if counts.sum() == 0:
            continue
        rows = np.repeat(sel, counts)
        cols = finite[np.concatenate(hits)]
        inside, d2 = geom.contains(rows, target_xy[cols])
        rows_out.append(rows[inside])
        cols_out.append(cols[inside])
        data_out.append(_weights(geom, d2[inside], weights))

    if not rows_out:
        return empty_csr((n_anchor, n_target), dtype=np.float32)
    return scipy.sparse.csr_matrix(
        (
            np.concatenate(data_out),
            (np.concatenate(rows_out), np.concatenate(cols_out)),
        ),
        shape=(n_anchor, n_target),
        dtype=np.float32,
    )


def pixel_footprint_membership(
    geom: AnchorGeometry,
    crop: tuple[int, int, int, int],
    weights: str = "gaussian",
) -> scipy.sparse.csr_matrix:
    """
    ``(N_anchor, crop_h * crop_w)`` operator over the pixels of an image crop.

    ``crop`` is the half-open ``(c0, r0, c1, r1)`` of the loaded pixels in full-image
    coordinates; columns index the crop row-major (``(row - r0) * crop_w + (col - c0)``).
    Pixel points are their centres. Candidates are each footprint's pixel bounding box.
    """
    c0, r0, c1, r1 = crop
    crop_w, crop_h = c1 - c0, r1 - r0
    n_anchor = len(geom.centres)
    boxes = geom.pixel_bboxes(r1, c1)
    boxes[:, 0] = np.maximum(boxes[:, 0], c0)
    boxes[:, 1] = np.maximum(boxes[:, 1], r0)
    boxes[:, 2] = np.maximum(boxes[:, 2], boxes[:, 0])
    boxes[:, 3] = np.maximum(boxes[:, 3], boxes[:, 1])
    widths = boxes[:, 2] - boxes[:, 0]
    counts = widths * (boxes[:, 3] - boxes[:, 1])
    anchors = np.flatnonzero(counts > 0)

    rows_out, cols_out, data_out = [], [], []
    start = 0
    while start < anchors.size:
        # Grow the block until it holds about _BLOCK_PAIRS candidate pixels.
        cum = np.cumsum(counts[anchors[start:]])
        stop = start + max(1, int(np.searchsorted(cum, _BLOCK_PAIRS, side="right")))
        sel = anchors[start:stop]
        start = stop
        n = counts[sel]
        rows = np.repeat(sel, n)
        offset = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        w = np.repeat(widths[sel], n)
        cols = np.repeat(boxes[sel, 0], n) + offset % w
        rws = np.repeat(boxes[sel, 1], n) + offset // w
        inside, d2 = geom.contains(rows, pixel_centres(cols, rws))
        rows_out.append(rows[inside])
        cols_out.append((rws[inside] - r0) * crop_w + (cols[inside] - c0))
        data_out.append(_weights(geom, d2[inside], weights))

    shape = (n_anchor, crop_h * crop_w)
    if not rows_out:
        return empty_csr(shape, dtype=np.float32)
    return scipy.sparse.csr_matrix(
        (
            np.concatenate(data_out),
            (np.concatenate(rows_out), np.concatenate(cols_out)),
        ),
        shape=shape,
        dtype=np.float32,
    )


def row_normalize(matrix: scipy.sparse.csr_matrix) -> scipy.sparse.csr_matrix:
    """Scale every non-empty row to sum 1 (empty rows stay zero)."""
    sums = np.asarray(matrix.sum(axis=1)).ravel()
    inv = np.zeros_like(sums, dtype=np.float64)
    inv[sums > 0] = 1.0 / sums[sums > 0]
    return cast(
        scipy.sparse.csr_matrix,
        (scipy.sparse.diags(inv.astype(np.float32)) @ matrix).tocsr(),
    )
