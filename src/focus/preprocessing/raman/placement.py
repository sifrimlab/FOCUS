"""Place every acquisition in the pixel frame of the reference acquisition (image 0).

Stage coordinates are not trusted blindly across acquisitions: the slide or the stage frame can move
between scans (in P762, scans taken 9 hours apart are about 455 µm apart in stage coordinates).
Candidate placements (stage coordinates relative to the reference or to any image already placed,
and a phase-correlation search over every offset) are scored by the correlation of the channel-mean
tissue images, and the best one is refined by phase correlation of whitened images (Kuglin and
Hines 1975).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_laplace
from skimage.registration import phase_cross_correlation
from skimage.transform import rescale

from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman.mosaic import Prepared

_WHITEN_SIGMA = 1.5


@dataclass(frozen=True)
class Placement:
    """Image ``k`` pixel ``(c, r)`` lies at image-0 pixel ``(x0 + sx * c, y0 + sy * r)``."""

    x0: float
    y0: float
    sx: float
    sy: float
    method: str
    moved_um: float
    correlation: float

    def annotation(self) -> str:
        """``"x0,y0,sx,sy"`` as stored in the image annotation."""
        return f"{self.x0:.2f},{self.y0:.2f},{self.sx:.6f},{self.sy:.6f}"

    def describe(self) -> str:
        """One detail line for the activity log."""
        if self.method == "reference":
            return "reference image"
        line = f"placed by {self.method} at ({self.x0:.0f}, {self.y0:.0f}) px"
        if self.moved_um > 1:
            line += f", {self.moved_um:.0f} µm from its stage position"
        return line + f" (tissue correlation {self.correlation:.2f})"


def reference_placement() -> Placement:
    """The identity placement of image 0."""
    return Placement(0.0, 0.0, 1.0, 1.0, "reference", 0.0, 1.0)


def _factor(pixel_um: float, target_um: float) -> int:
    return max(1, int(round(target_um / pixel_um)))


def _view(prepared: Prepared, target_um: float, ref_um: float) -> np.ndarray:
    """Channel-mean view of ``prepared`` resampled to ``ref_um`` per pixel (NaN filled with 0)."""
    pixel = float(np.mean(prepared.pixel_um))
    factor = _factor(pixel, target_um)
    view = prepared.views[factor]
    scale = factor * pixel / ref_um
    view = np.nan_to_num(view, nan=0.0)
    if abs(scale - 1.0) > 1e-3:
        view = rescale(
            view, scale, order=1, preserve_range=True, anti_aliasing=scale < 1
        )
    return np.asarray(view, dtype=np.float32)


def _whiten(view: np.ndarray) -> np.ndarray:
    return np.asarray(gaussian_laplace(view.astype(np.float32), _WHITEN_SIGMA))


def _overlap_ncc(ref: np.ndarray, other: np.ndarray, offset: tuple[int, int]) -> float:
    """Correlation of ``other`` placed at integer ``offset`` (x, y) on ``ref`` over the overlap."""
    ox, oy = offset
    h, w = ref.shape
    y0, x0 = max(0, oy), max(0, ox)
    y1, x1 = min(h, oy + other.shape[0]), min(w, ox + other.shape[1])
    if y1 - y0 < 8 or x1 - x0 < 8:
        return -1.0
    a = ref[y0:y1, x0:x1].ravel()
    b = other[y0 - oy : y1 - oy, x0 - ox : x1 - ox].ravel()
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / denominator) if denominator > 0 else -1.0


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


def _refine(
    ref: np.ndarray, other: np.ndarray, offset: tuple[int, int], limit: float
) -> tuple[int, int]:
    """Correct an offset (x, y) by phase correlation of the overlap, within ``limit`` pixels."""
    ox, oy = offset
    h, w = ref.shape
    y0, x0 = max(0, oy), max(0, ox)
    y1, x1 = min(h, oy + other.shape[0]), min(w, ox + other.shape[1])
    if y1 - y0 < 32 or x1 - x0 < 32:
        return offset
    a = ref[y0:y1, x0:x1]
    b = other[y0 - oy : y1 - oy, x0 - ox : x1 - ox]
    shift, _, _ = phase_cross_correlation(a, b, normalization=None)  # type: ignore[no-untyped-call]
    if np.any(np.abs(shift) > limit):
        return offset
    return ox + int(shift[1]), oy + int(shift[0])


def _scored(
    views: list[np.ndarray],
    offsets: list[np.ndarray],
    view: np.ndarray,
    offset: np.ndarray,
) -> tuple[float, int]:
    """Best overlap correlation of ``view`` at ``offset`` with any placed view, and which one."""
    best, which = -1.0, 0
    for index, (placed, at) in enumerate(zip(views, offsets)):
        rel = np.rint(offset - at).astype(int)
        score = _overlap_ncc(placed, view, (int(rel[0]), int(rel[1])))
        if score > best:
            best, which = score, index
    return best, which


def place(
    reference: Prepared,
    other: Prepared,
    placed: list[tuple[Prepared, Placement]],
) -> Placement:
    """Placement of ``other`` in the reference's level-0 pixel frame.

    Candidates: the stage coordinates relative to the reference, the stage coordinates relative to
    every image already placed (acquisitions of one session share a stage frame), and a correlation
    search over every offset. Each candidate is scored by the correlation of the channel-mean
    tissue images with every image already placed, at ``PLACEMENT_COARSE_UM``; the best one is then
    refined at ``PLACEMENT_FINE_UM`` against the image it matched.
    """
    ref_px = float(np.mean(reference.pixel_um))
    scale = float(np.mean(other.pixel_um)) / ref_px
    coarse_um = _factor(ref_px, tech.PLACEMENT_COARSE_UM) * ref_px
    fine_um = _factor(ref_px, tech.PLACEMENT_FINE_UM) * ref_px
    to_coarse = ref_px / coarse_um

    stage = (other.origin_um - reference.origin_um) / ref_px  # (x, y) level-0 px
    candidates: dict[str, np.ndarray] = {"stage coordinates": stage}
    ref_white = _whiten(_view(reference, tech.PLACEMENT_COARSE_UM, coarse_um))
    other_view = _view(other, tech.PLACEMENT_COARSE_UM, coarse_um)
    found = _coarse(ref_white, _whiten(other_view))
    candidates["image correlation"] = np.array(found, dtype=np.float64) / to_coarse
    for prepared, at in placed[1:]:
        relative = (other.origin_um - prepared.origin_um) / ref_px
        candidates[f"stage coordinates relative to '{prepared.acquisition.name}'"] = (
            np.array([at.x0, at.y0]) + relative
        )

    views = [_view(p, tech.PLACEMENT_COARSE_UM, coarse_um) for p, _ in placed]
    offsets = [np.array([pl.x0, pl.y0]) * to_coarse for _, pl in placed]
    scores = {
        label: _scored(views, offsets, other_view, offset * to_coarse)
        for label, offset in candidates.items()
    }
    label = max(scores, key=lambda k: scores[k][0])
    score, match = scores[label]

    matched, at = placed[match]
    ratio = coarse_um / fine_um
    target_fine = _whiten(_view(matched, tech.PLACEMENT_FINE_UM, fine_um))
    other_fine = _whiten(_view(other, tech.PLACEMENT_FINE_UM, fine_um))
    start = np.rint((candidates[label] - [at.x0, at.y0]) * ref_px / fine_um).astype(int)
    refined = _refine(
        target_fine, other_fine, (int(start[0]), int(start[1])), limit=2 * ratio
    )
    offset = (
        np.array([at.x0, at.y0])
        + np.array(refined, dtype=np.float64) * fine_um / ref_px
    )
    moved = float(np.hypot(*(offset - stage)) * ref_px)
    return Placement(
        x0=float(offset[0]),
        y0=float(offset[1]),
        sx=scale,
        sy=scale,
        method=label,
        moved_um=moved,
        correlation=score,
    )
