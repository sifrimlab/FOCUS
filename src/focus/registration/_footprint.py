"""
Footprint geometry shared by every registration engine (pure NumPy/SciPy, no torch).

An anchor spot's footprint is defined in the anchor modality's own frame, where
``uns['spot_size']`` and ``obsm['spatial']`` share units. Registration evaluates every
footprint there: target elements (spots, pixels, encoder tokens) are mapped back into the
anchor frame through the inverse of the alignment transform, which is recovered from the
correspondences the alignment step stored, ``obsm['spatial']`` -> ``obsm['{target}_spatial']``.

Conventions
-----------
- Image pixel ``(col, row)`` covers ``[col, col + 1) x [row, row + 1)``; its point is its
  centre ``(col + 0.5, row + 0.5)``. This is the frame the alignment step writes.
- A target element belongs to an anchor when its centre lies inside the footprint
  (boundary included). Gaussian weights use the anchor-frame distance to the anchor centre
  with ``sigma = sqrt(sx * sy) / 2``.
- The footprint shape follows the anchor lattice: an ellipse inscribed in ``spot_size`` on a
  hexagonal lattice (Visium discs), a rectangle along the lattice axes on a square or
  rectangular lattice (rasters, bins), an axis-aligned rectangle otherwise.
"""
import logging
from dataclasses import dataclass

import numpy as np
import scipy.sparse
from scipy.spatial import cKDTree

from focus.preprocessing._utils import infer_lattice

logger = logging.getLogger(__name__)

_MAX_FIT_POINTS = 4096
_LOCAL_NEIGHBOURS = 8
_LATTICE_COS_TOL = 0.02     # |cos| tolerance for square (0) and hexagonal (0.5) bases
_EQUAL_LENGTH_RATIO = 1.05  # |b| / |a| at or below which both basis vectors count as equal
_ANGLE_SNAP = 1e-6          # radians below which a lattice rotation is treated as exactly 0
_EPS32 = float(np.finfo(np.float32).eps)
_BLOCK_PAIRS = 4_000_000    # candidate (anchor, element) pairs evaluated at once


def read_spot_size(value) -> np.ndarray | None:
    """``uns['spot_size']`` as float64 ``[sx, sy]``; None when missing or not a positive finite size."""
    if value is None or isinstance(value, dict):
        return None
    arr = np.asarray(value, dtype=np.float64).ravel()
    if arr.size == 1:
        arr = np.repeat(arr, 2)
    if arr.size != 2 or not np.all(np.isfinite(arr)) or np.any(arr <= 0):
        return None
    return arr


def pixel_centres(cols: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """``(K, 2)`` points of image pixels ``(col, row)``: their centres ``(col + 0.5, row + 0.5)``."""
    return np.column_stack([np.asarray(cols, dtype=np.float64) + 0.5, np.asarray(rows, dtype=np.float64) + 0.5])


# --------------------------------------------------------------------------- transform


def _apply_homography(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Map ``(K, 2)`` points; points that land on or behind the horizon (w <= 0) become NaN."""
    hom = pts @ matrix[:, :2].T + matrix[:, 2]
    w = hom[:, 2:3]
    with np.errstate(divide="ignore", invalid="ignore"):
        out = hom[:, :2] / w
    out[(w[:, 0] <= 0) | ~np.isfinite(w[:, 0])] = np.nan
    return out


def _normalising_similarity(pts: np.ndarray) -> np.ndarray:
    """Hartley normalisation: centroid to the origin, mean distance sqrt(2)."""
    centre = pts.mean(axis=0)
    spread = np.sqrt(((pts - centre) ** 2).sum(axis=1)).mean()
    scale = np.sqrt(2.0) / spread if spread > 0 else 1.0
    return np.array([[scale, 0.0, -scale * centre[0]], [0.0, scale, -scale * centre[1]], [0.0, 0.0, 1.0]])


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
    return matrix / w


def _homography_jacobian(matrix: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """``(K, 2, 2)`` Jacobian of the homography at ``pts``."""
    hom = pts @ matrix[:, :2].T + matrix[:, 2]
    w = hom[:, 2]
    u = hom[:, :2] / w[:, None]
    return (matrix[None, :2, :2] - u[:, :, None] * matrix[None, 2:3, :2]) / w[:, None, None]


@dataclass(frozen=True)
class AnchorToTarget:
    """Map between the anchor frame and the target frame.

    ``kind`` is ``identity``, ``translation`` or ``homography`` (one 3x3 ``matrix`` for every
    spot) or ``local_affine`` (one affine per anchor spot, ``local_a`` / ``local_b``, used when
    the stored coordinates were not produced by a single matrix).
    """
    kind: str
    matrix: np.ndarray | None
    inverse: np.ndarray | None
    local_a: np.ndarray | None
    local_a_inv: np.ndarray | None
    local_b: np.ndarray | None
    max_residual: float

    def to_target(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return np.einsum("kij,kj->ki", self.local_a[rows], pts) + self.local_b[rows]
        return _apply_homography(self.matrix, pts)

    def to_anchor(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return np.einsum("kij,kj->ki", self.local_a_inv[rows], pts - self.local_b[rows])
        return _apply_homography(self.inverse, pts)

    def jacobian(self, pts: np.ndarray, rows: np.ndarray | None = None) -> np.ndarray:
        """``(K, 2, 2)`` derivative of anchor -> target at ``pts`` (target units per anchor unit)."""
        pts = np.asarray(pts, dtype=np.float64)
        if self.kind == "local_affine":
            return self.local_a[rows]
        return _homography_jacobian(self.matrix, pts)


def _matrix_transform(kind: str, matrix: np.ndarray, residual: float) -> AnchorToTarget:
    return AnchorToTarget(kind, matrix, np.linalg.inv(matrix), None, None, None, residual)


def _local_affine(src: np.ndarray, dst: np.ndarray, valid: np.ndarray) -> AnchorToTarget:
    """One affine per anchor, fitted to its nearest valid neighbours and pinned to its own pair."""
    n = len(src)
    s, d = src[valid], dst[valid]
    k = min(_LOCAL_NEIGHBOURS + 1, len(s))
    _, nb = cKDTree(s).query(s, k=k)
    ds = s[nb] - s[:, None, :]                    # (m, k, 2) neighbour offsets, anchor frame
    dd = d[nb] - d[:, None, :]                    # (m, k, 2) the same offsets, target frame
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
    return AnchorToTarget("local_affine", None, None, local_a, local_a_inv, local_b, 0.0)


def fit_anchor_to_target(src: np.ndarray, dst: np.ndarray, spot_size: np.ndarray) -> tuple[AnchorToTarget, np.ndarray]:
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
        raise ValueError("the aligned spots are collinear, so the alignment transform is undetermined")

    storage_tol = 8.0 * _EPS32 * max(float(np.abs(d).max()), 1.0)
    delta = d - s
    if not np.any(delta):
        return _matrix_transform("identity", np.eye(3), 0.0), valid
    shift = np.median(delta, axis=0)
    if np.abs(delta - shift).max() <= storage_tol:
        matrix = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]], [0.0, 0.0, 1.0]])
        return _matrix_transform("translation", matrix, float(np.abs(delta - shift).max())), valid

    rng = np.random.default_rng(0)
    n = len(s)
    fit_idx = np.arange(n)
    if n > _MAX_FIT_POINTS:
        extremes = [s[:, 0].argmin(), s[:, 0].argmax(), s[:, 1].argmin(), s[:, 1].argmax()]
        fit_idx = np.unique(np.concatenate([rng.choice(n, _MAX_FIT_POINTS, replace=False), extremes]))
    matrix = _fit_homography(s[fit_idx], d[fit_idx])

    scale = float(np.sqrt(abs(np.linalg.det(_homography_jacobian(matrix, s.mean(axis=0, keepdims=True))[0]))))
    tol = max(1e-3 * float(np.sqrt(np.prod(spot_size))) * scale, storage_tol)
    residual = float(np.nanmax(np.linalg.norm(_apply_homography(matrix, s) - d, axis=1)))
    if np.isfinite(residual) and residual <= tol:
        return _matrix_transform("homography", matrix, residual), valid

    logger.warning(
        "The aligned coordinates are not one projective transform of the anchor coordinates "
        f"(max residual {residual:.3g} > {tol:.3g}): using a local affine map per spot"
    )
    return _local_affine(src, dst, valid), valid


# --------------------------------------------------------------------------- shape


@dataclass(frozen=True)
class FootprintShape:
    """Footprint of every anchor spot, in anchor units.

    ``kind`` is ``ellipse`` or ``rectangle``; ``half`` the half-extents along the footprint
    axes, which are rotated by ``angle`` (radians) from the anchor x-axis; ``lattice`` the
    detected anchor lattice; ``sigma`` the Gaussian bandwidth.
    """
    kind: str
    lattice: str
    half: np.ndarray
    angle: float
    sigma: float


def _reduce_basis(basis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lagrange-Gauss reduction: shortest basis with |a| <= |b| and |a.b| <= |a|^2 / 2."""
    a, b = np.asarray(basis[0], dtype=np.float64), np.asarray(basis[1], dtype=np.float64)
    for _ in range(64):
        if a @ a > b @ b:
            a, b = b, a
        mu = np.rint((a @ b) / (a @ a))
        if mu == 0:
            break
        b = b - mu * a
    if a @ a > b @ b:
        a, b = b, a
    return a, b


def classify_lattice(basis: np.ndarray | None) -> tuple[str, float]:
    """Lattice kind (``hexagonal``, ``square``, ``rectangular``, ``oblique``, ``irregular``) and its rotation."""
    if basis is None:
        return "irregular", 0.0
    a, b = _reduce_basis(basis)
    la, lb = np.linalg.norm(a), np.linalg.norm(b)
    cos = (a @ b) / (la * lb)
    equal = lb / la <= _EQUAL_LENGTH_RATIO
    if abs(abs(cos) - 0.5) <= _LATTICE_COS_TOL and equal:
        return "hexagonal", 0.0
    if abs(cos) <= _LATTICE_COS_TOL:
        # Undirected axis angles in (-pi/2, pi/2]; the footprint x-axis is the one nearest the
        # anchor x-axis, which lands in (-pi/4, pi/4] for an orthogonal pair.
        angles = [((np.arctan2(v[1], v[0]) + np.pi / 2) % np.pi) - np.pi / 2 for v in (a, b)]
        angle = float(min(angles, key=abs))
        if abs(angle) < _ANGLE_SNAP:
            angle = 0.0
        return ("square" if equal else "rectangular"), angle
    return "oblique", 0.0


def footprint_shape(anchor_xy: np.ndarray, spot_size: np.ndarray) -> FootprintShape:
    """Footprint shape from the anchor lattice (see the module docstring)."""
    pts = np.asarray(anchor_xy, dtype=np.float64)
    pts = pts[np.isfinite(pts).all(axis=1)]
    found = infer_lattice(pts)
    lattice, angle = classify_lattice(None if found is None else found[1])
    kind = "ellipse" if lattice == "hexagonal" else "rectangle"
    if lattice not in ("square", "rectangular"):
        angle = 0.0
    sx, sy = float(spot_size[0]), float(spot_size[1])
    return FootprintShape(kind, lattice, np.array([sx / 2.0, sy / 2.0]), angle, float(np.sqrt(sx * sy) / 2.0))


# --------------------------------------------------------------------------- geometry


@dataclass
class AnchorGeometry:
    """Anchor footprints plus the map into one target frame."""
    centres: np.ndarray          # (N, 2) anchor frame
    valid: np.ndarray            # (N,) spots that can be placed in the target frame
    shape: FootprintShape
    transform: AnchorToTarget

    @classmethod
    def from_arrays(cls, spatial: np.ndarray, mapped: np.ndarray, spot_size: np.ndarray) -> "AnchorGeometry":
        transform, valid = fit_anchor_to_target(spatial, mapped, spot_size)
        centres = np.asarray(spatial, dtype=np.float64)
        shape = footprint_shape(centres[valid], spot_size)
        geom = cls(centres, valid, shape, transform)
        # A spot whose footprint crosses the projective horizon cannot be placed.
        corners = geom._target_corners(np.flatnonzero(valid))
        geom.valid[np.flatnonzero(valid)[~np.isfinite(corners).all(axis=(1, 2))]] = False
        return geom

    def describe(self) -> dict:
        """Provenance of the geometry, for ``uns['focus_registration_geometry']``."""
        return {
            "transform": self.transform.kind,
            "lattice": self.shape.lattice,
            "footprint": self.shape.kind,
            "footprint_angle": float(self.shape.angle),
        }

    def _corner_offsets(self) -> np.ndarray:
        """``(4, 2)`` corners of the footprint's bounding rectangle, relative to its centre."""
        hx, hy = self.shape.half
        local = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])
        c, s = np.cos(self.shape.angle), np.sin(self.shape.angle)
        return local @ np.array([[c, s], [-s, c]])

    def _target_corners(self, rows: np.ndarray) -> np.ndarray:
        """``(K, 4, 2)`` footprint bounding-rectangle corners in the target frame."""
        pts = (self.centres[rows][:, None, :] + self._corner_offsets()[None]).reshape(-1, 2)
        return self.transform.to_target(pts, np.repeat(rows, 4)).reshape(len(rows), 4, 2)

    def target_centres(self) -> np.ndarray:
        rows = np.arange(len(self.centres))
        out = np.full((len(rows), 2), np.nan)
        ok = rows[self.valid]
        out[ok] = self.transform.to_target(self.centres[ok], ok)
        return out

    def target_bboxes(self) -> np.ndarray:
        """``(N, 4)`` ``[xmin, ymin, xmax, ymax]`` of each footprint in the target frame (NaN if invalid).

        The image of the footprint's bounding rectangle is convex in front of the horizon, so
        the box of its four mapped corners contains the whole mapped footprint.
        """
        out = np.full((len(self.centres), 4), np.nan)
        ok = np.flatnonzero(self.valid)
        corners = self._target_corners(ok)
        out[ok, :2] = corners.min(axis=1)
        out[ok, 2:] = corners.max(axis=1)
        return out

    def search_radii(self) -> np.ndarray:
        """Radius around each mapped centre that contains the whole mapped footprint."""
        out = np.full(len(self.centres), np.nan)
        ok = np.flatnonzero(self.valid)
        corners = self._target_corners(ok)
        centre = self.transform.to_target(self.centres[ok], ok)
        reach = np.linalg.norm(corners - centre[:, None, :], axis=2).max(axis=1)
        out[ok] = reach * (1.0 + 1e-6) + self.transform.max_residual + 1e-9
        return out

    def pixel_bboxes(self, height: int, width: int) -> np.ndarray:
        """``(N, 4)`` int64 half-open ``[c0, r0, c1, r1)`` of the pixels whose centres can fall
        inside each footprint, clamped to a ``height x width`` image (empty for invalid spots)."""
        boxes = self.target_bboxes()
        out = np.zeros((len(boxes), 4), dtype=np.int64)
        ok = np.isfinite(boxes).all(axis=1)
        b = boxes[ok]
        out[ok, 0] = np.clip(np.ceil(b[:, 0] - 0.5), 0, width)
        out[ok, 1] = np.clip(np.ceil(b[:, 1] - 0.5), 0, height)
        out[ok, 2] = np.clip(np.floor(b[:, 2] - 0.5) + 1, 0, width)
        out[ok, 3] = np.clip(np.floor(b[:, 3] - 0.5) + 1, 0, height)
        out[:, 2] = np.maximum(out[:, 2], out[:, 0])
        out[:, 3] = np.maximum(out[:, 3], out[:, 1])
        return out

    def contains(self, rows: np.ndarray, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pairwise test: is target-frame point ``pts[k]`` inside footprint ``rows[k]``?

        Returns ``(inside, d2)`` where ``d2`` is the squared anchor-frame distance from the
        anchor centre (NaN where the point cannot be mapped back).
        """
        rows = np.asarray(rows, dtype=np.intp)
        q = self.transform.to_anchor(pts, rows)
        d = q - self.centres[rows]
        c, s = np.cos(self.shape.angle), np.sin(self.shape.angle)
        u = d @ np.array([[c, -s], [s, c]])
        h = self.shape.half
        with np.errstate(invalid="ignore"):
            if self.shape.kind == "ellipse":
                inside = (u[:, 0] / h[0]) ** 2 + (u[:, 1] / h[1]) ** 2 <= 1.0
            else:
                inside = (np.abs(u[:, 0]) <= h[0]) & (np.abs(u[:, 1]) <= h[1])
        inside &= self.valid[rows] & np.isfinite(u).all(axis=1)
        return inside, (d ** 2).sum(axis=1)

    def px_per_anchor_unit(self) -> float:
        """Linear scale of the map at the centroid of the placed spots (target units per anchor unit)."""
        ok = np.flatnonzero(self.valid)
        centre = self.centres[ok].mean(axis=0, keepdims=True)
        row = ok[np.argmin(np.linalg.norm(self.centres[ok] - centre, axis=1))]
        jac = self.transform.jacobian(centre if self.transform.kind != "local_affine" else self.centres[[row]], np.array([row]))
        return float(np.sqrt(abs(np.linalg.det(jac[0]))))


# --------------------------------------------------------------------------- membership


def _weights(geom: AnchorGeometry, d2: np.ndarray, weights: str) -> np.ndarray:
    if weights == "ones":
        return np.ones(len(d2), dtype=np.float32)
    return np.exp(-d2 / (2.0 * geom.shape.sigma ** 2)).astype(np.float32)


def footprint_membership(geom: AnchorGeometry, target_xy: np.ndarray, weights: str = "gaussian") -> scipy.sparse.csr_matrix:
    """
    ``(N_anchor, N_target)`` sparse operator of the target points inside each footprint.

    Candidates are found in the target frame with a KD-tree (radius covering the whole mapped
    footprint); membership and distance are decided in the anchor frame. ``weights`` is
    ``"gaussian"`` (unnormalised Gaussian of the anchor-frame distance) or ``"ones"``.
    Anchors with no member are all-zero rows.
    """
    target_xy = np.asarray(target_xy, dtype=np.float64)
    n_anchor, n_target = len(geom.centres), len(target_xy)
    finite = np.flatnonzero(np.isfinite(target_xy).all(axis=1))
    radii = geom.search_radii()
    usable = np.flatnonzero(geom.valid & np.isfinite(radii))
    if finite.size == 0 or usable.size == 0:
        return scipy.sparse.csr_matrix((n_anchor, n_target), dtype=np.float32)

    tree = cKDTree(target_xy[finite])
    centres = geom.target_centres()
    rows_out, cols_out, data_out = [], [], []
    block = 2048
    for start in range(0, usable.size, block):
        sel = usable[start:start + block]
        hits = [np.asarray(h, dtype=np.intp) for h in tree.query_ball_point(centres[sel], r=radii[sel], workers=-1)]
        counts = np.fromiter((h.size for h in hits), dtype=np.intp, count=len(hits))
        if counts.sum() == 0:
            continue
        rows = np.repeat(sel, counts)
        cols = finite[np.concatenate(hits)]
        inside, d2 = geom.contains(rows, target_xy[cols])
        rows_out.append(rows[inside])
        cols_out.append(cols[inside])
        data_out.append(_weights(geom, d2[inside], weights))

    if not rows_out:
        return scipy.sparse.csr_matrix((n_anchor, n_target), dtype=np.float32)
    return scipy.sparse.csr_matrix(
        (np.concatenate(data_out), (np.concatenate(rows_out), np.concatenate(cols_out))),
        shape=(n_anchor, n_target), dtype=np.float32,
    )


def pixel_footprint_membership(
    geom: AnchorGeometry,
    crop: tuple[int, int, int, int],
    weights: str = "gaussian",
) -> scipy.sparse.csr_matrix:
    """
    ``(N_anchor, crop_h * crop_w)`` operator over the pixels of an image crop.

    ``crop`` is the half-open ``(c0, r0, c1, r1)`` of the loaded pixels in full-image
    coordinates; columns index the crop row-major (``(row - r0) * crop_w + (col - c0)``).
    Pixel points are their centres. Candidates are each footprint's pixel bounding box.
    """
    c0, r0, c1, r1 = crop
    crop_w, crop_h = c1 - c0, r1 - r0
    n_anchor = len(geom.centres)
    boxes = geom.pixel_bboxes(r1, c1)
    boxes[:, 0] = np.maximum(boxes[:, 0], c0)
    boxes[:, 1] = np.maximum(boxes[:, 1], r0)
    boxes[:, 2] = np.maximum(boxes[:, 2], boxes[:, 0])
    boxes[:, 3] = np.maximum(boxes[:, 3], boxes[:, 1])
    widths = boxes[:, 2] - boxes[:, 0]
    counts = widths * (boxes[:, 3] - boxes[:, 1])
    anchors = np.flatnonzero(counts > 0)

    rows_out, cols_out, data_out = [], [], []
    start = 0
    while start < anchors.size:
        # Grow the block until it holds about _BLOCK_PAIRS candidate pixels.
        cum = np.cumsum(counts[anchors[start:]])
        stop = start + max(1, int(np.searchsorted(cum, _BLOCK_PAIRS, side="right")))
        sel = anchors[start:stop]
        start = stop
        n = counts[sel]
        rows = np.repeat(sel, n)
        offset = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        w = np.repeat(widths[sel], n)
        cols = np.repeat(boxes[sel, 0], n) + offset % w
        rws = np.repeat(boxes[sel, 1], n) + offset // w
        inside, d2 = geom.contains(rows, pixel_centres(cols, rws))
        rows_out.append(rows[inside])
        cols_out.append((rws[inside] - r0) * crop_w + (cols[inside] - c0))
        data_out.append(_weights(geom, d2[inside], weights))

    shape = (n_anchor, crop_h * crop_w)
    if not rows_out:
        return scipy.sparse.csr_matrix(shape, dtype=np.float32)
    return scipy.sparse.csr_matrix(
        (np.concatenate(data_out), (np.concatenate(rows_out), np.concatenate(cols_out))), shape=shape, dtype=np.float32,
    )


def row_normalize(matrix: scipy.sparse.csr_matrix) -> scipy.sparse.csr_matrix:
    """Scale every non-empty row to sum 1 (empty rows stay zero)."""
    sums = np.asarray(matrix.sum(axis=1)).ravel()
    inv = np.zeros_like(sums, dtype=np.float64)
    inv[sums > 0] = 1.0 / sums[sums > 0]
    return (scipy.sparse.diags(inv.astype(np.float32)) @ matrix).tocsr()
