"""Flat-field (illumination) estimation for one acquisition, checked against the tile overlaps.

Every tile of an acquisition is imaged through the same scan optics, so the illumination profile is
shared. FOCUS estimates it retrospectively as the per-pixel mean of the tissue tiles, smoothed at the
illumination scale, normalised to mean 1 (the averaging family of retrospective shading correction,
Tomazevic et al. 2002). The specimen differs from tile to tile and averages out; what remains at
large scale is the illumination.

The estimate is checked against an independent measurement: in each overlap, the same tissue is seen
near one edge of a tile and near the opposite edge of its neighbour, so the median ratio of the two
strips measures the edge-to-edge illumination ratio. ``overlap_check`` returns the measured and the
modelled ratios.

FOCUS design choice: BaSiC (Peng et al. 2017), used before raman-2, was calibrated against these
overlap ratios on the P762 acquisitions and missed the left-right shading of single-channel scans by
11 to 17 % while printing tile-grid texture into the flat-field; the smoothed mean matched within 3 %.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter

# Smoothing of the per-pixel mean, µm: the shading of the scan field varies over hundreds of µm,
# specimen structure that survives averaging over tens of tiles is smaller.
SMOOTH_UM = 36.0
# Tiles below this percentile of tile mean intensity (mostly background) are left out of the mean.
TISSUE_TILE_PERCENTILE = 25.0
# Fewer tiles than this cannot separate illumination from specimen; such acquisitions borrow the
# flat-field of the acquisition with the nearest wavenumbers in the same file.
MIN_TILES = 10
# Floor of the normalised profile, so near-dark corners are not amplified without limit.
_FLOOR = 0.05
# Strip margins (px) away from the overlap borders and the tile ends, for the overlap check.
_STRIP_MARGIN = 8
_STRIP_END = 100


@dataclass(frozen=True)
class FlatField:
    """A flat-field at tile resolution (mean 1) and how it was obtained."""

    profile: np.ndarray
    source: str

    @property
    def span(self) -> tuple[float, float]:
        """Minimum and maximum relative illumination."""
        return float(self.profile.min()), float(self.profile.max())


def channel_mean(tile: np.ndarray, saturation: int) -> np.ndarray:
    """``(Y, X)`` mean over channels of a ``(C, Y, X)`` tile, ignoring saturated samples.

    Pixels saturated in every channel keep the saturation value.
    """
    data = tile.astype(np.float32)
    valid = data < saturation
    count = valid.sum(axis=0)
    total = np.where(valid, data, 0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(count > 0, total / count, np.float32(saturation))
    return mean.astype(np.float32)


def estimate(tile_means: np.ndarray, pixel_um: float) -> np.ndarray:
    """Flat-field (mean 1) of a ``(N, Y, X)`` stack of channel-mean tiles."""
    level = tile_means.mean(axis=(1, 2))
    tissue = level >= np.percentile(level, TISSUE_TILE_PERCENTILE)
    mean = tile_means[tissue].mean(axis=0, dtype=np.float64)
    smooth = gaussian_filter(mean, SMOOTH_UM / pixel_um, mode="nearest")
    if float(smooth.mean()) <= 0:
        return np.ones(mean.shape, dtype=np.float32)
    profile = np.clip(smooth / smooth.mean(), _FLOOR, None)
    out: np.ndarray = (profile / profile.mean()).astype(np.float32)
    return out


@dataclass(frozen=True)
class OverlapCheck:
    """Edge-to-edge illumination ratios: measured in the overlaps and predicted by the model."""

    measured: tuple[float, float]
    modelled: tuple[float, float]
    pairs: tuple[int, int]

    def describe(self) -> str:
        """One detail line for the activity log."""
        parts = []
        for label, m, f, n in zip(
            ("right/left", "bottom/top"), self.measured, self.modelled, self.pairs
        ):
            if n:
                parts.append(
                    f"{label} {m:.2f} measured, {f:.2f} modelled ({n} overlaps)"
                )
        return "; ".join(parts) if parts else "no tissue overlaps to check"


def overlap_check(
    tile_means: np.ndarray,
    positions: np.ndarray,
    pairs: Sequence[tuple[int, int]],
    profile: np.ndarray,
) -> OverlapCheck:
    """Compare the flat-field with the illumination ratios measured in side-by-side overlaps.

    ``positions`` are registered ``(x, y)`` tile origins in pixels; ``pairs`` are overlapping tiles.
    """
    h, w = tile_means.shape[1:]
    # Strips darker than half the dim-tile level are background: their ratio is noise. Half, so a
    # strip at the dark edge of a strongly shaded tile is not mistaken for background.
    floor = 0.5 * float(
        np.percentile(tile_means.mean(axis=(1, 2)), TISSUE_TILE_PERCENTILE)
    )
    found: dict[str, list[tuple[float, float]]] = {"x": [], "y": []}
    m, e = _STRIP_MARGIN, _STRIP_END
    for i, j in pairs:
        dx, dy = positions[j] - positions[i]
        if abs(dx) > abs(dy):
            if dx < 0:
                i, j, dx, dy = j, i, -dx, -dy
            ov = int(round(w - dx))
            if ov <= 2 * m or abs(dy) >= h - 2 * e:
                continue
            a = (slice(e, h - e), slice(w - ov + m, w - m))
            b = (slice(e, h - e), slice(m, ov - m))
            axis = "x"
        else:
            if dy < 0:
                i, j, dx, dy = j, i, -dx, -dy
            ov = int(round(h - dy))
            if ov <= 2 * m or abs(dx) >= w - 2 * e:
                continue
            a = (slice(h - ov + m, h - m), slice(e, w - e))
            b = (slice(m, ov - m), slice(e, w - e))
            axis = "y"
        ta, tb = float(tile_means[i][a].mean()), float(tile_means[j][b].mean())
        if ta > floor and tb > floor:
            found[axis].append((ta / tb, float(profile[a].mean() / profile[b].mean())))
    measured = tuple(
        float(np.median([r for r, _ in found[k]])) if found[k] else float("nan")
        for k in ("x", "y")
    )
    modelled = tuple(
        float(np.median([f for _, f in found[k]])) if found[k] else float("nan")
        for k in ("x", "y")
    )
    return OverlapCheck(
        measured=(measured[0], measured[1]),
        modelled=(modelled[0], modelled[1]),
        pairs=(len(found["x"]), len(found["y"])),
    )
