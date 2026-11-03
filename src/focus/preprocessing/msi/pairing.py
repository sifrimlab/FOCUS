"""Register the two polarities of a dual ion mode acquisition on their spectral content and pair them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from focus.preprocessing.msi.pairing_signal import (
    RigidFit,
    lattice_offsets,
    rigid_fit,
    components,
    mean_squared_correlation,
    raster,
    robust_rigid_fit,
    rotate_rows,
    subcell_peak,
    tile_shifts,
)
from focus.preprocessing.msi.settings import (
    _PAIRING_ANGLE_EXCLUSION,
    _PAIRING_ANGLE_STEP,
    _PAIRING_COARSE_PITCHES,
    _PAIRING_HIGHPASS_PITCHES,
    _PAIRING_MAX_DISAGREEMENT,
    _PAIRING_MIN_CELLS,
    _PAIRING_MIN_MARGIN,
    _PAIRING_MIN_OVERLAP,
    _PAIRING_PEAK_EXCLUSION,
    _PAIRING_SNAP_COHERENCE,
)


class PolarityPairingError(ValueError):
    """The two polarities could not be registered with confidence."""

    def __init__(self, message: str, margin: float | None = None) -> None:
        """Keep the verification margin of the rejected pose, when one was scored."""
        super().__init__(message)
        self.margin = margin


@dataclass(frozen=True)
class Pose:
    """Rigid map of POSITIVE positions onto NEGATIVE ones: optional mirror of x, rotation, translation.

    ``q = R(angle) @ (M @ (p - center)) + center + translation``, with M the x mirror when ``mirror``.
    """

    angle: float
    translation: NDArray[np.float64]
    center: NDArray[np.float64]
    mirror: bool = False

    def apply(self, points: NDArray[np.float64]) -> NDArray[np.float64]:
        """Map POSITIVE positions into the NEGATIVE frame."""
        local = points - self.center
        if self.mirror:
            local = local * np.array([-1.0, 1.0])
        return rotate_rows(local, self.angle) + self.center + self.translation


@dataclass(frozen=True)
class Registration:
    """Final pose and the evidence it was accepted on."""

    pose: Pose
    margin: float
    disagreement: float
    overlap: float


@dataclass(frozen=True)
class _Candidate:
    """Best translation of one rotation: score, competing score far from the peak, overlap."""

    score: float
    pose: Pose
    second: float
    overlap: float


class _Search:
    """Translation search of the rotated, optionally mirrored, POSITIVE content over the NEGATIVE."""

    def __init__(
        self,
        pos: np.ndarray,
        neg: np.ndarray,
        Cp: np.ndarray,
        Cn: np.ndarray,
        cell: np.ndarray,
        sigma: float,
    ) -> None:
        """Rasterize the NEGATIVE images once at this cell size and high-pass sigma (micrometres)."""
        self.pos, self.Cp, self.cell = pos, Cp, cell
        self.sigma = sigma / cell[::-1]
        self.center = pos.mean(axis=0)
        self.neg_origin = neg.min(axis=0) - cell / 2
        self.F, self.mf = raster(
            neg, Cn, cell, self.neg_origin, self._shape(neg), self.sigma
        )

    def _shape(self, xy: np.ndarray) -> tuple[int, int]:
        """Image shape (rows, columns) covering ``xy`` with a margin of one cell."""
        nx, ny = (np.ptp(xy, axis=0) / self.cell).astype(int) + 3
        return int(ny), int(nx)

    def best(
        self,
        angle: float,
        mirror: bool,
        channels: slice = slice(None),
        near: tuple[NDArray[np.float64], float] | None = None,
    ) -> _Candidate:
        """Best translation at one rotation, and the best score more than the exclusion radius away.

        Globally (``near`` None) the score is the root mean squared channel correlation times the
        root of the overlap fraction, which keeps the search off small chance overlaps. With ``near``
        = (translation, radius in micrometres) only translations within the radius are searched and
        the overlap weight is left out: a local search compares poses of nearly equal overlap.
        """
        start = Pose(angle, np.zeros(2), self.center, mirror)
        moved = start.apply(self.pos)
        origin = moved.min(axis=0) - self.cell / 2
        G, mg = raster(
            moved,
            self.Cp[:, channels],
            self.cell,
            origin,
            self._shape(moved),
            self.sigma,
        )
        F = self.F[channels]
        smaller = min(self.mf.sum(), mg.sum())
        r2, n = mean_squared_correlation(F, self.mf, G, mg)
        zero = np.array(mg.shape[::-1]) - 1  # index (x, y) of the zero shift
        rows, cols = np.ogrid[: r2.shape[0], : r2.shape[1]]
        if near is None:
            z = np.sqrt(r2) * np.sqrt(np.clip(n, 0, None) / smaller)
        else:
            target = (near[0] - (self.neg_origin - origin)) / self.cell + zero
            z = np.sqrt(r2)
            offset = ((cols - target[0]) * self.cell[0]) ** 2 + (
                (rows - target[1]) * self.cell[1]
            ) ** 2
            z[offset > near[1] ** 2] = 0.0
        z[n < _PAIRING_MIN_OVERLAP * smaller] = 0.0
        k = np.unravel_index(int(np.argmax(z)), z.shape)
        shift = (subcell_peak(z, k) - zero) * self.cell
        far = (rows - k[0]) ** 2 + (cols - k[1]) ** 2 > _PAIRING_PEAK_EXCLUSION**2
        pose = Pose(angle, self.neg_origin - origin + shift, self.center, mirror)
        return _Candidate(
            float(z[k]), pose, float(z[far].max(initial=0.0)), float(n[k] / smaller)
        )


def _best_of(candidates: list[_Candidate]) -> _Candidate:
    return max(candidates, key=lambda c: c.score)


def _competitor(candidates: list[_Candidate], best: _Candidate) -> float:
    """Strongest competing pose: a distant translation, another rotation or the other handedness."""
    limit = np.deg2rad(_PAIRING_ANGLE_EXCLUSION)
    others = [
        c.score
        for c in candidates
        if c.pose.mirror != best.pose.mirror
        or abs(np.angle(np.exp(1j * (c.pose.angle - best.pose.angle)))) > limit
    ]
    return max([best.second, *others])


def _coarse(
    search: _Search, mirrors: tuple[bool, ...], channels: slice = slice(None)
) -> list[_Candidate]:
    angles = np.deg2rad(np.arange(-180.0, 180.0, _PAIRING_ANGLE_STEP))
    return [search.best(a, m, channels) for m in mirrors for a in angles]


def _refine(
    search: _Search, start: _Candidate, span: float, step: float, radius: float
) -> _Candidate:
    """Best rotation within ``start`` ± span degrees, every step degrees, parabola-refined.

    Translations are searched within ``radius`` micrometres of the start pose. The window moves to
    the best angle and is searched again while the best lies on its edge.
    """
    near = (start.pose.translation, radius)
    center = start.pose.angle
    offsets = np.deg2rad(np.arange(-span, span + step / 2, step))
    for _ in range(4):
        found = [search.best(center + d, start.pose.mirror, near=near) for d in offsets]
        i = int(np.argmax([c.score for c in found]))
        center = found[i].pose.angle
        if 0 < i < len(found) - 1:
            break
    else:
        return found[i]
    a, b, c = (found[j].score for j in (i - 1, i, i + 1))
    curvature = a - 2 * b + c
    if curvature >= 0:
        return found[i]
    vertex = center + 0.5 * (a - c) / curvature * np.deg2rad(step)
    return search.best(vertex, start.pose.mirror, near=near)


def _tile_fit(
    pose: Pose,
    pos: np.ndarray,
    neg: np.ndarray,
    Cp: np.ndarray,
    Cn: np.ndarray,
    steps: np.ndarray,
    sigma: float,
) -> Pose:
    """Correct the pose by the rotation and translation fitted to the tile shifts at native resolution.

    Repeated up to three times; the pose is returned as it is when fewer than three tiles agree.
    """
    for _ in range(3):
        centres, shifts, scores = tile_shifts(
            pose.apply(pos), neg, Cp, Cn, steps, sigma
        )
        fit = robust_rigid_fit(centres, shifts, scores, float(steps.max()))
        if fit is None:
            return pose
        pose = _corrected(pose, fit)
        angle, _, move = fit
        if abs(angle) < 1e-4 and np.linalg.norm(move) < 0.05 * steps.min():
            break
    return pose


def _corrected(pose: Pose, fit: RigidFit) -> Pose:
    """Pose followed by the rigid correction ``fit`` (rotation about its pivot, then translation)."""
    angle, pivot, move = fit
    base = rotate_rows(pose.center + pose.translation - pivot, angle)
    return Pose(
        pose.angle + angle, base + pivot + move - pose.center, pose.center, pose.mirror
    )


def _snap_to_lattice(
    pose: Pose, pos: np.ndarray, neg: np.ndarray, steps: np.ndarray
) -> Pose:
    """Put the mapped POSITIVE lattice onto the NEGATIVE one when the two lattices are commensurate.

    A rotation and translation are fitted to the tiles' sub-pixel lattice offsets
    (:func:`lattice_offsets`); a half-pitch displacement is settled on one consistent side. When
    fewer than half the tiles, or fewer than three, are coherent (rotated lattices) the pose is kept.
    """
    for _ in range(2):
        centres, offsets, weights, examined = lattice_offsets(
            pose.apply(pos), neg, steps, _PAIRING_SNAP_COHERENCE
        )
        if len(centres) < max(3, 0.5 * examined):
            return pose
        pose = _corrected(pose, rigid_fit(centres, centres + offsets, weights))
    return pose


def _scales(pos: np.ndarray, neg: np.ndarray, steps: np.ndarray) -> tuple[float, float]:
    """Coarse cell and high-pass sigma (micrometres), bounded by the shorter footprint side."""
    short = min(np.ptp(pos, axis=0).min(), np.ptp(neg, axis=0).min())
    big = float(steps.max())
    coarse_cell = max(
        big, min(_PAIRING_COARSE_PITCHES * big, short / _PAIRING_MIN_CELLS)
    )
    return coarse_cell, min(_PAIRING_HIGHPASS_PITCHES * big, short / 6)


def _disagreement(
    coarse: _Search, mirrors: tuple[bool, ...], pose: Pose, pos: np.ndarray, cell: float
) -> float:
    """Largest median distance, in coarse cells, between the final pose and the coarse pose found
    with the even or with the odd components."""
    k = coarse.Cp.shape[1]
    worst = 0.0
    for half in (slice(0, k, 2), slice(1, k, 2)):
        found = _best_of(_coarse(coarse, mirrors, half)).pose
        distance = np.median(np.linalg.norm(found.apply(pos) - pose.apply(pos), axis=1))
        worst = max(worst, float(distance) / cell)
    return worst


def register(
    pos: np.ndarray,
    neg: np.ndarray,
    pos_bands: np.ndarray,
    neg_bands: np.ndarray,
    steps: np.ndarray,
    allow_mirror: bool,
) -> Registration:
    """Register the POSITIVE positions onto the NEGATIVE ones from their spectral content.

    Raises PolarityPairingError when the pose is not clearly better than every alternative, or when
    the two halves of the components disagree on it.
    """
    Cp, Cn = components(pos_bands), components(neg_bands)
    coarse_cell, sigma = _scales(pos, neg, steps)
    mirrors = (False, True) if allow_mirror else (False,)
    coarse = _Search(pos, neg, Cp, Cn, np.full(2, coarse_cell), sigma)
    candidates = _coarse(coarse, mirrors)
    best = _best_of(candidates)
    margin = best.score / max(_competitor(candidates, best), 1e-12)

    middle = _Search(pos, neg, Cp, Cn, np.full(2, 2 * float(steps.max())), sigma)
    mid = _refine(middle, best, 3.0, 0.25, 2 * coarse_cell)
    pose = _tile_fit(mid.pose, pos, neg, Cp, Cn, steps, sigma)
    pose = _snap_to_lattice(pose, pos, neg, steps)

    disagreement = _disagreement(coarse, mirrors, pose, pos, coarse_cell)
    if margin < _PAIRING_MIN_MARGIN or disagreement > _PAIRING_MAX_DISAGREEMENT:
        raise PolarityPairingError(
            f"POSITIVE and NEGATIVE could not be registered with confidence: best pose "
            f"{_describe(pose)}, margin {margin:.2f} over the next best pose (at least "
            f"{_PAIRING_MIN_MARGIN} required), component halves disagree by {disagreement:.1f} "
            f"coarse cells (at most {_PAIRING_MAX_DISAGREEMENT} allowed)",
            margin=float(margin),
        )
    return Registration(pose, float(margin), disagreement, best.overlap)


def pair(
    pos: np.ndarray, neg: np.ndarray, pose: Pose, steps: np.ndarray
) -> tuple[NDArray[np.intp], NDArray[np.intp], NDArray[np.float64]]:
    """Pair each mapped POSITIVE position with its nearest NEGATIVE one within half a pixel diagonal.

    A NEGATIVE spectrum claimed twice goes to the closer POSITIVE one. Returns the paired indices in
    ascending POSITIVE order and the distance of each pair.
    """
    tree = cKDTree(neg)
    distance, partner = cast(
        tuple[np.ndarray, np.ndarray],
        tree.query(pose.apply(pos), distance_upper_bound=0.5 * float(np.hypot(*steps))),
    )
    order = np.argsort(distance, kind="stable")
    order = order[np.isfinite(distance[order])]
    _, first = np.unique(partner[order], return_index=True)
    pos_indices = np.sort(order[first])
    return pos_indices, partner[pos_indices], distance[pos_indices]


def _describe(pose: Pose) -> str:
    """Rotation, translation and handedness of a pose, for messages."""
    mirrored = ", x mirrored" if pose.mirror else ""
    return (
        f"rotation {np.rad2deg(pose.angle):+.2f} deg, translation "
        f"({pose.translation[0]:+.1f}, {pose.translation[1]:+.1f}) um{mirrored}"
    )


def describe(registration: Registration) -> str:
    """One-line summary of an accepted registration."""
    return f"{_describe(registration.pose)}, margin {registration.margin:.2f}"
