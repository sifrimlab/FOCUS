"""Fit and apply the original anchor-to-target geometric transform."""

from numpy.typing import NDArray

from typing import cast

import logging
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from focus.spatial.footprint_shape import _EPS32


logger = logging.getLogger("focus.registration._footprint")


_MAX_FIT_POINTS = 4096

_LOCAL_NEIGHBOURS = 8


# --------------------------------------------------------------------------- transform


def _apply_homography(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Map ``(K, 2)`` points; points that land on or behind the horizon (w <= 0) become NaN."""
    hom = pts @ matrix[:, :2].T + matrix[:, 2]
    w = hom[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = hom[:, :2] / w
    out[(w[:, 0] <= 0) | ~np.isfinite(w[:, 0])] = np.nan
    return cast(np.ndarray, out)


def _normalising_similarity(pts: np.ndarray) -> np.ndarray:
    """Hartley normalisation: centroid to the origin, mean distance sqrt(2)."""
    centre = pts.mean(axis=0)
    spread = np.sqrt(((pts - centre) ** 2).sum(axis=1)).mean()
    scale = np.sqrt(2.0) / spread if spread > 0 else 1.0
    return np.array(
        [
            [scale, 0.0, -scale * centre[0]],
            [0.0, scale, -scale * centre[1]],
            [0.0, 0.0, 1.0],
        ]
    )


def _fit_homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Normalised direct linear transform (Hartley 1997), scaled so w = 1 at the source centroid."""
    t_src, t_dst = _normalising_similarity(src), _normalising_similarity(dst)
    s = src @ t_src[:2, :2].T + t_src[:2, 2]
    d = dst @ t_dst[:2, :2].T + t_dst[:2, 2]
    n = len(s)
    a = np.zeros((2 * n, 9))
    a[0::2, 0:2], a[0::2, 2] = -s, -1.0
    a[0::2, 6:8], a[0::2, 8] = s * d[:, :1], d[:, 0]
    a[1::2, 3:5], a[1::2, 5] = -s, -1.0
    a[1::2, 6:8], a[1::2, 8] = s * d[:, 1:], d[:, 1]
    h = np.linalg.svd(a, full_matrices=False)[2][-1].reshape(3, 3)
    matrix = np.linalg.inv(t_dst) @ h @ t_src
    w = matrix[2] @ np.append(src.mean(axis=0), 1.0)
    return cast(np.ndarray, matrix / w)


def _homography_jacobian(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """``(K, 2, 2)`` Jacobian of the homography at ``pts``."""
    hom = pts @ matrix[:, :2].T + matrix[:, 2]
    w = hom[:, 2]
    u = hom[:, :2] / w[:, None]
    return cast(
        np.ndarray,
        (matrix[None, :2, :2] - u[:, :, None] * matrix[None, 2:3, :2])
        / w[:, None, None],
    )


@dataclass(frozen=True)
class AnchorToTarget:
    """Map between the anchor frame and the target frame.

    ``kind`` is ``identity``, ``translation`` or ``homography`` (one 3x3 ``matrix`` for every
    spot) or ``local_affine`` (one affine per anchor spot, ``local_a`` / ``local_b``, used when
    the stored coordinates were not produced by a single matrix).
    """

    kind: str
    matrix: NDArray[np.float64] | None
    inverse: NDArray[np.float64] | None
    local_a: NDArray[np.float64] | None
    local_a_inv: NDArray[np.float64] | None
    local_b: NDArray[np.float64] | None
    max_residual: float

    def to_target(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        """Map anchor points into target coordinates using the fitted transform."""
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return cast(
                np.ndarray,
                np.einsum("kij,kj->ki", cast(np.ndarray, self.local_a)[rows], pts)
                + cast(np.ndarray, self.local_b)[rows],
            )
        return _apply_homography(cast(np.ndarray, self.matrix), pts)

    def to_anchor(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        """Map target points back into the anchor coordinate frame."""
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return cast(
                np.ndarray,
                np.einsum(
                    "kij,kj->ki",
                    cast(np.ndarray, self.local_a_inv)[rows],
                    pts - cast(np.ndarray, self.local_b)[rows],
                ),
            )
        return _apply_homography(cast(np.ndarray, self.inverse), pts)

    def jacobian(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        """``(K, 2, 2)`` derivative of anchor -> target at ``pts`` (target units per anchor unit)."""
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return cast(np.ndarray, self.local_a)[rows]
        return _homography_jacobian(cast(np.ndarray, self.matrix), pts)


def _matrix_transform(kind: str, matrix: np.ndarray, residual: float) -> AnchorToTarget:
    """Construct a global transform and its inverse with the measured residual."""
    return AnchorToTarget(
        kind, matrix, np.linalg.inv(matrix), None, None, None, residual
    )


def _local_affine(
    src: np.ndarray, dst: np.ndarray, valid: np.ndarray
) -> AnchorToTarget:
    """One affine per anchor, fitted to its nearest valid neighbours and pinned to its own pair."""
    n = len(src)
    s, d = src[valid], dst[valid]
    k = min(_LOCAL_NEIGHBOURS + 1, len(s))
    _, nb = cKDTree(s).query(s, k=k)
    ds = s[nb] - s[:, None, :]  # (m, k, 2) neighbour offsets, anchor frame
    dd = d[nb] - d[:, None, :]  # (m, k, 2) the same offsets, target frame
    gram = np.einsum("mki,mkj->mij", ds, ds)
    cross = np.einsum("mki,mkj->mij", ds, dd)
    design = np.column_stack([s, np.ones(len(s))])
    global_fit = np.linalg.lstsq(design, d, rcond=None)[0]
    a = np.repeat(global_fit[:2].T[None], len(s), axis=0)
    ok = np.abs(np.linalg.det(gram)) > 1e-12 * np.einsum("mii->m", gram) ** 2
    a[ok] = np.transpose(np.linalg.solve(gram[ok], cross[ok]), (0, 2, 1))
    local_a = np.full((n, 2, 2), np.nan)
    local_a[valid] = a
    local_b = np.full((n, 2), np.nan)
    local_b[valid] = d - np.einsum("mij,mj->mi", a, s)
    with np.errstate(invalid="ignore"):
        local_a_inv = np.full((n, 2, 2), np.nan)
        invertible = valid.copy()
        invertible[valid] = np.abs(np.linalg.det(a)) > 0
        local_a_inv[invertible] = np.linalg.inv(local_a[invertible])
    return AnchorToTarget(
        "local_affine", None, None, local_a, local_a_inv, local_b, 0.0
    )


def fit_anchor_to_target(
    src: np.ndarray,
    dst: np.ndarray,
    spot_size: np.ndarray,
    nonrigid: bool = False,
) -> tuple[AnchorToTarget, np.ndarray]:
    """
    Recover the anchor -> target map from the stored correspondences.

    The alignment step maps every anchor spot with one 3x3 matrix (a similarity, possibly with
    reflection, or a projective transform), then rescales each axis to full resolution, so a
    single homography reproduces ``dst`` exactly. A normalised DLT fit on a seeded subsample is
    verified against every valid row; when it does not reproduce them within tolerance (stored
    coordinates not produced by one matrix) a per-anchor local affine is used instead.

    Parameters
    ----------
    src : np.ndarray
        ``(N, 2)`` anchor-native coordinates (``obsm['spatial']``).
    dst : np.ndarray
        ``(N, 2)`` the same spots in the target frame (``obsm['{target}_spatial']``).
    spot_size : np.ndarray
        ``(2,)`` anchor spot size, used to scale the verification tolerance.
    nonrigid : bool
        The alignment stored a non-rigid (landmark) warp, so the local affine map is the
        expected outcome and is logged without a warning.

    Returns
    -------
    transform : AnchorToTarget
    valid : np.ndarray
        ``(N,)`` bool, rows with finite correspondences.

    Raises
    ------
    ValueError
        Fewer than 3 valid spots, or all valid spots collinear.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    valid = np.isfinite(src).all(axis=1) & np.isfinite(dst).all(axis=1)
    if valid.sum() < 3:
        raise ValueError("fewer than 3 spots have finite aligned coordinates")
    s, d = src[valid], dst[valid]
    centred = s - s.mean(axis=0)
    sv = np.linalg.svd(centred, compute_uv=False)
    if sv[1] <= 1e-9 * max(sv[0], 1e-300):
        raise ValueError(
            "the aligned spots are collinear, so the alignment transform is undetermined"
        )

    storage_tol = 8.0 * _EPS32 * max(float(np.abs(d).max()), 1.0)
    delta = d - s
    if not np.any(delta):
        return _matrix_transform("identity", np.eye(3), 0.0), valid
    shift = np.median(delta, axis=0)
    if np.abs(delta - shift).max() <= storage_tol:
        matrix = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]], [0.0, 0.0, 1.0]])
        return _matrix_transform(
            "translation", matrix, float(np.abs(delta - shift).max())
        ), valid

    rng = np.random.default_rng(0)
    n = len(s)
    fit_idx = np.arange(n)
    if n > _MAX_FIT_POINTS:
        extremes = [
            s[:, 0].argmin(),
            s[:, 0].argmax(),
            s[:, 1].argmin(),
            s[:, 1].argmax(),
        ]
        fit_idx = np.unique(
            np.concatenate([rng.choice(n, _MAX_FIT_POINTS, replace=False), extremes])
        )
    matrix = _fit_homography(s[fit_idx], d[fit_idx])

    scale = float(
        np.sqrt(
            abs(
                np.linalg.det(
                    _homography_jacobian(matrix, s.mean(axis=0, keepdims=True))[0]
                )
            )
        )
    )
    tol = max(1e-3 * float(np.sqrt(np.prod(spot_size))) * scale, storage_tol)
    residual = float(
        np.nanmax(np.linalg.norm(_apply_homography(matrix, s) - d, axis=1))
    )
    if np.isfinite(residual) and residual <= tol:
        return _matrix_transform("homography", matrix, residual), valid

    _report_local_affine(nonrigid, residual, tol)
    return _local_affine(src, dst, valid), valid


def _report_local_affine(nonrigid: bool, residual: float, tol: float) -> None:
    """Log the fallback to a per-spot affine map: expected for a landmark warp, else a warning."""
    if nonrigid:
        logger.info("Landmark-warped alignment: using a local affine map per spot")
        return
    logger.warning(
        "The aligned coordinates are not one projective transform of the anchor "
        f"coordinates (max residual {residual:.3g} > {tol:.3g}): using a local affine "
        "map per spot"
    )
