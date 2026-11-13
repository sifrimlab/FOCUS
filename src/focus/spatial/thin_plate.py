"""
Residual maps of the landmark warp: translation, similarity or regularised thin-plate spline.

A residual map ``R`` is fitted from landmark positions placed by the pair matrix (``p``) onto
where the operator pinned them (``q``):

- 0 landmarks: identity; 1: translation; 2 (or collinear): similarity;
- 3+: a thin-plate spline ``r^2 log r`` with an affine part and smoothing ``lambda`` on the
  kernel diagonal, fitted in coordinates centred on the landmarks and scaled by their RMS
  spread, so ``lambda`` has no units.

``gui_src/alignment/src/utils/warp.ts`` implements the same maths for the live preview.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import ArrayLike, NDArray

_COLLINEAR_TOL = 1e-9
# An outline anchor within this fraction of the outline's diagonal of a landmark is released, so
# a landmark near the border can still move it.
ANCHOR_RELEASE = 0.15
_CHUNK = 32768  # rows per kernel block: bounds the (rows, landmarks, 2) temporaries

Points = NDArray[np.float64]


def _kernel(r2: np.ndarray) -> np.ndarray:
    """Thin-plate kernel ``r^2 log r`` from squared distances (0 at r = 0)."""
    out = np.zeros_like(r2)
    pos = r2 > 0
    out[pos] = 0.5 * r2[pos] * np.log(r2[pos])
    return out


def _sq_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """``(len(a), len(b))`` squared distances."""
    d = a[:, None, :] - b[None, :, :]
    return cast(np.ndarray, np.einsum("ijk,ijk->ij", d, d))


def apply_homography(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply a row-major 3x3 projective matrix to ``(N, 2)`` points (w = 0 is left undivided)."""
    hom = np.column_stack([pts, np.ones(len(pts))]) @ matrix.T
    w = hom[:, 2:3].copy()
    w[w == 0] = 1.0
    return cast(np.ndarray, hom[:, :2] / w)


def homography_jacobian(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """``(N, 2, 2)`` Jacobian of a projective matrix at ``pts``."""
    hom = np.column_stack([pts, np.ones(len(pts))]) @ matrix.T
    w = hom[:, 2].copy()
    w[w == 0] = 1.0
    xy = hom[:, :2] / w[:, None]
    # d(xy_i)/d(x_j) = (H[i, j] - xy_i H[2, j]) / w
    jac = matrix[None, :2, :2] - xy[:, :, None] * matrix[None, 2:3, :2]
    return cast(np.ndarray, jac / w[:, None, None])


@dataclass(frozen=True)
class ResidualMap:
    """
    The residual part ``R`` of a landmark warp, fitted from ``p -> q``.

    ``kind`` is ``identity``, ``translation``, ``similarity`` or ``tps``. The TPS acts on
    ``u = (x - centre) / spread`` and returns ``centre + spread * f(u)``.
    """

    kind: str
    centre: np.ndarray
    spread: float
    nodes: np.ndarray  # (n, 2) normalised TPS nodes (empty unless tps)
    weights: np.ndarray  # (n, 2) kernel weights
    affine: (
        np.ndarray
    )  # (3, 2): rows [1, ux, uy] -> output (normalised for tps, raw otherwise)

    @classmethod
    def fit(cls, p: ArrayLike, q: ArrayLike, smoothing: float) -> ResidualMap:
        """Fit the residual map sending ``p`` onto ``q`` (``(n, 2)`` each)."""
        p = np.asarray(p, dtype=np.float64).reshape(-1, 2)
        q = np.asarray(q, dtype=np.float64).reshape(-1, 2)
        n = len(p)
        empty = np.zeros((0, 2))
        if n == 0:
            return cls("identity", np.zeros(2), 1.0, empty, empty, _affine_identity())
        if n == 1:
            a = _affine_identity()
            a[0] = q[0] - p[0]
            return cls("translation", np.zeros(2), 1.0, empty, empty, a)
        centre = p.mean(axis=0)
        spread = float(np.sqrt(np.mean(np.sum((p - centre) ** 2, axis=1))))
        if not np.isfinite(spread) or spread <= 0:
            a = _affine_identity()
            a[0] = (q - p).mean(axis=0)
            return cls("translation", np.zeros(2), 1.0, empty, empty, a)
        u = (p - centre) / spread
        v = (q - centre) / spread
        if n == 2 or _collinear(u):
            return cls(
                "similarity", centre, spread, empty, empty, _similarity_fit(u, v)
            )
        k = _kernel(_sq_dist(u, u)) + float(smoothing) * np.eye(n)
        poly = np.column_stack([np.ones(n), u])
        system = np.zeros((n + 3, n + 3))
        system[:n, :n] = k
        system[:n, n:] = poly
        system[n:, :n] = poly.T
        rhs = np.zeros((n + 3, 2))
        rhs[:n] = v
        try:
            sol = np.linalg.solve(system, rhs)
        except np.linalg.LinAlgError:
            return cls(
                "similarity", centre, spread, empty, empty, _similarity_fit(u, v)
            )
        return cls("tps", centre, spread, u, sol[:n], sol[n:])

    def _normalised(self, x: np.ndarray) -> np.ndarray:
        """Map raw coordinates into the fit's normalised frame."""
        return cast(np.ndarray, (x - self.centre) / self.spread)

    def __call__(self, x: ArrayLike) -> Points:
        """Apply the residual map to ``(N, 2)`` points."""
        x = np.asarray(x, dtype=np.float64).reshape(-1, 2)
        if self.kind in ("identity", "translation"):
            return cast(Points, x + self.affine[0])
        u = self._normalised(x)
        out = self.affine[0] + u @ self.affine[1:]
        if self.kind == "tps":
            for lo in range(0, len(u), _CHUNK):
                hi = lo + _CHUNK
                out[lo:hi] += _kernel(_sq_dist(u[lo:hi], self.nodes)) @ self.weights
        return cast(Points, self.centre + self.spread * out)

    def jacobian(self, x: ArrayLike) -> np.ndarray:
        """``(N, 2, 2)`` Jacobian of the residual map (scale-free: normalisation cancels)."""
        x = np.asarray(x, dtype=np.float64).reshape(-1, 2)
        jac = np.broadcast_to(self.affine[1:].T, (len(x), 2, 2)).copy()
        if self.kind != "tps":
            return jac
        u = self._normalised(x)
        for lo in range(0, len(u), _CHUNK):
            hi = lo + _CHUNK
            d = u[lo:hi, None, :] - self.nodes[None, :, :]  # (m, n, 2)
            r2 = np.einsum("ijk,ijk->ij", d, d)
            g = np.zeros_like(r2)
            pos = r2 > 0
            g[pos] = np.log(r2[pos]) + 1.0  # d/du (r^2 log r) = (2 log r + 1) (u - u_i)
            # jac[i, a, b] += sum_k w[k, a] * g[i, k] * d[i, k, b]
            jac[lo:hi] += np.einsum("ka,ik,ikb->iab", self.weights, g, d)
        return jac


def _affine_identity() -> np.ndarray:
    """Affine block of the identity: rows ``[offset, d/dx, d/dy]``."""
    return np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])


def _collinear(u: np.ndarray) -> bool:
    """True when normalised points lie (almost) on a line."""
    sv = np.linalg.svd(u - u.mean(axis=0), compute_uv=False)
    return bool(sv[-1] <= _COLLINEAR_TOL * max(sv[0], 1e-300))


def _similarity_fit(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Least-squares similarity ``v = a u + b`` (complex form), as an affine block."""
    zu = u[:, 0] + 1j * u[:, 1]
    zv = v[:, 0] + 1j * v[:, 1]
    mu, mv = zu.mean(), zv.mean()
    du, dv = zu - mu, zv - mv
    den = np.vdot(du, du).real
    a = np.vdot(du, dv) / den if den > 0 else 1.0 + 0j
    b = mv - a * mu
    return np.array([[b.real, b.imag], [a.real, a.imag], [-a.imag, a.real]])


def border_anchors(lo: ArrayLike, hi: ArrayLike) -> Points:
    """``(8, 2)`` outline anchors of a box: its corners, then its edge midpoints."""
    (x0, y0), (x1, y1) = (
        np.asarray(lo, dtype=np.float64),
        np.asarray(hi, dtype=np.float64),
    )
    xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
    return np.array(
        [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [xm, y0], [x1, ym], [xm, y1], [x0, ym]]
    )


def active_anchors(anchors: ArrayLike, src: ArrayLike) -> Points:
    """
    The outline anchors that hold: none without landmarks, and none within
    ``ANCHOR_RELEASE`` of the outline's diagonal from a landmark (order preserved).
    """
    a = np.asarray(anchors, dtype=np.float64).reshape(-1, 2)
    s = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    if not len(a) or not len(s):
        return a[:0]
    radius = ANCHOR_RELEASE * float(np.hypot(*np.ptp(a, axis=0)))
    nearest = np.sqrt(_sq_dist(a, s).min(axis=1))
    return cast(Points, a[nearest > radius])


def anchored_pairs(
    matrix: np.ndarray, src: np.ndarray, dst: np.ndarray, anchors: ArrayLike
) -> tuple[Points, Points]:
    """
    Residual-fit pairs ``p -> q``: the landmarks placed by ``matrix`` onto their pins, plus the
    held outline anchors onto themselves (zero residual), so a landmark bends the tissue
    around it while the outline stays where the matrix puts it.
    """
    act = active_anchors(anchors, src)
    p = apply_homography(matrix, np.vstack([src, act]))
    q = np.vstack([dst, apply_homography(matrix, act)])
    return p, q


def leave_one_out(
    matrix: np.ndarray,
    src: np.ndarray,
    dst: np.ndarray,
    smoothing: float,
    anchors: ArrayLike | None = None,
) -> np.ndarray:
    """
    ``(n,)`` display-pixel error of each landmark predicted from all the others.

    The outline anchors held by the full set of landmarks stay in every fit. A large value flags
    a landmark that disagrees with its neighbours. NaN with fewer than 2 landmarks.
    """
    src = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    dst = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    n = len(src)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    act = active_anchors(np.zeros((0, 2)) if anchors is None else anchors, src)
    p = apply_homography(matrix, src)
    pa = apply_homography(matrix, act)
    for i in range(n):
        keep = np.arange(n) != i
        fit = ResidualMap.fit(
            np.vstack([p[keep], pa]), np.vstack([dst[keep], pa]), smoothing
        )
        out[i] = float(np.linalg.norm(fit(p[i : i + 1])[0] - dst[i]))
    return out
