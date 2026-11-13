"""
Landmark warp of the alignment tool: a homography refined by a regularised thin-plate spline.

The browser keeps one 3x3 matrix ``H`` per pair (the global placement) and, optionally, landmark
pairs. Each landmark is attached to the moving layer at a native position ``src`` and pinned to a
fixed-layer display position ``dst``. The warp is

    W(x) = R(H x)

where ``R`` is a residual map fitted from ``H src -> dst``.

``anchors`` are points on the moving layer's outline (corners and edge midpoints) held where
``H`` puts them (zero residual), except those near a landmark. With them, a landmark bends the
tissue around it while the overall placement stays with ``H``; the residual is a thin-plate
spline with an affine part and smoothing ``lambda`` on the kernel diagonal, fitted in
coordinates centred on the fit points and scaled by their RMS spread, so ``lambda`` has no units
(see :mod:`focus.spatial.thin_plate`). Without anchors (warps stored before they existed) the
residual is a translation for 1 landmark and a similarity for 2.

``gui_src/alignment/src/utils/warp.ts`` implements the same maths for the live preview; the
parity fixture ``gui_src/alignment/tests/fixtures/warp_parity.json`` keeps the two identical.

Exclusion polygons mark tissue with no counterpart in the other section (tears, folds, missing
pieces). Moving-layer polygons are in moving-native coordinates, fixed-layer polygons in
fixed-layer display coordinates; a point inside either maps to NaN.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, cast

import numpy as np
import shapely
from numpy.typing import ArrayLike, NDArray

from focus.spatial.thin_plate import (
    Points,
    ResidualMap,
    anchored_pairs,
    apply_homography,
    homography_jacobian,
)

WARP_VERSION = 1
DEFAULT_SMOOTHING = 1e-3
_NEWTON_ITERATIONS = 30


def _polygons(raw: object) -> list[np.ndarray]:
    """Normalise a JSON list of polygons into ``(k, 2)`` arrays with at least 3 vertices."""
    out: list[np.ndarray] = []
    for poly in cast(Iterable[ArrayLike], raw or []):
        arr = np.asarray(poly, dtype=np.float64).reshape(-1, 2)
        if len(arr) >= 3 and np.isfinite(arr).all():
            out.append(arr)
    return out


def _points(raw: object) -> np.ndarray:
    """Finite ``(k, 2)`` points of a JSON list (empty when absent)."""
    arr = np.asarray(raw if raw is not None else [], dtype=np.float64).reshape(-1, 2)
    return arr[np.isfinite(arr).all(axis=1)]


def inside_any(polygons: list[np.ndarray], pts: np.ndarray) -> NDArray[np.bool_]:
    """``(N,)`` True where a point lies inside (or on) any polygon; NaN points are False."""
    mask = np.zeros(len(pts), dtype=bool)
    if not polygons or not len(pts):
        return mask
    finite = np.isfinite(pts).all(axis=1)
    geoms = [shapely.make_valid(shapely.Polygon(p)) for p in polygons]
    area = shapely.union_all(geoms)
    xy = pts[finite]
    mask[finite] = shapely.intersects_xy(area, xy[:, 0], xy[:, 1])
    return mask


@dataclass(frozen=True)
class LandmarkWarp:
    """
    Moving-native -> fixed-layer coordinates as confirmed in the alignment tool.

    ``matrix`` (row-major) and the residual map produce fixed-layer *display* coordinates;
    ``fixed_scale`` ``(sx, sy)`` converts them to the fixed modality's full-resolution frame.
    """

    matrix: np.ndarray
    src: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    dst: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    smoothing: float = DEFAULT_SMOOTHING
    moving_exclusions: list[np.ndarray] = field(default_factory=list)
    fixed_exclusions: list[np.ndarray] = field(default_factory=list)
    fixed_scale: np.ndarray = field(default_factory=lambda: np.ones(2))
    anchors: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))

    @classmethod
    def from_browser(
        cls,
        column_major: ArrayLike,
        landmarks: Mapping[str, Any] | None = None,
        smoothing: float | None = None,
        exclusions: Mapping[str, Any] | None = None,
        fixed_scale: ArrayLike = (1.0, 1.0),
    ) -> LandmarkWarp:
        """Build from the browser payload (gl-matrix column-major ``transform_matrix``)."""
        matrix = np.asarray(column_major, dtype=np.float64).reshape(3, 3, order="F")
        landmarks = landmarks or {}
        exclusions = exclusions or {}
        src = np.asarray(landmarks.get("src") or [], dtype=np.float64).reshape(-1, 2)
        dst = np.asarray(landmarks.get("dst") or [], dtype=np.float64).reshape(-1, 2)
        if len(src) != len(dst):
            raise ValueError(
                f"landmark source and destination counts differ ({len(src)} vs {len(dst)})"
            )
        keep = np.isfinite(src).all(axis=1) & np.isfinite(dst).all(axis=1)
        return cls(
            matrix=matrix,
            src=src[keep],
            dst=dst[keep],
            smoothing=DEFAULT_SMOOTHING if smoothing is None else float(smoothing),
            moving_exclusions=_polygons(exclusions.get("moving")),
            fixed_exclusions=_polygons(exclusions.get("fixed")),
            fixed_scale=np.asarray(fixed_scale, dtype=np.float64).reshape(2),
            anchors=_points(landmarks.get("anchors")),
        )

    # --- Model ---

    @cached_property
    def _pairs(self) -> tuple[Points, Points]:
        """Residual-fit pairs: landmarks onto their pins, held anchors onto themselves."""
        return anchored_pairs(self.matrix, self.src, self.dst, self.anchors)

    @cached_property
    def residual(self) -> ResidualMap:
        """Residual map fitted from the landmarks (and held anchors) placed by ``matrix``."""
        p, q = self._pairs
        return ResidualMap.fit(p, q, self.smoothing)

    @cached_property
    def _reverse(self) -> ResidualMap:
        """Residual fitted in the reverse direction: an initial guess for the inverse."""
        p, q = self._pairs
        return ResidualMap.fit(q, p, self.smoothing)

    @property
    def n_landmarks(self) -> int:
        """Number of landmark pairs."""
        return len(self.src)

    @property
    def is_nonrigid(self) -> bool:
        """True when the warp is not a single projective transform (a TPS is in use)."""
        return self.residual.kind == "tps"

    @property
    def has_exclusions(self) -> bool:
        """True when any exclusion polygon was drawn."""
        return bool(self.moving_exclusions or self.fixed_exclusions)

    # --- Display frame ---

    def to_fixed(self, pts: ArrayLike) -> Points:
        """Moving-native -> fixed display coordinates, ignoring exclusions."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        return self.residual(apply_homography(self.matrix, pts))

    def jacobian(self, pts: ArrayLike) -> np.ndarray:
        """``(N, 2, 2)`` Jacobian of :meth:`to_fixed`."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        outer = self.residual.jacobian(apply_homography(self.matrix, pts))
        return cast(np.ndarray, outer @ homography_jacobian(self.matrix, pts))

    def folded(self, pts: ArrayLike) -> NDArray[np.bool_]:
        """True where the residual map turns tissue inside out (its Jacobian determinant <= 0)."""
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        det = np.linalg.det(self.residual.jacobian(apply_homography(self.matrix, pts)))
        return cast(NDArray[np.bool_], ~(det > 0))

    def from_fixed(self, y: ArrayLike, tol: float = 1e-6) -> Points:
        """
        Fixed display -> moving-native: the exact inverse of :meth:`to_fixed`.

        Starts from the reverse-fitted residual and the inverse matrix, then runs Newton
        iterations on :meth:`to_fixed`. Points that do not converge to ``tol`` display pixels
        (outside a fold, or where the matrix is singular) are NaN.
        """
        y = np.asarray(y, dtype=np.float64).reshape(-1, 2)
        out = np.full_like(y, np.nan)
        ok = np.isfinite(y).all(axis=1)
        if not ok.any():
            return out
        target = y[ok]
        x = apply_homography(np.linalg.inv(self.matrix), self._reverse(target))
        if self.residual.kind != "tps":
            # Identity/translation/similarity compose with H into one projective matrix:
            # the closed form above is already exact up to rounding; refine once anyway.
            iterations = 2
        else:
            iterations = _NEWTON_ITERATIONS
        scale = max(1.0, float(np.abs(target).max()))
        err = np.full(len(target), np.inf)
        for _ in range(iterations):
            fx = self.to_fixed(x)
            r = fx - target
            err = np.linalg.norm(r, axis=1)
            active = np.isfinite(err) & (err > tol * 1e-3 * scale)
            if not active.any():
                break
            jac = self.jacobian(x[active])
            det = np.linalg.det(jac)
            good = np.abs(det) > 1e-300
            idx = np.flatnonzero(active)[good]
            step = np.linalg.solve(jac[good], r[active][good][:, :, None])[:, :, 0]
            x[idx] -= step
        err = np.linalg.norm(self.to_fixed(x) - target, axis=1)
        x[~(err <= tol * scale)] = np.nan
        out[ok] = x
        return out

    def excluded(self, moving: np.ndarray, fixed: np.ndarray) -> NDArray[np.bool_]:
        """True where a moving-native point or its fixed display image is excluded."""
        return inside_any(self.moving_exclusions, moving) | inside_any(
            self.fixed_exclusions, fixed
        )

    # --- Full-resolution frame ---

    def map_full(self, pts: ArrayLike) -> Points:
        """
        Moving-native -> fixed full-resolution coordinates, NaN where excluded.

        This is what the alignment step stores as ``obsm['{fixed}_spatial']``.
        """
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
        disp = self.to_fixed(pts)
        disp[self.excluded(pts, disp)] = np.nan
        return cast(Points, disp * self.fixed_scale)

    def inverse_full(self, y_full: ArrayLike) -> Points:
        """Fixed full-resolution -> moving-native coordinates, NaN where excluded."""
        y = np.asarray(y_full, dtype=np.float64).reshape(-1, 2) / self.fixed_scale
        x = self.from_fixed(y)
        x[self.excluded(x, y)] = np.nan
        return x

    def rescaled(self, fixed_scale: ArrayLike) -> LandmarkWarp:
        """
        The same map for a fixed layer displayed at another pyramid level.

        ``fixed_scale`` ``(sx, sy)`` is the new display -> full-resolution factor; the matrix,
        the landmark destinations and the fixed-layer exclusions move to the new display frame.
        """
        new = np.asarray(fixed_scale, dtype=np.float64).reshape(2)
        factor = self.fixed_scale / new
        if np.array_equal(factor, np.ones(2)):
            return self
        return LandmarkWarp(
            matrix=np.diag([factor[0], factor[1], 1.0]) @ self.matrix,
            src=self.src,
            dst=self.dst * factor,
            smoothing=self.smoothing,
            moving_exclusions=self.moving_exclusions,
            fixed_exclusions=[p * factor for p in self.fixed_exclusions],
            fixed_scale=new,
            anchors=self.anchors,
        )

    # --- Serialisation ---

    def to_dict(self) -> dict[str, object]:
        """JSON-ready state; also the browser's initial state when re-aligning."""
        return {
            "version": WARP_VERSION,
            "matrix": self.matrix.reshape(-1).tolist(),  # row-major
            "landmarks": {
                "src": self.src.tolist(),
                "dst": self.dst.tolist(),
                "anchors": self.anchors.tolist(),
            },
            "smoothing": self.smoothing,
            "exclusions": {
                "moving": [p.tolist() for p in self.moving_exclusions],
                "fixed": [p.tolist() for p in self.fixed_exclusions],
            },
            "fixed_scale": self.fixed_scale.tolist(),
        }

    def to_json(self) -> str:
        """Serialise :meth:`to_dict` to a compact JSON string."""
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LandmarkWarp:
        """Inverse of :meth:`to_dict`."""
        version = int(data.get("version", 0))
        if version != WARP_VERSION:
            raise ValueError(f"unsupported landmark warp version {version}")
        matrix = np.asarray(data["matrix"], dtype=np.float64).reshape(3, 3)
        landmarks = data.get("landmarks") or {}
        exclusions = data.get("exclusions") or {}
        return cls(
            matrix=matrix,
            src=np.asarray(landmarks.get("src") or [], dtype=np.float64).reshape(-1, 2),
            dst=np.asarray(landmarks.get("dst") or [], dtype=np.float64).reshape(-1, 2),
            smoothing=float(data.get("smoothing", DEFAULT_SMOOTHING)),
            moving_exclusions=_polygons(exclusions.get("moving")),
            fixed_exclusions=_polygons(exclusions.get("fixed")),
            fixed_scale=np.asarray(
                data.get("fixed_scale", [1.0, 1.0]), dtype=np.float64
            ).reshape(2),
            anchors=_points(landmarks.get("anchors")),
        )

    @classmethod
    def from_json(cls, text: str) -> LandmarkWarp:
        """Inverse of :meth:`to_json`."""
        return cls.from_dict(json.loads(text))


def stored_warp_is_nonrigid(raw: object) -> bool:
    """
    True when a stored warp (JSON from ``uns['focus_alignment_warps']``) is not one
    projective transform: a thin-plate spline, or a composition through a guide modality.
    """
    if raw is None:
        return False
    text = raw.decode() if isinstance(raw, bytes) else str(raw)
    try:
        data = json.loads(text)
        return bool(data.get("guide")) or LandmarkWarp.from_dict(data).is_nonrigid
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
