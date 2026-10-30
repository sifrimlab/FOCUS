"""
Planning of the footprint-restricted CLS readout (pure NumPy, no torch).

Feature extraction encodes the tissue in encoder-sized windows. Every anchor spot is read out
in one window by its own CLS query, which attends only to the window tokens inside the spot's
footprint (see ``MicroscopyImageFeatureExtractor.extract_spot_features``). This module decides
which pyramid level is encoded, where the windows sit, which spots each window reads out, and
which tokens each readout may attend to.

Coordinates: pixel ``j`` of a level spans ``[j, j + 1)``; level ``i`` coordinates are level-0
coordinates times the measured size ratio ``(W_i / W_0, H_i / H_0)``. The token at row ``r``,
column ``c`` of a window with top-left ``(ox, oy)`` is centred at
``(ox + (c + 0.5) * p, oy + (r + 0.5) * p)`` for token size ``p``.
"""
import math
from dataclasses import dataclass, field

import numpy as np

# Prov-GigaPath tiles were resized to 0.5 µm per pixel (20x) before pretraining.
ENCODER_UM_PER_PX = 0.5
_MAX_SIDE_TOKENS = 64        # largest window side, in tokens
_MAX_READOUTS = 512          # spots read out by one forward pass of one window


@dataclass(frozen=True)
class LevelGeometry:
    """The pyramid level that is encoded and its relation to level 0."""
    level: int
    dims0: tuple[int, int]          # (H0, W0)
    dims: tuple[int, int]           # (H, W) of the encoded level
    um_per_px: float | None         # µm per pixel of the encoded level (None when unknown)

    @property
    def scale(self) -> np.ndarray:
        """Level pixels per level-0 pixel, ``(sx, sy)``, from the measured sizes."""
        return np.array([self.dims[1] / self.dims0[1], self.dims[0] / self.dims0[0]], dtype=np.float64)

    def to_level(self, xy0: np.ndarray) -> np.ndarray:
        return np.asarray(xy0, dtype=np.float64) * self.scale

    def to_level0(self, xy: np.ndarray) -> np.ndarray:
        return np.asarray(xy, dtype=np.float64) / self.scale


def select_encoding_level(
    level0_um_per_px: float | None,
    pyramid_dims: list[tuple[int, int]],
    target: float = ENCODER_UM_PER_PX,
) -> LevelGeometry:
    """
    The stored pyramid level whose pixel size is closest to ``target`` (log scale).

    Nothing is resampled: a level finer than the target is used as stored, and a source
    coarser than the target is encoded at level 0. Ties go to the finer level. When the
    level-0 pixel size is unknown, level 0 is used.
    """
    h0, w0 = pyramid_dims[0]
    if level0_um_per_px is None or not math.isfinite(level0_um_per_px) or level0_um_per_px <= 0:
        return LevelGeometry(0, (h0, w0), (h0, w0), None)
    best, best_err, best_um = 0, math.inf, level0_um_per_px
    for i, (h, w) in enumerate(pyramid_dims):
        um = level0_um_per_px * math.sqrt((w0 / w) * (h0 / h))
        err = abs(math.log(um / target))
        if err < best_err - 1e-12:
            best, best_err, best_um = i, err, um
    return LevelGeometry(best, (h0, w0), tuple(pyramid_dims[best]), best_um)


@dataclass(frozen=True)
class TokenGrid:
    """Window geometry of the encoder: token side ``patch`` and training window side ``base_side``."""
    patch: int
    base_side: int

    @property
    def context(self) -> int:
        """Minimum margin of tissue kept around a footprint inside its window."""
        return self.base_side // 4

    @property
    def max_side(self) -> int:
        return _MAX_SIDE_TOKENS * self.patch


@dataclass
class Chunk:
    """One forward-pass item: a window and the spots read out in it."""
    origin: tuple[int, int]      # (ox, oy) top-left, encoded-level pixels
    shape: tuple[int, int]       # (h, w) pixels, multiples of the token size
    spots: np.ndarray            # anchor indices


@dataclass
class WindowPlan:
    chunks: list[Chunk]                        # sorted by window shape, then readout count
    boxes: np.ndarray                          # (N, 4) footprint boxes, encoded-level pixels
    centres: np.ndarray                        # (N, 2) anchor centres, encoded-level pixels
    unplaced: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.intp))
    truncated: int = 0

    def groups(self):
        """Yield ``(shape, chunks)`` runs of consecutive chunks that share a window shape."""
        start = 0
        while start < len(self.chunks):
            shape = self.chunks[start].shape
            stop = start
            while stop < len(self.chunks) and self.chunks[stop].shape == shape:
                stop += 1
            yield shape, self.chunks[start:stop]
            start = stop


def _ceil_to(x, m):
    return np.ceil(np.asarray(x, dtype=np.float64) / m) * m


def _floor_to(x, m):
    return np.floor(np.asarray(x, dtype=np.float64) / m) * m


def plan_windows(geom, centres0: np.ndarray, level: LevelGeometry, grid: TokenGrid) -> WindowPlan:
    """
    Assign every placeable anchor spot to one encoder window.

    A footprint of extent ``e`` (encoded-level pixels, rounded up to whole tokens) needs
    ``e + 2 * context`` plus one token of slack. It gets a window of side ``base_side`` when that
    fits, otherwise the need rounded up to 4 tokens, at most ``max_side`` (larger footprints are
    read through the largest window centred on them). Windows sit on a lattice of stride
    ``side - e - 2 * context`` (rounded down to tokens, at least one token), so every footprint
    has at least ``context`` pixels of tissue around it inside its window, except where the
    window meets the image edge. Spots that share a window share one forward pass, in chunks of
    at most 512 readouts.

    Parameters
    ----------
    geom : AnchorGeometry
        Footprints in the level-0 image frame.
    centres0 : np.ndarray
        ``(N, 2)`` anchor centres in level-0 pixels.
    level : LevelGeometry
    grid : TokenGrid
    """
    h_img, w_img = level.dims
    p, base, ctx, max_side = grid.patch, grid.base_side, grid.context, grid.max_side
    n = len(centres0)

    scale = np.tile(level.scale, 2)
    boxes = geom.target_bboxes() * scale
    centres = level.to_level(centres0)

    clipped = boxes.copy()
    clipped[:, [0, 2]] = np.clip(clipped[:, [0, 2]], 0, w_img)
    clipped[:, [1, 3]] = np.clip(clipped[:, [1, 3]], 0, h_img)
    with np.errstate(invalid="ignore"):
        ok = geom.valid & np.isfinite(boxes).all(axis=1)
        ok &= (clipped[:, 2] > clipped[:, 0]) & (clipped[:, 3] > clipped[:, 1])
    extent = np.where(ok, np.maximum(clipped[:, 2] - clipped[:, 0], clipped[:, 3] - clipped[:, 1]), 0.0)
    mid = np.column_stack([(clipped[:, 0] + clipped[:, 2]) / 2, (clipped[:, 1] + clipped[:, 3]) / 2])

    # One token of slack on top of the footprint and its context: snapping the window origin
    # to the stride lattice moves the window by at most half a stride.
    need = _ceil_to(extent, p) + 2 * ctx + p
    side = np.where(need <= base, base, np.minimum(max_side, _ceil_to(need, 4 * p)))
    truncated = int((ok & (need > max_side)).sum())
    side_x = np.minimum(side, _floor_to(w_img, p))
    side_y = np.minimum(side, _floor_to(h_img, p))
    ok &= (side_x >= p) & (side_y >= p)

    stride = np.maximum(p, _floor_to(side - _ceil_to(extent, p) - 2 * ctx, p))
    ox = np.clip(stride * np.round((mid[:, 0] - side_x / 2) / stride), 0, np.maximum(w_img - side_x, 0))
    oy = np.clip(stride * np.round((mid[:, 1] - side_y / 2) / stride), 0, np.maximum(h_img - side_y, 0))

    placed = np.flatnonzero(ok)
    keys = np.column_stack([side_y[placed], side_x[placed], oy[placed], ox[placed]]).astype(np.int64)
    chunks: list[Chunk] = []
    if placed.size:
        windows, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.ravel()
        order = np.argsort(inverse, kind="stable")
        bounds = np.searchsorted(inverse[order], np.arange(len(windows) + 1))
        for wi, (sy, sx, wy, wx) in enumerate(windows):
            members = placed[order[bounds[wi]:bounds[wi + 1]]]
            for start in range(0, members.size, _MAX_READOUTS):
                chunks.append(Chunk((int(wx), int(wy)), (int(sy), int(sx)), members[start:start + _MAX_READOUTS]))
        chunks.sort(key=lambda c: (c.shape, -len(c.spots), c.origin[1], c.origin[0]))

    return WindowPlan(chunks, boxes, centres, np.flatnonzero(~ok), truncated)


def readout_membership(
    chunk: Chunk,
    plan: WindowPlan,
    geom,
    level: LevelGeometry,
    grid: TokenGrid,
    token_background: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Tokens each spot of ``chunk`` reads out.

    A token belongs to a spot when its centre lies inside the spot's footprint. A spot whose
    footprint contains no token centre reads the token that contains the spot centre. Background
    tokens are then removed; a spot left without tokens is not read out (its row stays zero).

    Returns
    -------
    member : np.ndarray
        ``(R, T)`` bool, R = number of spots in the chunk, T = tokens of the window.
    keep : np.ndarray
        ``(R,)`` bool, spots with at least one foreground token.
    """
    p = grid.patch
    ox, oy = chunk.origin
    h, w = chunk.shape
    ny, nx = h // p, w // p
    spots = chunk.spots
    r_count = len(spots)
    member = np.zeros((r_count, ny * nx), dtype=bool)

    # Candidate tokens: centres within one token of the footprint box.
    b = plan.boxes[spots]
    c_lo = np.clip(np.ceil((b[:, 0] - p - ox) / p - 0.5), 0, nx).astype(np.int64)
    c_hi = np.clip(np.floor((b[:, 2] + p - ox) / p - 0.5) + 1, 0, nx).astype(np.int64)
    r_lo = np.clip(np.ceil((b[:, 1] - p - oy) / p - 0.5), 0, ny).astype(np.int64)
    r_hi = np.clip(np.floor((b[:, 3] + p - oy) / p - 0.5) + 1, 0, ny).astype(np.int64)
    widths = np.maximum(c_hi - c_lo, 0)
    counts = widths * np.maximum(r_hi - r_lo, 0)
    if counts.sum():
        local = np.repeat(np.arange(r_count), counts)
        offset = np.arange(int(counts.sum())) - np.repeat(np.cumsum(counts) - counts, counts)
        wrep = np.repeat(np.maximum(widths, 1), counts)
        cols = np.repeat(c_lo, counts) + offset % wrep
        rows = np.repeat(r_lo, counts) + offset // wrep
        centres = np.column_stack([ox + (cols + 0.5) * p, oy + (rows + 0.5) * p])
        inside, _ = geom.contains(spots[local], level.to_level0(centres))
        member[local[inside], rows[inside] * nx + cols[inside]] = True

    empty = np.flatnonzero(~member.any(axis=1))
    if empty.size:
        c = np.floor((plan.centres[spots[empty], 0] - ox) / p)
        r = np.floor((plan.centres[spots[empty], 1] - oy) / p)
        inside = (c >= 0) & (c < nx) & (r >= 0) & (r < ny)
        member[empty[inside], (r[inside] * nx + c[inside]).astype(np.int64)] = True

    member &= ~token_background[None, :]
    return member, member.any(axis=1)
