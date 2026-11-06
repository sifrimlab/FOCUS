"""Place every acquisition in the pixel frame of the reference acquisition (image 0).

A placement is a similarity transform (rotation, scale and shift) from an image's level-0 pixels to
image 0's level-0 pixels. Stage coordinates are not trusted blindly across acquisitions: the slide
can be lifted and put back between scans, which moves it (455 µm on P762) and turns it (0.64° on
P762, enough to misplace one end of a 10 mm tissue by 100 µm under a shift-only model).

1. Candidates: a rotation search over the full circle (each angle with a phase-correlation search
   over every shift, Kuglin and Hines 1975, on Laplacian-of-Gaussian whitened channel-mean views),
   the stage offset to the reference, and the stage offset to every image already placed composed
   with that image's placement (acquisitions of one sitting share a stage frame, so they share its
   rotation). Each is scored by the correlation of the channel-mean tissue images with every image
   already placed.
2. Refinement against the image the best candidate matched: blocks of the whitened fine views are
   matched by normalised cross-correlation in a window around the prediction; a block is kept when
   its peak beats the quantile reached by mismatched block pairs (the permutation test of ASHLAR,
   Muhlich et al. 2022); a similarity transform is fitted to the kept blocks by RANSAC (Fischler and
   Bolles 1981) and least squares on its inliers, twice with a shrinking window.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_laplace, map_coordinates
from scipy.signal import fftconvolve
from skimage.measure import ransac
from skimage.registration import phase_cross_correlation
from skimage.transform import SimilarityTransform

from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman.mosaic import Prepared
from focus.spatial.spectral import nan_block_mean

_WHITEN_SIGMA = 1.5
_SEED = 0
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


@dataclass(frozen=True)
class Placement:
    """Image ``k`` pixel ``(c, r)`` lies at image-0 pixel ``(a c + b r + tx, c' c + d r + ty)``.

    ``matrix`` is ``(a, b, tx, c', d, ty)``; ``scale_ratio`` is the fitted scale over the ratio of
    the recorded pixel sizes; ``blocks`` the inlier blocks of the refinement (0: not refined).
    """

    matrix: tuple[float, float, float, float, float, float]
    method: str
    moved_um: float
    correlation: float
    rotation_deg: float = 0.0
    scale_ratio: float = 1.0
    residual_um: float | None = None
    blocks: int = 0

    @property
    def homogeneous(self) -> np.ndarray:
        """``(3, 3)`` homogeneous matrix."""
        return _homogeneous(self.matrix)

    @property
    def x0(self) -> float:
        """Image-0 column of the image's pixel (0, 0)."""
        return self.matrix[2]

    @property
    def y0(self) -> float:
        """Image-0 row of the image's pixel (0, 0)."""
        return self.matrix[5]

    def annotation(self) -> str:
        """``"a,b,tx,c,d,ty"`` as stored in the image annotation."""
        return ",".join(f"{v:.10g}" for v in self.matrix)

    def describe(self) -> str:
        """One detail line for the activity log."""
        if self.method == "reference":
            return "reference image"
        line = (
            f"placed by {self.method}: rotation {self.rotation_deg:+.2f}°, "
            f"scale {self.scale_ratio:.4f} of the pixel-size ratio, origin at "
            f"({self.x0:.0f}, {self.y0:.0f}) px"
        )
        if self.moved_um > 1:
            line += f", {self.moved_um:.0f} µm from its stage position"
        if self.blocks:
            line += f"; {self.blocks} matched blocks, median residual {self.residual_um:.1f} µm"
        else:
            line += "; not refined (too few matching blocks)"
        return line + f" (tissue correlation {self.correlation:.2f})"


def reference_placement() -> Placement:
    """The identity placement of image 0."""
    return Placement(IDENTITY, "reference", 0.0, 1.0)


def _homogeneous(matrix: tuple[float, ...] | np.ndarray) -> np.ndarray:
    out = np.eye(3)
    out[:2] = np.asarray(matrix, dtype=np.float64).reshape(2, 3)
    return out


def _flat(m: np.ndarray) -> tuple[float, float, float, float, float, float]:
    a, b, tx, c, d, ty = (float(v) for v in m[:2].ravel())
    return (a, b, tx, c, d, ty)


def _similarity(theta: float, scale: float, shift: tuple[float, float]) -> np.ndarray:
    cos, sin = math.cos(theta), math.sin(theta)
    return np.array(
        [
            [scale * cos, -scale * sin, shift[0]],
            [scale * sin, scale * cos, shift[1]],
            [0, 0, 1],
        ]
    )


def _view_to_px(factor: int) -> np.ndarray:
    """View pixel (u, v) -> level-0 pixel centre: block ``factor`` starts at ``factor * u``."""
    half = (factor - 1) / 2.0
    return np.array([[factor, 0, half], [0, factor, half], [0, 0, 1]], dtype=np.float64)


def _view(prepared: Prepared, um: float) -> tuple[np.ndarray, int]:
    """Channel-mean view at about ``um`` per pixel (NaN unmeasured) and its downscale factor."""
    pixel = float(np.mean(prepared.pixel_um))
    factor = max(1, int(round(um / pixel)))
    if factor in prepared.views:
        return prepared.views[factor], factor
    stored = [f for f in prepared.views if factor % f == 0]
    base = max(stored) if stored else min(prepared.views)
    view = nan_block_mean(prepared.views[base], max(1, factor // base))
    return view, base * max(1, factor // base)


def _filled(view: np.ndarray) -> np.ndarray:
    return np.asarray(np.nan_to_num(view, nan=0.0), dtype=np.float32)


def _whiten(view: np.ndarray) -> np.ndarray:
    """LoG-whitened view; unmeasured pixels and a margin around them NaN."""
    finite = np.isfinite(view)
    if not finite.any():
        return np.full(view.shape, np.nan, dtype=np.float32)
    filled = np.where(finite, view, float(np.mean(view[finite])))
    white = gaussian_laplace(filled.astype(np.float32), _WHITEN_SIGMA)
    margin = binary_erosion(finite, iterations=int(math.ceil(3 * _WHITEN_SIGMA)))
    return np.where(margin, white, np.nan).astype(np.float32)


def _warp(
    source: np.ndarray,
    source_factor: int,
    transform: np.ndarray,
    target_factor: int,
    shape: tuple[int, int],
    origin: tuple[int, int] = (0, 0),
) -> np.ndarray:
    """``source`` view resampled on a grid of the target's view (NaN outside the source).

    ``transform`` maps source level-0 pixels to target level-0 pixels; the grid starts at target
    view pixel ``origin`` (u, v) and has ``shape`` (rows, columns).
    """
    rows, cols = shape
    vv, uu = np.mgrid[0:rows, 0:cols].astype(np.float64)
    pts = np.stack([uu.ravel() + origin[0], vv.ravel() + origin[1], np.ones(uu.size)])
    to_source = (
        np.linalg.inv(_view_to_px(source_factor))
        @ np.linalg.inv(transform)
        @ _view_to_px(target_factor)
    )
    u, v, _ = to_source @ pts
    finite = np.isfinite(source)
    values = map_coordinates(
        np.where(finite, source, 0.0).astype(np.float32), [v, u], order=1, cval=np.nan
    )
    weight = map_coordinates(finite.astype(np.float32), [v, u], order=1, cval=0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(weight > 0.999, values / np.maximum(weight, 1e-6), np.nan)
    return out.reshape(shape).astype(np.float32)


def _masked_ncc(a: np.ndarray, b: np.ndarray, minimum: int = 64) -> float:
    """Correlation of two equally shaped images over the pixels finite in both."""
    both = np.isfinite(a) & np.isfinite(b)
    if int(both.sum()) < minimum:
        return -1.0
    x = a[both] - a[both].mean()
    y = b[both] - b[both].mean()
    denominator = float(np.sqrt((x * x).sum() * (y * y).sum()))
    return float((x * y).sum() / denominator) if denominator > 0 else -1.0


def _coarse(ref: np.ndarray, other: np.ndarray) -> tuple[int, int]:
    """Offset (x, y) of ``other`` on ``ref`` from a correlation over every relative position."""
    h = ref.shape[0] + other.shape[0]
    w = ref.shape[1] + other.shape[1]
    a = np.zeros((h, w), dtype=np.float32)
    b = np.zeros((h, w), dtype=np.float32)
    a[: ref.shape[0], : ref.shape[1]] = ref
    b[: other.shape[0], : other.shape[1]] = other
    shift, _, _ = phase_cross_correlation(a, b, normalization=None)  # type: ignore[no-untyped-call]
    dy, dx = int(shift[0]), int(shift[1])
    if dy >= ref.shape[0]:
        dy -= h
    if dx >= ref.shape[1]:
        dx -= w
    return dx, dy


@dataclass(frozen=True)
class _Reference:
    """A reference view with its two whitened forms."""

    view: np.ndarray
    factor: int
    filled: np.ndarray  # whitened, unmeasured at 0 (phase correlation)
    white: np.ndarray  # whitened, unmeasured NaN (scoring)

    @classmethod
    def of(cls, view: np.ndarray, factor: int) -> _Reference:
        return cls(view, factor, _whiten_fill(view), _whiten(view))


def _rotated(
    ref: _Reference,
    other_view: np.ndarray,
    other_factor: int,
    other_shape: tuple[int, int],
    theta: float,
    scale: float,
) -> tuple[np.ndarray, float]:
    """Transform of ``other`` turned by ``theta`` with the best shift on ``ref``, and its score."""
    ref_view, ref_factor = ref.view, ref.factor
    rotation = _similarity(theta, scale, (0.0, 0.0))
    rows, cols = other_shape
    corners = np.array(
        [[0, 0, 1], [cols, 0, 1], [0, rows, 1], [cols, rows, 1]], float
    ).T
    in_view = np.linalg.inv(_view_to_px(ref_factor)) @ rotation @ corners
    u0, v0 = np.floor(in_view[:2].min(axis=1)).astype(int)
    u1, v1 = np.ceil(in_view[:2].max(axis=1)).astype(int)
    canvas = _warp(
        other_view, other_factor, rotation, ref_factor, (v1 - v0, u1 - u0), (u0, v0)
    )
    dx, dy = _coarse(ref.filled, _whiten_fill(canvas))
    shift = ((dx - u0) * ref_factor, (dy - v0) * ref_factor)
    transform = _similarity(theta, scale, shift)
    placed = _warp(other_view, other_factor, transform, ref_factor, ref_view.shape)
    return transform, _masked_ncc(ref.white, _whiten(placed))


def _whiten_fill(view: np.ndarray) -> np.ndarray:
    """Whitened view with unmeasured pixels at 0 (for the zero-padded phase correlation)."""
    return np.asarray(gaussian_laplace(_filled(view), _WHITEN_SIGMA), dtype=np.float32)


def rotation_search(
    reference: Prepared, other: Prepared, scale: float
) -> tuple[np.ndarray, float]:
    """Best similarity transform of ``other`` on ``reference`` over the full circle, and its score.

    The full circle is searched in steps of ``PLACEMENT_ROTATION_STEPS_DEG[0]`` on the search view,
    then each finer step within the previous step on the coarse view.
    """
    steps = tech.PLACEMENT_ROTATION_STEPS_DEG
    ref = _Reference.of(*_view(reference, tech.PLACEMENT_SEARCH_UM))
    other_view, other_f = _view(other, tech.PLACEMENT_SEARCH_UM)
    angles = np.arange(0.0, 360.0, steps[0])
    best_angle, (best, score) = 0.0, (np.eye(3), -2.0)
    for angle in angles:
        found = _rotated(
            ref, other_view, other_f, other.shape, math.radians(angle), scale
        )
        if found[1] > score:
            best_angle, (best, score) = float(angle), found
    ref = _Reference.of(*_view(reference, tech.PLACEMENT_COARSE_UM))
    other_view, other_f = _view(other, tech.PLACEMENT_COARSE_UM)
    for previous, step in zip(steps[:-1], steps[1:]):
        centre = best_angle
        best, score = np.eye(3), -2.0
        for angle in np.arange(centre - previous, centre + previous + step / 2, step):
            found = _rotated(
                ref, other_view, other_f, other.shape, math.radians(angle), scale
            )
            if found[1] > score:
                best_angle, (best, score) = float(angle), found
    return best, score


def _score(
    placed: list[tuple[Prepared, Placement]], other: Prepared, transform: np.ndarray
) -> tuple[float, int]:
    """Best correlation of ``other`` at ``transform`` with any placed image, and which one."""
    other_view, other_f = _view(other, tech.PLACEMENT_COARSE_UM)
    best, which = -1.0, 0
    for index, (prepared, at) in enumerate(placed):
        view, factor = _view(prepared, tech.PLACEMENT_COARSE_UM)
        relative = np.linalg.inv(at.homogeneous) @ transform
        warped = _warp(other_view, other_f, relative, factor, view.shape)
        score = _masked_ncc(view, warped)
        if score > best:
            best, which = score, index
    return best, which


@dataclass(frozen=True)
class BlockFit:
    """A refined transform, its inlier blocks and their median residual (target level-0 px)."""

    transform: np.ndarray
    blocks: int
    residual_px: float


def _block_ncc(template: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Normalised cross-correlation of ``template`` at every position inside ``region``."""
    t = template - template.mean()
    norm_t = float(np.sqrt((t * t).sum()))
    ones = np.ones_like(t)
    num = fftconvolve(region, t[::-1, ::-1], mode="valid")
    s1 = fftconvolve(region, ones, mode="valid")
    s2 = fftconvolve(region * region, ones, mode="valid")
    var = np.maximum(s2 - s1 * s1 / t.size, 1e-12)
    return np.asarray(num / (np.sqrt(var) * max(norm_t, 1e-12)))


def refine_blocks(
    target: Prepared, other: Prepared, transform: np.ndarray
) -> BlockFit | None:
    """Similarity transform of ``other`` on ``target`` refined by block matching (None: too few)."""
    target_view, target_f = _view(target, tech.PLACEMENT_FINE_UM)
    other_view, other_f = _view(other, tech.PLACEMENT_FINE_UM)
    a = _whiten(target_view)
    rng = np.random.default_rng(_SEED)
    side = tech.PLACEMENT_BLOCK_PX
    fit: BlockFit | None = None
    for search in tech.PLACEMENT_BLOCK_SEARCH_PX:
        b = _whiten(_warp(other_view, other_f, transform, target_f, target_view.shape))
        rows, cols = a.shape
        origins = [
            (y, x)
            for y in range(search, rows - side - search, side)
            for x in range(search, cols - side - search, side)
        ]
        usable = []
        for y, x in origins:
            block = b[y : y + side, x : x + side]
            region = a[y - search : y + side + search, x - search : x + side + search]
            if np.isfinite(block).all() and np.isfinite(region).all():
                usable.append((y, x, float(block.std())))
        if len(usable) < tech.PLACEMENT_MIN_BLOCKS:
            return fit
        texture = np.array([s for _, _, s in usable])
        usable = [u for u, s in zip(usable, texture) if s >= np.median(texture)]
        if len(usable) > tech.PLACEMENT_MAX_BLOCKS:
            pick = rng.choice(len(usable), tech.PLACEMENT_MAX_BLOCKS, replace=False)
            usable = [usable[i] for i in sorted(pick)]

        def peak(i: int, j: int) -> tuple[float, int, int]:
            y, x, _ = usable[i]
            ry, rx, _ = usable[j]
            ncc = _block_ncc(
                b[y : y + side, x : x + side],
                a[ry - search : ry + side + search, rx - search : rx + side + search],
            )
            k = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
            return float(ncc[k]), int(k[0]) - search, int(k[1]) - search

        far = [
            (i, j)
            for i, j in rng.integers(0, len(usable), (4 * tech.PLACEMENT_NULL_PAIRS, 2))
            if abs(usable[i][0] - usable[j][0]) + abs(usable[i][1] - usable[j][1])
            > 3 * side
        ][: tech.PLACEMENT_NULL_PAIRS]
        null = [peak(int(i), int(j))[0] for i, j in far]
        threshold = (
            float(np.percentile(null, tech.PLACEMENT_BLOCK_QUANTILE)) if null else 0.0
        )
        src, dst = [], []
        to_px = _view_to_px(target_f)
        inverse = np.linalg.inv(transform)
        for i, (y, x, _) in enumerate(usable):
            value, dy, dx = peak(i, i)
            if value <= threshold or max(abs(dy), abs(dx)) >= search:
                continue
            centre = np.array([x + (side - 1) / 2.0, y + (side - 1) / 2.0, 1.0])
            at = to_px @ centre
            src.append((inverse @ at)[:2])
            dst.append((to_px @ (centre + [dx, dy, 0.0]))[:2])
        if len(src) < tech.PLACEMENT_MIN_BLOCKS:
            return fit
        src_a, dst_a = np.asarray(src), np.asarray(dst)
        model, inliers = ransac(  # type: ignore[no-untyped-call]
            (src_a, dst_a),
            SimilarityTransform,
            min_samples=3,
            residual_threshold=tech.PLACEMENT_RANSAC_PX * target_f,
            max_trials=1000,
            rng=_SEED,
        )
        if (
            model is None
            or inliers is None
            or int(inliers.sum()) < tech.PLACEMENT_MIN_BLOCKS
        ):
            return fit
        refined = SimilarityTransform.from_estimate(src_a[inliers], dst_a[inliers])
        if not refined:
            return fit
        transform = np.asarray(refined.params, dtype=np.float64)
        residual = np.linalg.norm(
            (
                transform
                @ np.column_stack([src_a[inliers], np.ones(int(inliers.sum()))]).T
            )[:2].T
            - dst_a[inliers],
            axis=1,
        )
        fit = BlockFit(transform, int(inliers.sum()), float(np.median(residual)))
    return fit


def _stage_relative(other: Prepared, anchor: Prepared, at: Placement) -> np.ndarray:
    """``other`` placed by its stage offset to ``anchor``, through ``anchor``'s placement."""
    anchor_px = float(np.mean(anchor.pixel_um))
    scale = float(np.mean(other.pixel_um)) / anchor_px
    shift = (other.origin_um - anchor.origin_um) / anchor_px
    to_anchor = np.array([[scale, 0, shift[0]], [0, scale, shift[1]], [0, 0, 1]])
    return np.asarray(at.homogeneous @ to_anchor)


def place(
    reference: Prepared,
    other: Prepared,
    placed: list[tuple[Prepared, Placement]],
) -> Placement:
    """Placement of ``other`` in the reference's level-0 pixel frame (see the module docstring)."""
    ref_px = float(np.mean(reference.pixel_um))
    scale = float(np.mean(other.pixel_um)) / ref_px
    stage = _stage_relative(other, reference, placed[0][1])
    candidates: dict[str, np.ndarray] = {"stage coordinates": stage}
    candidates["image correlation"], _ = rotation_search(reference, other, scale)
    for prepared, at in placed[1:]:
        candidates[f"stage coordinates relative to '{prepared.acquisition.name}'"] = (
            _stage_relative(other, prepared, at)
        )
    scores = {label: _score(placed, other, m) for label, m in candidates.items()}
    label = max(scores, key=lambda k: scores[k][0])
    score, match = scores[label]
    transform = candidates[label]

    matched, at = placed[match]
    relative = np.linalg.inv(at.homogeneous) @ transform
    fit = refine_blocks(matched, other, relative)
    blocks, residual_um = 0, None
    if fit is not None:
        transform = at.homogeneous @ fit.transform
        blocks = fit.blocks
        residual_um = fit.residual_px * float(np.mean(matched.pixel_um))

    rows, cols = other.shape
    centre = np.array([cols / 2.0, rows / 2.0, 1.0])
    moved = float(np.linalg.norm((transform @ centre - stage @ centre)[:2]) * ref_px)
    linear = transform[:2, :2]
    fitted_scale = float(np.sqrt(abs(np.linalg.det(linear))))
    return Placement(
        matrix=_flat(transform),
        method=label,
        moved_um=moved,
        correlation=score,
        rotation_deg=math.degrees(math.atan2(linear[1, 0], linear[0, 0])),
        scale_ratio=fitted_scale / scale,
        residual_um=residual_um,
        blocks=blocks,
    )
