"""
Lattice planning of the microscopy encoding (pure NumPy, no torch).

Feature extraction encodes the image in ``base x base`` encoder windows on a fixed lattice whose
stride is half a window, rounded down to whole tokens. Window origins are multiples of the token
size, so the tokens of every window fall on one global token grid. Each token of that grid is
read from exactly one window: the one whose centre is nearest to it, ties going to the lower
window. Its centre then lies at least ``base / 4`` px inside that window, except at the image
border.

A spot's embedding is the weighted mean of the final-layer tokens its footprint overlaps (see
``MicroscopyImageFeatureExtractor.extract_spot_features``), and its patch is the window that reads
the token holding its centre. This module decides which pyramid level is encoded, where the
windows sit, which tokens each spot reads and with which weight, and which window is each spot's
patch.

Coordinates: pixel ``j`` of a level spans ``[j, j + 1)``; level ``i`` coordinates are level-0
coordinates times the measured size ratio ``(W_i / W_0, H_i / H_0)``. Token ``(r, c)`` of the
global grid covers ``[c * p, (c + 1) * p) x [r * p, (r + 1) * p)`` for token size ``p``.
"""

from numpy.typing import NDArray

from typing import cast

from numpy.typing import ArrayLike
from focus.spatial.footprint import AnchorGeometry
import math
from dataclasses import dataclass

import numpy as np

# Prov-GigaPath tiles were resized to 0.5 µm per pixel (20x) before pretraining.
ENCODER_UM_PER_PX = 0.5
_SAMPLES_PER_TOKEN = (
    4  # s x s sample points per token measure its overlap with a footprint
)
_MAX_POINTS = 1 << 22  # sample points per containment call (bounds host memory)


@dataclass(frozen=True)
class LevelGeometry:
    """The pyramid level that is encoded and its relation to level 0."""

    level: int
    dims0: tuple[int, int]  # (H0, W0)
    dims: tuple[int, int]  # (H, W) of the encoded level
    um_per_px: float | None  # µm per pixel of the encoded level (None when unknown)

    @property
    def scale(self) -> np.ndarray:
        """Level pixels per level-0 pixel, ``(sx, sy)``, from the measured sizes."""
        return np.array(
            [self.dims[1] / self.dims0[1], self.dims[0] / self.dims0[0]],
            dtype=np.float64,
        )

    def to_level(self, xy0: ArrayLike) -> np.ndarray:
        """Convert level-0 pixel coordinates to this pyramid level."""
        return cast(np.ndarray, np.asarray(xy0, dtype=np.float64) * self.scale)

    def to_level0(self, xy: ArrayLike) -> np.ndarray:
        """Convert this pyramid level's coordinates to level-0 pixels."""
        return cast(np.ndarray, np.asarray(xy, dtype=np.float64) / self.scale)


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
    if (
        level0_um_per_px is None
        or not math.isfinite(level0_um_per_px)
        or level0_um_per_px <= 0
    ):
        return LevelGeometry(0, (h0, w0), (h0, w0), None)
    best, best_err, best_um = 0, math.inf, level0_um_per_px
    for i, (h, w) in enumerate(pyramid_dims):
        um = level0_um_per_px * math.sqrt((w0 / w) * (h0 / h))
        err = abs(math.log(um / target))
        if err < best_err - 1e-12:
            best, best_err, best_um = i, err, um
    return LevelGeometry(
        best, (h0, w0), cast(tuple[int, int], tuple(pyramid_dims[best])), best_um
    )


@dataclass(frozen=True)
class TokenGrid:
    """Window geometry of the encoder: token side ``patch`` and training window side ``base_side``."""

    patch: int
    base_side: int

    @property
    def stride(self) -> int:
        """Lattice stride: half the encoder window, rounded down to whole tokens (at least one)."""
        return max(self.patch, (self.base_side // 2) // self.patch * self.patch)


@dataclass(frozen=True)
class AxisLattice:
    """Window placement along one image axis."""

    origins: NDArray[np.int64]  # window starts, multiples of the token size
    side: int  # window extent along this axis, pixels (0 when no whole token fits)
    n_tokens: int  # whole tokens covered by the windows: [0, n_tokens * p)
    owner: NDArray[np.int64]  # (n_tokens,) index of the window that reads each token


def axis_lattice(dim: int, grid: TokenGrid) -> AxisLattice:
    """
    Windows along an axis of ``dim`` pixels.

    Windows are ``base_side`` wide (or the whole axis, rounded down to tokens, when it is
    shorter), start at ``0, stride, 2 * stride, ...`` and end with one window flush with the last
    whole token. Each token is owned by the window whose centre is nearest to the token centre,
    ties going to the lower window.
    """
    p = grid.patch
    side = min(grid.base_side, dim // p * p)
    empty = np.zeros(0, dtype=np.int64)
    if side < p:
        return AxisLattice(empty, 0, 0, empty)
    last = (dim - side) // p * p
    origins = np.arange(0, last + 1, grid.stride, dtype=np.int64)
    if origins[-1] < last:
        origins = np.append(origins, np.int64(last))
    n_tokens = (last + side) // p
    if len(origins) == 1:
        return AxisLattice(origins, side, n_tokens, np.zeros(n_tokens, dtype=np.int64))
    centres = origins + side / 2
    tok = (np.arange(n_tokens) + 0.5) * p
    hi = np.clip(np.searchsorted(centres, tok, side="left"), 1, len(centres) - 1)
    owner = np.where(tok - centres[hi - 1] <= centres[hi] - tok, hi - 1, hi)
    return AxisLattice(origins, side, n_tokens, owner.astype(np.int64))


@dataclass(frozen=True)
class WindowLattice:
    """The encoder windows of one image. Windows are numbered row-major (``wy * nx + wx``)."""

    patch: int
    stride: int  # lattice stride, pixels
    y: AxisLattice
    x: AxisLattice

    @property
    def shape(self) -> tuple[int, int]:
        """Number of windows along ``(y, x)``."""
        return len(self.y.origins), len(self.x.origins)

    @property
    def window_shape(self) -> tuple[int, int]:
        """Window ``(h, w)`` in pixels."""
        return self.y.side, self.x.side

    @property
    def token_shape(self) -> tuple[int, int]:
        """Rows and columns of the global token grid covered by the windows."""
        return self.y.n_tokens, self.x.n_tokens

    @property
    def tokens_per_window(self) -> int:
        """Tokens in one window."""
        return (self.y.side // self.patch) * (self.x.side // self.patch)

    def origin(self, window: ArrayLike) -> NDArray[np.int64]:
        """``(K, 2)`` top-left ``(ox, oy)`` of windows, in encoded-level pixels."""
        wy, wx = np.divmod(np.asarray(window, dtype=np.int64), self.shape[1])
        return np.column_stack([self.x.origins[wx], self.y.origins[wy]])

    def centre(self, window: ArrayLike) -> NDArray[np.float64]:
        """``(K, 2)`` centre ``(x, y)`` of windows, in encoded-level pixels."""
        h, w = self.window_shape
        centre = self.origin(window).astype(np.float64) + np.array([w / 2, h / 2])
        return cast(NDArray[np.float64], centre)

    def token_window(
        self, rows: ArrayLike, cols: ArrayLike
    ) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """Window reading each global token ``(rows, cols)`` and the token's index inside it."""
        rows = np.asarray(rows, dtype=np.int64)
        cols = np.asarray(cols, dtype=np.int64)
        wy, wx = self.y.owner[rows], self.x.owner[cols]
        local_r = rows - self.y.origins[wy] // self.patch
        local_c = cols - self.x.origins[wx] // self.patch
        return wy * self.shape[1] + wx, local_r * (self.x.side // self.patch) + local_c


def build_lattice(dims: tuple[int, int], grid: TokenGrid) -> WindowLattice:
    """The window lattice of an encoded image of ``dims = (H, W)`` pixels."""
    return WindowLattice(
        grid.patch,
        grid.stride,
        axis_lattice(int(dims[0]), grid),
        axis_lattice(int(dims[1]), grid),
    )


@dataclass
class SpotTokens:
    """The tokens each spot reads, as entries sorted by window."""

    spot: NDArray[np.int64]  # (E,) anchor index
    window: NDArray[np.int64]  # (E,) lattice window that reads the token
    local: NDArray[np.int64]  # (E,) token index inside that window, row-major
    weight: NDArray[np.float64]  # (E,) overlap of the token with the footprint, (0, 1]
    total: NDArray[
        np.float64
    ]  # (N,) summed weight per spot; 0 when the spot reads nothing


def _sample_offsets(p: int, s: int) -> np.ndarray:
    """``(s*s, 2)`` sub-cell centres of a ``p`` px token, relative to its top-left corner."""
    k = (np.arange(s) + 0.5) * p / s
    xx, yy = np.meshgrid(k, k)
    return np.column_stack([xx.ravel(), yy.ravel()])


def footprint_tokens(
    geom: AnchorGeometry,
    centres0: np.ndarray,
    level: LevelGeometry,
    lattice: WindowLattice,
    token_background: np.ndarray,
    samples: int = _SAMPLES_PER_TOKEN,
    max_points: int = _MAX_POINTS,
) -> SpotTokens:
    """
    The tokens of the global grid each anchor footprint overlaps, weighted by that overlap.

    A token's weight is the fraction of ``samples x samples`` sub-cell centres of the token that
    lie inside the footprint (``geom.contains``, so the footprint's shape and the alignment map,
    affine or projective, are followed exactly). A placed spot whose footprint covers no sample
    point reads the token holding its centre (clipped to the grid) with weight 1. Background
    tokens are then dropped; a spot left without tokens reads nothing (``total == 0``).

    Parameters
    ----------
    geom : AnchorGeometry
        Footprints in the level-0 image frame.
    centres0 : np.ndarray
        ``(N, 2)`` anchor centres in level-0 pixels.
    level : LevelGeometry
    lattice : WindowLattice
        Windows of the encoded image.
    token_background : np.ndarray
        ``lattice.token_shape`` bool, True for background tokens.
    """
    p = lattice.patch
    ny, nx = lattice.token_shape
    n = len(centres0)
    if ny == 0 or nx == 0:
        empty = np.zeros(0, dtype=np.int64)
        return SpotTokens(empty, empty, empty, np.zeros(0), np.zeros(n))

    boxes = geom.target_bboxes() * np.tile(level.scale, 2)
    with np.errstate(invalid="ignore"):
        ok = geom.valid & np.isfinite(boxes).all(axis=1)
        ok &= (boxes[:, 2] > 0) & (boxes[:, 0] < nx * p)
        ok &= (boxes[:, 3] > 0) & (boxes[:, 1] < ny * p)
    idx = np.flatnonzero(ok)
    b = boxes[idx]
    c_lo = np.clip(np.floor(b[:, 0] / p), 0, nx - 1).astype(np.int64)
    c_hi = np.clip(np.floor(b[:, 2] / p), 0, nx - 1).astype(np.int64)
    r_lo = np.clip(np.floor(b[:, 1] / p), 0, ny - 1).astype(np.int64)
    r_hi = np.clip(np.floor(b[:, 3] / p), 0, ny - 1).astype(np.int64)
    widths = c_hi - c_lo + 1
    counts = widths * (r_hi - r_lo + 1)

    offsets = _sample_offsets(p, samples)
    per_token = len(offsets)
    budget = max(1, max_points // per_token)
    spot_parts, row_parts, col_parts, w_parts = [], [], [], []
    ends = np.cumsum(counts)
    start = 0
    while start < len(idx):
        # Spots in order until their candidate tokens fill the budget (always at least one spot).
        base = ends[start] - counts[start]
        stop = max(start + 1, int(np.searchsorted(ends, base + budget, side="right")))
        sel = np.arange(start, stop)
        cnt = counts[sel]
        local = np.repeat(sel, cnt)
        offset = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        cols = c_lo[local] + offset % widths[local]
        rows = r_lo[local] + offset // widths[local]
        corner = np.column_stack([cols * p, rows * p]).astype(np.float64)
        pts = (corner[:, None, :] + offsets[None]).reshape(-1, 2)
        inside, _ = geom.contains(
            np.repeat(idx[local], per_token), level.to_level0(pts)
        )
        weight = inside.reshape(-1, per_token).mean(axis=1)
        hit = weight > 0
        spot_parts.append(idx[local[hit]])
        row_parts.append(rows[hit])
        col_parts.append(cols[hit])
        w_parts.append(weight[hit])
        start = stop

    spot = np.concatenate(spot_parts) if spot_parts else np.zeros(0, np.int64)
    rows = np.concatenate(row_parts) if row_parts else np.zeros(0, np.int64)
    cols = np.concatenate(col_parts) if col_parts else np.zeros(0, np.int64)
    weight = np.concatenate(w_parts) if w_parts else np.zeros(0)

    # A placed spot whose footprint covers no sample point reads the token holding its centre.
    centres = level.to_level(centres0)
    h, w = level.dims
    with np.errstate(invalid="ignore"):
        placed = geom.valid & np.isfinite(centres).all(axis=1)
        placed &= (centres[:, 0] >= 0) & (centres[:, 0] < w)
        placed &= (centres[:, 1] >= 0) & (centres[:, 1] < h)
    missing = np.flatnonzero(placed & (np.bincount(spot, minlength=n) == 0))
    if missing.size:
        spot = np.concatenate([spot, missing])
        rows = np.concatenate([rows, _centre_token(centres[missing, 1], p, ny)])
        cols = np.concatenate([cols, _centre_token(centres[missing, 0], p, nx)])
        weight = np.concatenate([weight, np.ones(missing.size)])

    fg = ~token_background[rows, cols]
    spot, rows, cols, weight = spot[fg], rows[fg], cols[fg], weight[fg]
    window, local_tok = lattice.token_window(rows, cols)
    order = np.argsort(window, kind="stable")
    return SpotTokens(
        spot[order].astype(np.int64),
        window[order],
        local_tok[order],
        weight[order],
        np.bincount(spot, weights=weight, minlength=n).astype(np.float64),
    )


def _centre_token(v: np.ndarray, p: int, n: int) -> NDArray[np.int64]:
    """Global token index holding coordinate ``v``, clipped to the ``n`` tokens of the grid."""
    return np.clip(np.floor(v / p), 0, n - 1).astype(np.int64)


def patch_links(
    centres0: np.ndarray,
    level: LevelGeometry,
    lattice: WindowLattice,
    live: np.ndarray,
) -> NDArray[np.int64]:
    """
    Each spot's patch: the window that reads the token holding its centre (clipped to the grid).

    Only ``live`` spots (those with a non-zero embedding) are linked; the others get ``-1``.
    """
    n = len(centres0)
    out = np.full(n, -1, dtype=np.int64)
    ny, nx = lattice.token_shape
    if ny == 0 or nx == 0:
        return out
    centres = level.to_level(centres0)
    ok = np.asarray(live, dtype=bool) & np.isfinite(centres).all(axis=1)
    p = lattice.patch
    rows = _centre_token(centres[ok, 1], p, ny)
    cols = _centre_token(centres[ok, 0], p, nx)
    out[ok] = lattice.token_window(rows, cols)[0]
    return out


def patch_anchor_xy(
    geom: AnchorGeometry,
    lattice: WindowLattice,
    level: LevelGeometry,
    patch: np.ndarray,
    centres0: np.ndarray,
) -> NDArray[np.float64]:
    """
    ``(N, 2)`` centre of each spot's patch in the anchor frame (NaN without a patch).

    A window centre is mapped back through the alignment map evaluated at the linked spot
    nearest to it, so every spot of one patch gets the same coordinates (the choice matters only
    for a ``local_affine`` map). Centres the map cannot place are NaN.
    """
    out = np.full((len(patch), 2), np.nan)
    linked = np.flatnonzero(np.asarray(patch) >= 0)
    if not linked.size:
        return out
    windows, inverse = np.unique(patch[linked], return_inverse=True)
    inverse = inverse.ravel()
    centre = lattice.centre(windows)
    dist = np.linalg.norm(level.to_level(centres0[linked]) - centre[inverse], axis=1)
    order = np.lexsort((dist, inverse))
    first = order[np.searchsorted(inverse[order], np.arange(len(windows)))]
    xy = geom.transform.to_anchor(level.to_level0(centre), linked[first])
    xy[~np.isfinite(xy).all(axis=1)] = np.nan
    out[linked] = xy[inverse]
    return out
