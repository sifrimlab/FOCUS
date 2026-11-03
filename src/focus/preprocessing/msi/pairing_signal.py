"""Content and image primitives of the dual polarity registration (docs/scientific/msi_methods.md §2.2)."""

from __future__ import annotations

from typing import cast

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage as ndi
from scipy.spatial import cKDTree

from focus.preprocessing.msi.settings import (
    _PAIRING_BAND_EDGES,
    _PAIRING_COMPONENTS,
    _PAIRING_PCA_SPECTRA,
    _PAIRING_TILE_OUTLIER,
    _PAIRING_TILE_PITCHES,
    _PAIRING_TILE_RADIUS,
)

RigidFit = tuple[float, NDArray[np.float64], NDArray[np.float64]]


def band_sums(mz: np.ndarray, intensity: np.ndarray) -> NDArray[np.float32]:
    """Intensity summed in each pairing m/z band."""
    band = np.searchsorted(_PAIRING_BAND_EDGES, mz) - 1
    n_bands = len(_PAIRING_BAND_EDGES) - 1
    inside = (band >= 0) & (band < n_bands)
    return np.bincount(
        band[inside], weights=intensity[inside], minlength=n_bands
    ).astype(np.float32)


def components(bands: np.ndarray, seed: int = 0) -> NDArray[np.float64]:
    """Unit-variance principal components of the band composition and log total of each spectrum."""
    bands = bands.astype(np.float64)
    total = bands.sum(axis=1, keepdims=True)
    X = np.column_stack(
        [np.log1p(1e4 * bands / np.maximum(total, 1e-12)), np.log1p(total)]
    )
    X = X[:, X.std(axis=0) > 0]
    X = (X - X.mean(axis=0)) / X.std(axis=0)
    rng = np.random.default_rng(seed)
    fit = rng.choice(len(X), min(len(X), _PAIRING_PCA_SPECTRA), replace=False)
    _, _, vt = np.linalg.svd(X[fit], full_matrices=False)
    C = X @ vt[:_PAIRING_COMPONENTS].T
    return cast(NDArray[np.float64], C / np.maximum(C.std(axis=0), 1e-12))


def lattice_steps(pixels: np.ndarray, physical: np.ndarray) -> NDArray[np.float64]:
    """Physical pixel pitch (x, y) of a raster, from the least-squares pixel-to-physical map."""
    A = np.column_stack([pixels.astype(np.float64), np.ones(len(pixels))])
    T = np.linalg.lstsq(A, physical.astype(np.float64), rcond=None)[0][:2]
    steps = np.zeros(2)
    for row in T:
        axis = int(np.argmax(np.abs(row)))
        steps[axis] = max(steps[axis], float(np.linalg.norm(row)))
    steps[steps == 0] = steps.max()
    return steps


def raster(
    xy: np.ndarray,
    C: np.ndarray,
    cell: np.ndarray,
    origin: np.ndarray,
    shape: tuple[int, int],
    sigma: np.ndarray,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """High-passed component images and the mask of the cells the spectra cover.

    Each spectrum is splatted bilinearly onto its four nearest cell centres, so a rotated lattice
    leaves no moire of empty and doubled cells. The images are the weighted means minus their masked
    Gaussian low-pass (sigma in cells, per axis).
    """
    position = (xy - origin) / cell - 0.5
    corner = np.floor(position).astype(np.int64)
    frac = position - corner
    size = shape[0] * shape[1]
    flat, weights = [], []
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        x, y = corner[:, 0] + dx, corner[:, 1] + dy
        w = (frac[:, 0] if dx else 1 - frac[:, 0]) * (
            frac[:, 1] if dy else 1 - frac[:, 1]
        )
        inside = (x >= 0) & (x < shape[1]) & (y >= 0) & (y < shape[0])
        flat.append(np.where(inside, y * shape[1] + x, 0))
        weights.append(np.where(inside, w, 0.0))
    index, weight = np.concatenate(flat), np.concatenate(weights)
    total = np.bincount(index, weights=weight, minlength=size).reshape(shape)
    mask = total > 0.25
    smooth = ndi.gaussian_filter(mask.astype(np.float64), sigma)
    images = np.zeros((C.shape[1], *shape))
    for i, column in enumerate(C.T):
        values = np.bincount(index, weights=weight * np.tile(column, 4), minlength=size)
        image = np.where(mask, values.reshape(shape) / np.maximum(total, 1e-12), 0.0)
        low = ndi.gaussian_filter(image, sigma) / np.maximum(smooth, 1e-12)
        images[i] = np.where(mask, image - low, 0.0)
    return images, mask.astype(np.float64)


def mean_squared_correlation(
    F: np.ndarray, mf: np.ndarray, G: np.ndarray, mg: np.ndarray
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Masked normalized cross-correlation of every pair of channels, as the mean squared value.

    Returns the map over all integer translations of G over F and the overlap (cells) of each one.
    """
    shape = (mf.shape[0] + mg.shape[0] - 1, mf.shape[1] + mg.shape[1] - 1)

    def fft(a: np.ndarray) -> np.ndarray:
        return np.fft.rfft2(a, s=shape)

    def ifft(a: np.ndarray) -> np.ndarray:
        return np.fft.irfft2(a, s=shape)

    Mf, Mg = fft(mf), fft(mg[::-1, ::-1])
    n = np.rint(ifft(Mf * Mg))
    Ff = [fft(f * mf) for f in F]
    Gg = [fft((g * mg)[::-1, ::-1]) for g in G]
    with np.errstate(invalid="ignore", divide="ignore"):
        sf = [ifft(a * Mg) for a in Ff]
        sg = [ifft(Mf * b) for b in Gg]
        vf = [
            np.clip(ifft(fft(f * f * mf) * Mg) - s**2 / n, 0, None)
            for f, s in zip(F, sf)
        ]
        vg = [
            np.clip(ifft(Mf * fft((g * g * mg)[::-1, ::-1])) - s**2 / n, 0, None)
            for g, s in zip(G, sg)
        ]
        total = np.zeros(shape)
        for i in range(len(F)):
            for j in range(len(G)):
                r = (ifft(Ff[i] * Gg[j]) - sf[i] * sg[j] / n) / np.sqrt(vf[i] * vg[j])
                total += np.clip(np.nan_to_num(r), -1.0, 1.0) ** 2
    return total / (len(F) * len(G)), n


def subcell_peak(z: np.ndarray, k: tuple[int | np.integer, ...]) -> NDArray[np.float64]:
    """Peak position (x, y) in cells, refined by a parabola through each axis' neighbours."""

    def vertex(a: float, b: float, c: float) -> float:
        curvature = a - 2 * b + c
        return 0.0 if curvature >= 0 else 0.5 * (a - c) / curvature

    y, x = k
    dy = vertex(z[y - 1, x], z[y, x], z[y + 1, x]) if 0 < y < z.shape[0] - 1 else 0.0
    dx = vertex(z[y, x - 1], z[y, x], z[y, x + 1]) if 0 < x < z.shape[1] - 1 else 0.0
    return np.array([x + dx, y + dy])


class _TileGrid:
    """Mapped POSITIVE and NEGATIVE images on one native grid, with room for the tile search."""

    def __init__(
        self,
        mapped: np.ndarray,
        neg: np.ndarray,
        Cp: np.ndarray,
        Cn: np.ndarray,
        cell: np.ndarray,
        sigma: float,
    ) -> None:
        """Rasterize both; half a cell keeps lattice positions off the cell edges."""
        margin = cell * (_PAIRING_TILE_RADIUS + 1.5)
        self.cell = cell
        self.origin = np.minimum(mapped.min(axis=0), neg.min(axis=0)) - margin
        top = np.maximum(mapped.max(axis=0), neg.max(axis=0)) + margin
        nx, ny = ((top - self.origin) / cell).astype(int) + 1
        shape, hp = (int(ny), int(nx)), sigma / cell[::-1]
        self.F, self.mf = raster(neg, Cn, cell, self.origin, shape, hp)
        self.G, self.mg = raster(mapped, Cp, cell, self.origin, shape, hp)
        self.both = (self.mf > 0) & (self.mg > 0)

    def shift(
        self, r0: int, c0: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64], float] | None:
        """Centre, shift (micrometres) and peak score of the tile at (r0, c0); None when not usable."""
        side, radius = _PAIRING_TILE_PITCHES, _PAIRING_TILE_RADIUS
        window = (slice(r0, r0 + side), slice(c0, c0 + side))
        mask = self.mg[window] * self.both[window]
        if mask.sum() < 0.5 * side * side:
            return None
        around = (
            slice(r0 - radius, r0 + side + radius),
            slice(c0 - radius, c0 + side + radius),
        )
        G = self.G[(slice(None), *window)] * mask
        r2, n = mean_squared_correlation(
            self.F[(slice(None), *around)], self.mf[around], G, mask
        )
        zero = np.array(mask.shape[::-1]) - 1 + radius
        rows, cols = np.ogrid[: r2.shape[0], : r2.shape[1]]
        z = np.sqrt(r2)
        outside = (np.abs(cols - zero[0]) > radius) | (np.abs(rows - zero[1]) > radius)
        z[outside | (n < 0.5 * mask.sum())] = 0.0
        k = np.unravel_index(int(np.argmax(z)), z.shape)
        if z[k] <= 0:
            return None
        ys, xs = np.nonzero(mask)
        centre = (
            self.origin + (np.array([xs.mean() + c0, ys.mean() + r0]) + 0.5) * self.cell
        )
        return centre, (subcell_peak(z, k) - zero) * self.cell, float(z[k])


def tile_shifts(
    mapped: np.ndarray,
    neg: np.ndarray,
    Cp: np.ndarray,
    Cn: np.ndarray,
    cell: np.ndarray,
    sigma: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """Residual shift of every overlap tile of the mapped POSITIVE content over the NEGATIVE one.

    Returns the tile centres, their shifts (micrometres) and the peak scores.
    """
    grid = _TileGrid(mapped, neg, Cp, Cn, cell, sigma)
    rows, cols = np.nonzero(grid.both)
    side = _PAIRING_TILE_PITCHES
    found = []
    if len(rows):
        found = [
            tile
            for r0 in range(rows.min(), rows.max() + 1, side)
            for c0 in range(cols.min(), cols.max() + 1, side)
            if (tile := grid.shift(r0, c0)) is not None
        ]
    return (
        np.array([t[0] for t in found]).reshape(-1, 2),
        np.array([t[1] for t in found]).reshape(-1, 2),
        np.array([t[2] for t in found]),
    )


def rigid_fit(source: np.ndarray, target: np.ndarray, weight: np.ndarray) -> RigidFit:
    """Weighted least-squares rotation (radians) about the weighted source centroid, and translation."""
    w = weight / weight.sum()
    cs, ct = w @ source, w @ target
    A, B = source - cs, target - ct
    angle = float(
        np.arctan2(w @ (A[:, 0] * B[:, 1] - A[:, 1] * B[:, 0]), w @ (A * B).sum(axis=1))
    )
    return angle, cs, ct - cs


def rotate_rows(points: np.ndarray, angle: float) -> NDArray[np.float64]:
    """Rotate row vectors counter-clockwise by ``angle`` radians."""
    c, s = np.cos(angle), np.sin(angle)
    return cast(NDArray[np.float64], points @ np.array([[c, s], [-s, c]]))


def robust_rigid_fit(
    centres: np.ndarray, shifts: np.ndarray, scores: np.ndarray, pitch: float
) -> RigidFit | None:
    """Rigid fit to the tile shifts, dropping tiles further than the outlier distance and refitting.

    None when fewer than three tiles remain.
    """
    keep = np.ones(len(centres), dtype=bool)
    fit = None
    for _ in range(3):
        if keep.sum() < 3:
            return None
        fit = rigid_fit(centres[keep], centres[keep] + shifts[keep], scores[keep])
        angle, pivot, move = fit
        predicted = rotate_rows(centres - pivot, angle) + pivot + move
        refit = (
            np.linalg.norm(predicted - centres - shifts, axis=1)
            <= _PAIRING_TILE_OUTLIER * pitch
        )
        if (refit == keep).all():
            break
        keep = refit
    return fit


def lattice_offsets(
    mapped: np.ndarray, neg: np.ndarray, steps: np.ndarray, coherence: float
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], int]:
    """Sub-pixel lattice offset of every tile of mapped POSITIVE positions, where it is coherent.

    Lattice offsets are periodic in the pitch, so each tile averages the displacements from its mapped
    POSITIVE positions to their nearest NEGATIVE ones as phases (circular mean per axis). Tiles whose
    phases agree (commensurate lattices) are unwrapped against their nearest already unwrapped
    neighbour, starting from the tile nearest the centroid. Returns the coherent tiles' centres,
    unwrapped offsets (micrometres) and position counts, and the number of tiles examined.
    """
    distance, nearest = cKDTree(neg).query(mapped)
    inside = distance <= float(np.hypot(*steps))
    points = mapped[inside]
    phases = np.exp(2j * np.pi * (neg[nearest[inside]] - points) / steps)
    key = np.floor(
        (points - points.min(axis=0, initial=0.0)) / (_PAIRING_TILE_PITCHES * steps)
    )
    _, tile, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    tile = tile.ravel()
    centres, offsets, weights = [], [], []
    for t in np.flatnonzero(counts >= 50):
        mean = phases[tile == t].mean(axis=0)
        if (np.abs(mean) >= coherence).all():
            centres.append(points[tile == t].mean(axis=0))
            offsets.append(np.angle(mean) / (2 * np.pi) * steps)
            weights.append(float(counts[t]))
    centre_array = np.array(centres).reshape(-1, 2)
    unwrapped = np.array(offsets).reshape(-1, 2)
    if len(centre_array):
        order = np.argsort(
            np.linalg.norm(centre_array - centre_array.mean(axis=0), axis=1)
        )
        for i, current in enumerate(order[1:], start=1):
            done = order[:i]
            reference = done[
                np.argmin(
                    np.linalg.norm(centre_array[done] - centre_array[current], axis=1)
                )
            ]
            unwrapped[current] += steps * np.round(
                (unwrapped[reference] - unwrapped[current]) / steps
            )
    return centre_array, unwrapped, np.array(weights), int((counts >= 50).sum())
