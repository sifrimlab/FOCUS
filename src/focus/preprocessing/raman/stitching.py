"""Tile registration and mosaic assembly, after ASHLAR's edge alignment (Muhlich et al. 2022).

Stage positions predict where neighbouring tiles overlap. Each overlap is registered by phase
correlation (Kuglin and Hines 1975; ``skimage.registration.phase_cross_correlation``) on whitened
channel-mean tiles. A shift is accepted when the normalised cross-correlation of the aligned overlap
beats what pairs of tiles that do not overlap reach (a permutation threshold, as in ASHLAR), and when
it stays within ``MAX_SHIFT_UM`` of the stage prediction. Tile positions then solve a weighted least
squares problem over the accepted shifts, with a weak pull towards the stage positions so tiles
without accepted neighbours keep their stage placement. Edges whose residual exceeds
``MAX_RESIDUAL_PX`` are dropped and the system is solved again.

Assembly never blends: each output pixel takes the value of the tile whose centre is nearest, so
every stored sample is one measurement. Pixels no tile covers are NaN.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_laplace
from skimage.registration import phase_cross_correlation

# Largest accepted correction of a stage-predicted offset (ASHLAR's default max_shift), µm.
MAX_SHIFT_UM = 15.0
# Overlaps narrower than this (px) are not registered.
MIN_OVERLAP_PX = 16
# Whitening: Laplacian of Gaussian with this sigma (px) before correlation.
WHITEN_SIGMA = 1.0
# Permutation threshold: quantile of the correlation reached by non-overlapping tile pairs.
PERMUTATION_QUANTILE = 99.0
PERMUTATION_PAIRS = 200
# Edges disagreeing with the solved positions by more than this are dropped (px).
MAX_RESIDUAL_PX = 3.0
# Weight of the stage prior relative to the median accepted edge weight.
STAGE_PRIOR = 1e-3
_SEED = 0


@dataclass(frozen=True)
class Registration:
    """Solved integer tile origins ``(x, y)`` in mosaic pixels and the registration summary."""

    positions: np.ndarray
    stage_px: np.ndarray
    pairs: list[tuple[int, int]]
    accepted: int
    threshold: float
    residual_px: float
    correction_px: float

    def describe(self) -> str:
        """One detail line for the activity log."""
        if not self.pairs:
            return "single tile: stage position"
        return (
            f"{self.accepted} of {len(self.pairs)} overlaps registered "
            f"(correlation threshold {self.threshold:.2f}); median residual "
            f"{self.residual_px:.1f} px; median correction from the stage {self.correction_px:.1f} px"
        )


def stage_pixels(stage_um: np.ndarray, pixel_um: tuple[float, float]) -> np.ndarray:
    """Stage tile origins ``(x, y)`` µm as pixel offsets from the first-tile minimum.

    LAS X stage X runs along image columns and stage Y along image rows (checked against the
    mosaics LAS X merges itself); no axis is flipped.
    """
    origin = stage_um.min(axis=0)
    out: np.ndarray = (stage_um - origin) / np.asarray(pixel_um, dtype=np.float64)
    return out


def neighbour_pairs(
    stage_px: np.ndarray, shape: tuple[int, int]
) -> list[tuple[int, int]]:
    """Tile pairs that overlap side by side (diagonal corner contacts are ignored)."""
    h, w = shape
    pairs = []
    for i in range(len(stage_px)):
        for j in range(i + 1, len(stage_px)):
            dx, dy = stage_px[j] - stage_px[i]
            ox, oy = w - abs(dx), h - abs(dy)
            side = abs(dx) < w / 2 or abs(dy) < h / 2
            if ox >= MIN_OVERLAP_PX and oy >= MIN_OVERLAP_PX and side:
                pairs.append((i, j))
    return pairs


def _crops(
    a: np.ndarray, b: np.ndarray, offset: tuple[int, int]
) -> tuple[np.ndarray, np.ndarray] | None:
    """Overlapping regions of ``a`` and of ``b`` placed at integer ``offset`` (x, y) from ``a``."""
    h, w = a.shape
    dx, dy = offset
    x0, x1 = max(0, dx), min(w, w + dx)
    y0, y1 = max(0, dy), min(h, h + dy)
    if x1 - x0 < MIN_OVERLAP_PX or y1 - y0 < MIN_OVERLAP_PX:
        return None
    return a[y0:y1, x0:x1], b[y0 - dy : y1 - dy, x0 - dx : x1 - dx]


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denominator) if denominator > 0 else 0.0


def _register_pair(
    a: np.ndarray, b: np.ndarray, predicted: tuple[int, int], max_shift: float
) -> tuple[np.ndarray, float] | None:
    """Measured offset of ``b`` from ``a`` (x, y) near ``predicted``, and its correlation."""
    crops = _crops(a, b, predicted)
    if crops is None:
        return None
    ca, cb = crops
    if ca.std() == 0 or cb.std() == 0:
        return None
    shift, _, _ = phase_cross_correlation(ca, cb, normalization=None)  # type: ignore[no-untyped-call]
    # ``shift`` (rows, cols) moves ``cb`` onto ``ca``; the offset of ``b`` corrects by the same.
    correction = np.array([shift[1], shift[0]])
    limit = min(max_shift, min(ca.shape) / 2 - 1)
    if np.any(np.abs(correction) > limit):
        return None
    offset = (int(predicted[0] + correction[0]), int(predicted[1] + correction[1]))
    aligned = _crops(a, b, offset)
    if aligned is None:
        return None
    return np.array(offset, dtype=np.float64), _ncc(*aligned)


def _threshold(
    tiles: Callable[[int], np.ndarray],
    stage_px: np.ndarray,
    pairs: list[tuple[int, int]],
    max_shift: float,
) -> float:
    """Correlation reached by tile pairs that do not overlap, using real overlap geometries."""
    n = len(stage_px)
    if n < 3 or not pairs:
        return 1.0
    rng = np.random.default_rng(_SEED)
    neighbours = set(pairs)
    values: list[float] = []
    for _ in range(PERMUTATION_PAIRS * 3):
        if len(values) >= PERMUTATION_PAIRS:
            break
        i, j = (int(v) for v in rng.choice(n, size=2, replace=False))
        if (min(i, j), max(i, j)) in neighbours:
            continue
        gi, gj = pairs[int(rng.integers(len(pairs)))]
        offset = np.rint(stage_px[gj] - stage_px[gi]).astype(int)
        result = _register_pair(
            tiles(i), tiles(j), (int(offset[0]), int(offset[1])), max_shift
        )
        values.append(result[1] if result is not None else 0.0)
    return float(np.percentile(values, PERMUTATION_QUANTILE)) if values else 1.0


def _solve(
    n: int,
    edges: list[tuple[int, int, np.ndarray, float]],
    stage_px: np.ndarray,
) -> np.ndarray:
    """Weighted least squares positions from pairwise offsets with a weak stage prior."""
    prior = STAGE_PRIOR * (float(np.median([e[3] for e in edges])) if edges else 1.0)
    matrix = np.eye(n) * prior
    rhs = stage_px * prior
    for i, j, offset, weight in edges:
        matrix[i, i] += weight
        matrix[j, j] += weight
        matrix[i, j] -= weight
        matrix[j, i] -= weight
        rhs[i] -= weight * offset
        rhs[j] += weight * offset
    return np.asarray(np.linalg.solve(matrix, rhs))


def register(
    tiles: Callable[[int], np.ndarray],
    stage_px: np.ndarray,
    shape: tuple[int, int],
    pixel_um: float,
) -> Registration:
    """Register the tiles of one acquisition; ``tiles(i)`` returns tile ``i``'s channel mean."""
    n = len(stage_px)
    pairs = neighbour_pairs(stage_px, shape)
    max_shift = MAX_SHIFT_UM / pixel_um
    cache: dict[int, np.ndarray] = {}

    def whitened(i: int) -> np.ndarray:
        if i not in cache:
            cache[i] = gaussian_laplace(tiles(i).astype(np.float32), WHITEN_SIGMA)
        return cache[i]

    measured = []
    for i, j in pairs:
        predicted = np.rint(stage_px[j] - stage_px[i]).astype(int)
        result = _register_pair(
            whitened(i), whitened(j), (int(predicted[0]), int(predicted[1])), max_shift
        )
        if result is not None:
            measured.append((i, j, result[0], result[1]))
    threshold = _threshold(whitened, stage_px, pairs, max_shift)
    edges = [(i, j, off, ncc) for i, j, off, ncc in measured if ncc > threshold]
    positions = _solve(n, edges, stage_px)
    for _ in range(3):
        residuals = [
            float(np.abs(positions[j] - positions[i] - off).max())
            for i, j, off, _ in edges
        ]
        keep = [e for e, r in zip(edges, residuals) if r <= MAX_RESIDUAL_PX]
        if len(keep) == len(edges):
            break
        edges = keep
        positions = _solve(n, edges, stage_px)
    residual = (
        float(
            np.median(
                [
                    np.abs(positions[j] - positions[i] - off).max()
                    for i, j, off, _ in edges
                ]
            )
        )
        if edges
        else 0.0
    )
    correction = float(
        np.median(
            np.abs(positions - positions.mean(0) - (stage_px - stage_px.mean(0))).max(
                axis=1
            )
        )
    )
    integer = np.rint(positions - positions.min(axis=0)).astype(np.int64)
    return Registration(
        positions=integer,
        stage_px=stage_px,
        pairs=pairs,
        accepted=len(edges),
        threshold=threshold,
        residual_px=residual,
        correction_px=correction,
    )


# --- Assembly ---


def mosaic_shape(positions: np.ndarray, shape: tuple[int, int]) -> tuple[int, int]:
    """``(rows, columns)`` of the mosaic holding every tile."""
    return int(positions[:, 1].max()) + shape[0], int(positions[:, 0].max()) + shape[1]


def ownership(positions: np.ndarray, shape: tuple[int, int]) -> list[np.ndarray]:
    """Per tile, the boolean ``(Y, X)`` mask of the pixels it supplies (nearest tile centre).

    Ties go to the tile with the lower index, so every covered mosaic pixel has exactly one owner.
    """
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    centre = np.array([(w - 1) / 2, (h - 1) / 2])
    masks = []
    for k, pk in enumerate(positions):
        own = np.ones((h, w), dtype=bool)
        dk = (xx - centre[0]) ** 2 + (yy - centre[1]) ** 2
        for n, pn in enumerate(positions):
            if n == k:
                continue
            dx, dy = pn - pk
            if abs(dx) >= w or abs(dy) >= h:
                continue
            dn = (xx - centre[0] - dx) ** 2 + (yy - centre[1] - dy) ** 2
            covered = (xx >= dx) & (xx < dx + w) & (yy >= dy) & (yy < dy + h)
            closer = (dn < dk) | ((dn == dk) & (n < k))
            own &= ~(covered & closer)
        masks.append(own)
    return masks


def assemble_channel(
    plane: Callable[[int], np.ndarray],
    positions: np.ndarray,
    masks: list[np.ndarray],
    shape: tuple[int, int],
) -> np.ndarray:
    """``(rows, columns)`` float32 mosaic of one channel; uncovered pixels are NaN."""
    rows, cols = mosaic_shape(positions, shape)
    h, w = shape
    out = np.full((rows, cols), np.nan, dtype=np.float32)
    for k, (x, y) in enumerate(positions):
        view = out[y : y + h, x : x + w]
        np.copyto(view, plane(k), where=masks[k])
    return out


def tile_boxes(
    positions: np.ndarray, shape: tuple[int, int]
) -> Iterator[tuple[int, int, int, int]]:
    """``(x0, y0, x1, y1)`` mosaic box of every tile."""
    h, w = shape
    for x, y in positions:
        yield int(x), int(y), int(x) + w, int(y) + h
