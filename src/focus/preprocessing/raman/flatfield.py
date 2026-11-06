"""Flat-field (illumination) estimation for one acquisition, checked against the tile overlaps.

Every tile of an acquisition is imaged through the same scan optics, so the illumination profile is
shared. FOCUS estimates it retrospectively as the per-pixel mean of the tissue tiles, smoothed at the
illumination scale, normalised to mean 1 (the averaging family of retrospective shading correction,
Tomazevic et al. 2002). The specimen differs from tile to tile and averages out; what remains at
large scale is the illumination.

The illumination multiplies the optical signal, not the detector's own offset. A multi-channel SRS
acquisition carries a constant offset (6.9 counts on the P762 CH scan) that the illumination does not
shade, and a channel close to that offset looks less shaded than a bright one. FOCUS estimates the
offset from the channels themselves (``detector_offset``): with one shared illumination profile, each
channel's tile-averaged map is its offset plus its signal times the profile, so the amplitude of the
shading in each channel is proportional to the channel's mean above the offset. On P762 this one
parameter explains all 20 channels of the CH scan (R² 0.999 across channels, residual 0.13 counts
against 0.30 counts of split-half noise), so no channel-specific profile is fitted. The flat-field is
estimated on the channel mean above the offset and applied as ``(raw - offset) / profile`` to every
channel.

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

from focus.spatial.spectral import nan_block_mean

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
# Detector offset: side (µm) of the blocks the per-channel tile maps are averaged over, the fewest
# channels that can separate an offset from the signal, the R² across channels the common-shading
# model must reach, and how far above its split-half noise the shading must rise to be measured.
OFFSET_BLOCK_UM = 9.0
OFFSET_MIN_CHANNELS = 3
OFFSET_MIN_R2 = 0.95
OFFSET_MIN_SNR = 5.0


@dataclass(frozen=True)
class DetectorOffset:
    """The unshaded detector offset of an acquisition (0 when it cannot be measured).

    ``r2``: linearity of the shading amplitude in the channel means; ``residual`` and ``noise``: RMS
    of the per-channel tile maps left by the common-shading model and their split-half noise, in
    counts (NaN when not estimated).
    """

    counts: float
    r2: float
    residual: float
    noise: float
    note: str

    def describe(self) -> str:
        """One detail line for the activity log."""
        if self.counts > 0:
            return (
                f"detector offset {self.counts:.2f} counts (one illumination profile for every "
                f"channel: R² {self.r2:.3f}, residual {self.residual:.2f} counts against "
                f"{self.noise:.2f} of noise)"
            )
        return f"no detector offset subtracted ({self.note})"


def no_offset(note: str) -> DetectorOffset:
    """An offset of 0 that was not measured, and why."""
    nan = float("nan")
    return DetectorOffset(0.0, nan, nan, nan, note)


@dataclass(frozen=True)
class FlatField:
    """A flat-field at tile resolution (mean 1), the unshaded offset it applies above, its source."""

    profile: np.ndarray
    source: str
    offset: float = 0.0
    offset_fit: DetectorOffset | None = None

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


def channel_blocks(tile: np.ndarray, saturation: int, factor: int) -> np.ndarray:
    """``(C, Y/f, X/f)`` block means of every channel of a tile, ignoring saturated samples."""
    data = tile.astype(np.float32)
    data[data >= saturation] = np.nan
    return np.stack([nan_block_mean(plane, factor) for plane in data])


def _tile_maps(blocks: np.ndarray, tiles: np.ndarray, sigma: float) -> np.ndarray:
    """``(C, y, x)`` mean over ``tiles`` of each channel's block map, smoothed by ``sigma`` blocks."""
    with np.errstate(invalid="ignore"):
        mean = np.nanmean(blocks[tiles], axis=0)
    out = np.empty_like(mean, dtype=np.float64)
    for c, plane in enumerate(mean):
        level = np.nanmean(plane) if np.isfinite(plane).any() else 0.0
        out[c] = gaussian_filter(
            np.where(np.isfinite(plane), plane, level), sigma, mode="nearest"
        )
    return out


def detector_offset(blocks: np.ndarray, block_um: float) -> DetectorOffset:
    """Unshaded detector offset from ``(T, C, y, x)`` per-channel tile block means.

    The tissue-tile average of each channel, smoothed at the illumination scale, is centred; the
    leading spatial component's loading in each channel is linear in the channel mean, ``k (mean -
    offset)``, when one illumination profile shades the signal above one offset. The offset is
    accepted when the line fits (``OFFSET_MIN_R2``), the shading rises above its split-half noise
    (``OFFSET_MIN_SNR``) and the offset lies between 0 and the dimmest channel mean.
    """
    tiles, channels = blocks.shape[:2]
    if channels < OFFSET_MIN_CHANNELS:
        return no_offset(f"fewer than {OFFSET_MIN_CHANNELS} channels")
    if tiles < 2 * MIN_TILES:
        return no_offset(f"fewer than {2 * MIN_TILES} tiles")
    with np.errstate(invalid="ignore"):
        level = np.nanmean(blocks, axis=(1, 2, 3))
    level = np.where(np.isfinite(level), level, -np.inf)
    tissue = np.flatnonzero(level >= np.percentile(level, TISSUE_TILE_PERCENTILE))
    sigma = SMOOTH_UM / block_um
    maps = _tile_maps(blocks, tissue, sigma)
    alpha = maps.mean(axis=(1, 2))
    centred = (maps - alpha[:, None, None]).reshape(channels, -1)
    u, singular, _ = np.linalg.svd(centred, full_matrices=False)
    beta = u[:, 0] * singular[0]
    if float(np.dot(beta - beta.mean(), alpha - alpha.mean())) < 0:
        beta = -beta
    design = np.column_stack([alpha, np.ones(channels)])
    (slope, intercept), *_ = np.linalg.lstsq(design, beta, rcond=None)
    fitted = design @ np.array([slope, intercept])
    total = float(((beta - beta.mean()) ** 2).sum())
    r2 = 1.0 - float(((beta - fitted) ** 2).sum()) / total if total > 0 else 0.0
    half = (
        _tile_maps(blocks, tissue[::2], sigma) - _tile_maps(blocks, tissue[1::2], sigma)
    ) / 2
    noise = float(np.sqrt(np.mean(half**2)))
    if slope <= 0:
        return no_offset("the shading does not grow with the channel level")
    offset = float(-intercept / slope)
    above = alpha - offset
    shape = centred.T @ above / max(float(above @ above), 1e-12)
    residual = float(np.sqrt(np.mean((centred - np.outer(above, shape)) ** 2)))
    signal = float(np.sqrt(np.mean(np.outer(above, shape) ** 2)))
    if r2 < OFFSET_MIN_R2:
        return DetectorOffset(0.0, r2, residual, noise, f"R² {r2:.2f} across channels")
    if signal < OFFSET_MIN_SNR * noise:
        return DetectorOffset(0.0, r2, residual, noise, "shading within the noise")
    if not 0.0 <= offset < float(alpha.min()):
        return DetectorOffset(
            0.0, r2, residual, noise, f"implausible offset {offset:.2f} counts"
        )
    return DetectorOffset(offset, r2, residual, noise, "")


def estimate(tile_means: np.ndarray, pixel_um: float) -> np.ndarray:
    """Flat-field (mean 1) of a ``(N, Y, X)`` stack of channel-mean tiles (offset removed)."""
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
