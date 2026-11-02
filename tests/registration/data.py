"""Synthetic data and shared setup for registration scenarios."""

from typing import cast

from numpy.typing import ArrayLike

import anndata as ad
import numpy as np
from scipy.spatial import cKDTree


def square_lattice(
    n: int = 20,
    pitch: float = 10.0,
    angle: float = 0.0,
    origin: tuple[float, float] = (5.0, 5.0),
) -> np.ndarray:
    """Return n x n square-lattice coordinates at pitch, rotated by angle radians and translated."""
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    pts = np.column_stack([i.ravel(), j.ravel()]) * pitch
    c, s = np.cos(angle), np.sin(angle)
    return cast(np.ndarray, pts @ np.array([[c, s], [-s, c]]) + origin)


def hex_lattice(n: int = 16, pitch: float = 100.0, angle: float = 0.0) -> np.ndarray:
    """Return staggered hexagonal-lattice coordinates at pitch and angle radians."""
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    pts = np.column_stack(
        [
            i.ravel() * pitch + (j.ravel() % 2) * pitch / 2,
            j.ravel() * pitch * np.sqrt(3) / 2,
        ]
    )
    c, s = np.cos(angle), np.sin(angle)
    return cast(np.ndarray, pts @ np.array([[c, s], [-s, c]]) + 500.0)


def legacy_interpolate(
    anchor: np.ndarray,
    spot_size: tuple[float, float] | list[float] | np.ndarray,
    target: np.ndarray,
    features: np.ndarray,
) -> tuple[np.ndarray, list[set[int]]]:
    """The pre-footprint implementation: centre-in rectangle and Gaussian, in the target frame."""
    sx, sy = spot_size
    sigma = np.sqrt(sx * sy) / 2.0
    tree = cKDTree(target)
    out = np.zeros((len(anchor), features.shape[1]))
    members: list[set[int]] = []
    for i, (cx, cy) in enumerate(anchor):
        cand = np.asarray(
            tree.query_ball_point([cx, cy], r=np.hypot(sx / 2, sy / 2)), dtype=np.intp
        )
        if cand.size == 0:
            members.append(set())
            continue
        d = target[cand] - [cx, cy]
        keep = (np.abs(d[:, 0]) <= sx / 2) & (np.abs(d[:, 1]) <= sy / 2)
        members.append(set(cand[keep].tolist()))
        if keep.any():
            w = np.exp(-(d[keep] ** 2).sum(1) / (2 * sigma**2))
            out[i] = (features[cand[keep]] * (w / w.sum())[:, None]).sum(0)
    return out, members


def write_anchor(
    path: str,
    spatial: np.ndarray,
    mapped: np.ndarray,
    target: str,
    spot_size: ArrayLike,
) -> None:
    """Write float32 native/mapped coordinates and spot sizes to the supplied anchor H5AD path."""
    a = ad.AnnData(X=np.zeros((len(spatial), 1), dtype=np.float32))
    a.obsm["spatial"] = spatial.astype(np.float32)
    a.obsm[f"{target}_spatial"] = mapped.astype(np.float32)
    a.uns["spot_size"] = np.asarray(spot_size, dtype=np.float32)
    a.write_h5ad(path)
