"""Validate spot dimensions and infer rectangular or hexagonal footprint shape."""

from numpy.typing import NDArray

from numpy.typing import ArrayLike

from dataclasses import dataclass

import numpy as np

from focus.spatial.lattice import infer_lattice

_LATTICE_COS_TOL = 0.02  # |cos| tolerance for square (0) and hexagonal (0.5) bases

_EQUAL_LENGTH_RATIO = (
    1.05  # |b| / |a| at or below which both basis vectors count as equal
)

_ANGLE_SNAP = 1e-6  # radians below which a lattice rotation is treated as exactly 0

_EPS32 = float(np.finfo(np.float32).eps)


def read_spot_size(value: ArrayLike | None) -> np.ndarray | None:
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
    return np.column_stack(
        [
            np.asarray(cols, dtype=np.float64) + 0.5,
            np.asarray(rows, dtype=np.float64) + 0.5,
        ]
    )


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
    half: NDArray[np.float64]
    angle: float
    sigma: float


def _reduce_basis(basis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lagrange-Gauss reduction: shortest basis with |a| <= |b| and |a.b| <= |a|^2 / 2."""
    a, b = (
        np.asarray(basis[0], dtype=np.float64),
        np.asarray(basis[1], dtype=np.float64),
    )
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
        angles = [
            ((np.arctan2(v[1], v[0]) + np.pi / 2) % np.pi) - np.pi / 2 for v in (a, b)
        ]
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
    return FootprintShape(
        kind,
        lattice,
        np.array([sx / 2.0, sy / 2.0]),
        angle,
        float(np.sqrt(sx * sy) / 2.0),
    )
