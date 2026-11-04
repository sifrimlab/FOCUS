"""Pairwise overlap of anchor and target spot footprints (areal interpolation weights).

The overlap area of anchor ``i`` and target spot ``j`` is estimated by sampling the smaller of the
two footprints on a stratified grid and finding which footprints of the other set contain each
point. The result does not depend on which set is larger: an anchor inside one target spot gets that
spot, an anchor covering many target spots gets all of them in proportion to their overlap.

Areas are in target-frame units. The anchor footprint is mapped through the local Jacobian of the
alignment transform, so the two areas are comparable whenever both coordinate systems are in µm.
"""

from dataclasses import dataclass
from typing import cast

import numpy as np
import scipy.sparse as sp
from scipy.spatial import cKDTree

from focus.spatial.footprint import AnchorGeometry
from focus.spatial.footprint_shape import FootprintShape
from focus.spatial.matrices import empty_csr
from focus.spatial.membership import footprint_membership

# Sample points evaluated at once.
_BLOCK_POINTS = 1_000_000

# Total sample points one overlap computation may evaluate, and the finest grid per footprint.
_POINT_BUDGET = 10_000_000
_MAX_PER_AXIS = 32


def footprint_area(shape: FootprintShape) -> float:
    """Area of one footprint of ``shape``, in the units of its frame."""
    hx, hy = (float(v) for v in shape.half)
    return float(np.pi * hx * hy) if shape.kind == "ellipse" else 4.0 * hx * hy


def samples_per_axis(n_footprints: int) -> int:
    """Grid points per axis: as fine as the point budget allows for ``n_footprints`` sampled footprints."""
    return int(np.clip(np.sqrt(_POINT_BUDGET / max(n_footprints, 1)), 1, _MAX_PER_AXIS))


def unit_samples(kind: str, per_axis: int) -> np.ndarray:
    """Cell-centred stratified points of the footprint's unit square (or disc), ``(K, 2)`` in [-1, 1]."""
    axis = (np.arange(per_axis) + 0.5) / per_axis * 2.0 - 1.0
    gx, gy = np.meshgrid(axis, axis)
    points = np.column_stack([gx.ravel(), gy.ravel()])
    if kind == "ellipse":
        points = points[(points**2).sum(axis=1) <= 1.0]
    return points


def _offsets(shape: FootprintShape, unit: np.ndarray) -> np.ndarray:
    """``(K, 2)`` sample offsets from a footprint centre, scaled and rotated to ``shape``."""
    c, s = np.cos(shape.angle), np.sin(shape.angle)
    return np.asarray((unit * shape.half) @ np.array([[c, s], [-s, c]]))


def _inside(
    points: np.ndarray, centres: np.ndarray, shape: FootprintShape
) -> np.ndarray:
    """Whether ``points[k]`` lies in the footprint of ``shape`` centred at ``centres[k]`` (upper edges open)."""
    c, s = np.cos(shape.angle), np.sin(shape.angle)
    u = (points - centres) @ np.array([[c, -s], [s, c]])
    h = shape.half
    if shape.kind == "ellipse":
        return np.asarray(((u / h) ** 2).sum(axis=1) < 1.0)
    return np.asarray(((u >= -h) & (u < h)).all(axis=1))


def _csr(
    rows: list[np.ndarray], cols: list[np.ndarray], shape: tuple[int, int]
) -> sp.csr_matrix:
    """Count matrix of the ``(row, col)`` pairs (repeated pairs add up)."""
    if not rows:
        return empty_csr(shape, dtype=np.float32)
    r, c = np.concatenate(rows), np.concatenate(cols)
    return sp.csr_matrix(
        (np.ones(len(r), dtype=np.float32), (r, c)), shape=shape, dtype=np.float32
    )


def _anchor_fractions(
    geom: AnchorGeometry,
    target_xy: np.ndarray,
    target_shape: FootprintShape,
    per_axis: int,
) -> sp.csr_matrix:
    """``(N_anchor, N_target)`` share of each anchor footprint lying in each target footprint."""
    n_anchor, n_target = len(geom.centres), len(target_xy)
    unit = unit_samples(geom.shape.kind, per_axis)
    k = len(unit)
    offsets = _offsets(geom.shape, unit)
    finite = np.flatnonzero(np.isfinite(target_xy).all(axis=1))
    anchors = np.flatnonzero(geom.valid)
    if finite.size == 0 or anchors.size == 0:
        return empty_csr((n_anchor, n_target), dtype=np.float32)
    tree = cKDTree(target_xy[finite])
    reach = float(np.hypot(*target_shape.half)) * (1.0 + 1e-9)
    rows_out: list[np.ndarray] = []
    cols_out: list[np.ndarray] = []
    step = max(1, _BLOCK_POINTS // k)
    for start in range(0, anchors.size, step):
        sel = anchors[start : start + step]
        points = (geom.centres[sel][:, None, :] + offsets[None]).reshape(-1, 2)
        mapped = geom.transform.to_target(points, np.repeat(sel, k))
        usable = np.flatnonzero(np.isfinite(mapped).all(axis=1))
        hits = tree.query_ball_point(mapped[usable], r=reach, workers=-1)
        counts = np.fromiter((len(h) for h in hits), dtype=np.intp, count=len(hits))
        if counts.sum() == 0:
            continue
        point = np.repeat(usable, counts)
        target = finite[
            np.concatenate([np.asarray(h, dtype=np.intp) for h in hits if len(h)])
        ]
        inside = _inside(mapped[point], target_xy[target], target_shape)
        rows_out.append(sel[point[inside] // k])
        cols_out.append(target[inside])
    return _csr(rows_out, cols_out, (n_anchor, n_target)) / np.float32(k)


def _target_fractions(
    geom: AnchorGeometry,
    target_xy: np.ndarray,
    target_shape: FootprintShape,
    per_axis: int,
) -> sp.csr_matrix:
    """``(N_anchor, N_target)`` share of each target footprint lying in each anchor footprint."""
    n_anchor, n_target = len(geom.centres), len(target_xy)
    unit = unit_samples(target_shape.kind, per_axis)
    k = len(unit)
    offsets = _offsets(target_shape, unit)
    finite = np.flatnonzero(np.isfinite(target_xy).all(axis=1))
    rows_out: list[np.ndarray] = []
    cols_out: list[np.ndarray] = []
    step = max(1, _BLOCK_POINTS // k)
    for start in range(0, finite.size, step):
        sel = finite[start : start + step]
        points = (target_xy[sel][:, None, :] + offsets[None]).reshape(-1, 2)
        member = footprint_membership(geom, points, "ones", closed=False).tocoo()
        rows_out.append(member.row.astype(np.intp))
        cols_out.append(sel[member.col // k])
    if not rows_out:
        return empty_csr((n_anchor, n_target), dtype=np.float32)
    return _csr(rows_out, cols_out, (n_anchor, n_target)) / np.float32(k)


@dataclass(frozen=True)
class FootprintOverlap:
    """Overlap areas of every anchor footprint with every target footprint.

    ``area`` is ``(N_anchor, N_target)`` in target-frame units. ``anchor_area`` is each anchor's
    footprint area in the same units (0 for anchors that cannot be placed) and ``target_area`` the
    footprint area of one target spot. ``sampled`` names the set whose footprints were sampled
    (``anchor`` or ``target``) and ``per_axis`` the sample grid.
    """

    area: sp.csr_matrix
    anchor_area: np.ndarray
    target_area: float
    sampled: str
    per_axis: int

    def coverage(self) -> np.ndarray:
        """Fraction of each anchor footprint covered by target spots, in [0, 1]."""
        total = np.asarray(self.area.sum(axis=1)).ravel()
        out = np.divide(
            total,
            self.anchor_area,
            out=np.zeros_like(total, dtype=np.float64),
            where=self.anchor_area > 0,
        )
        return cast(np.ndarray, np.minimum(out, 1.0))

    def mean_weights(self) -> sp.csr_matrix:
        """Overlap normalised per anchor: the weights of an area-weighted mean (rows sum to 1 or 0)."""
        total = np.asarray(self.area.sum(axis=1)).ravel()
        inverse = np.divide(
            1.0, total, out=np.zeros_like(total, dtype=np.float64), where=total > 0
        )
        return sp.csr_matrix(sp.diags(inverse.astype(np.float32)) @ self.area)

    def share_weights(self) -> sp.csr_matrix:
        """Fraction of each target spot lying in each anchor (the apportioning of an extensive quantity)."""
        return sp.csr_matrix(self.area / np.float32(self.target_area))


def anchor_areas(geom: AnchorGeometry) -> np.ndarray:
    """Anchor footprint area in target-frame units per anchor (0 where the anchor cannot be placed)."""
    out = np.zeros(len(geom.centres), dtype=np.float64)
    rows = np.flatnonzero(geom.valid)
    if rows.size == 0:
        return out
    jacobian = geom.transform.jacobian(geom.centres[rows], rows)
    scale = np.abs(np.linalg.det(jacobian))
    area = footprint_area(geom.shape) * scale
    out[rows] = np.where(np.isfinite(area), area, 0.0)
    return out


def footprint_overlap(
    geom: AnchorGeometry, target_xy: np.ndarray, target_shape: FootprintShape
) -> FootprintOverlap:
    """Overlap of the anchor footprints of ``geom`` with the target spots at ``target_xy``.

    The smaller footprint is sampled (the anchor when it is not larger than a target spot), on a
    grid as fine as the point budget allows for the number of sampled footprints.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    anchor_area = anchor_areas(geom)
    target_area = footprint_area(target_shape)
    placed = anchor_area > 0
    if not placed.any() or target_area <= 0:
        empty = empty_csr((len(geom.centres), len(target_xy)), dtype=np.float32)
        return FootprintOverlap(empty, anchor_area, target_area, "anchor", 1)
    ratio = float(np.median(anchor_area[placed])) / target_area
    if ratio <= 1.0:
        per_axis = samples_per_axis(int(placed.sum()))
        fractions = _anchor_fractions(geom, target_xy, target_shape, per_axis)
        area = sp.csr_matrix(
            sp.diags(anchor_area.astype(np.float32)) @ fractions, dtype=np.float32
        )
        return FootprintOverlap(area, anchor_area, target_area, "anchor", per_axis)
    per_axis = samples_per_axis(int(np.isfinite(target_xy).all(axis=1).sum()))
    fractions = _target_fractions(geom, target_xy, target_shape, per_axis)
    area = sp.csr_matrix(fractions * np.float32(target_area), dtype=np.float32)
    return FootprintOverlap(area, anchor_area, target_area, "target", per_axis)
