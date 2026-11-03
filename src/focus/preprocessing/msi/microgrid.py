"""Detect sparse microgrid cells as local outliers against a background-ion model."""

from __future__ import annotations
from typing import cast
import focus.preprocessing.msi.settings as _settings
from focus.preprocessing.msi.tissue import ForegroundResult, SpectraContext

import numpy as np
from focus.runtime.reporter_context import get_reporter
from scipy.ndimage import (
    median_filter,
    label as label_components,
)
from scipy.stats import norm as normal_distribution


def detect_microgrid_spots(
    mz_vectors: list[np.ndarray],
    intensity_vectors: list[np.ndarray],
    pixel_coordinates: np.ndarray | None,
    context: SpectraContext,
) -> ForegroundResult:
    """
    Identify cell spots on a microgrid slide as sparse local outliers against the background.

    A microgrid acquisition covers the whole slide, so background spots are the large majority and
    cells cover one or a few spots each. The detection therefore models the background and flags the
    spots that depart from it, instead of splitting the score distribution into two classes. It runs on
    recalibrated spectra, so a drifting background ion stays one ion.

    1. Background ions: the recurrent ions of the sample (``context.features``, found over every
       spectrum) present in at least _MICROGRID_BACKGROUND_OCCUPANCY of the spectra, over their whole
       feature range. Ions found on most of the slide come from matrix, sprayed standards and slide
       coating.
    2. Per-spot features: log intensity of the non-background (cell) peaks and their fraction of the
       TIC.
    3. Local background correction: each feature is rasterised on the pixel grid and the median over a
       _MICROGRID_BACKGROUND_WINDOW window is subtracted. Cells cover less than half of the window, so
       the median follows the background and removes deposition gradients and row drift. The residual
       is converted to a robust z-score (median / MAD).
    4. Hysteresis threshold on the mean z-score: seeds need z >= max(_MICROGRID_SEED_Z, the Gaussian
       quantile giving _MICROGRID_SEED_ALPHA expected noise seeds on the slide). Spots with
       z >= _MICROGRID_GROW_Z that are 8-connected to a seed join it, so a cell spanning a few spots is
       kept whole.
    """
    n_spots = len(mz_vectors)
    n_peaks = np.fromiter((len(mz) for mz in mz_vectors), dtype=np.int64, count=n_spots)
    valid = n_peaks > 0
    n_valid = int(valid.sum())
    zero_score = np.zeros(n_spots, dtype=np.float32)
    if n_valid == 0:
        return ForegroundResult(
            np.zeros(n_spots, dtype=bool), zero_score, False, "no peaks"
        )

    log_bin_width = context.log_bin_width
    block_spectra = _settings._MICROGRID_BLOCK_SPECTRA

    # ------------------------------------------------------------------
    # 1. Background ion set
    # ------------------------------------------------------------------
    features = context.features
    background = features.presence >= _settings._MICROGRID_BACKGROUND_OCCUPANCY
    n_background_ions = int(background.sum())
    lookup_lo = int(features.left[background].min()) if n_background_ions else 0
    lookup = np.zeros(
        int(features.right[background].max()) - lookup_lo + 1
        if n_background_ions
        else 0,
        dtype=bool,
    )
    for left, right in zip(features.left[background], features.right[background]):
        lookup[left - lookup_lo : right - lookup_lo + 1] = True

    # ------------------------------------------------------------------
    # 2. Per-spot features
    # ------------------------------------------------------------------
    tic = np.zeros(n_spots, dtype=np.float64)
    cell_intensity = np.zeros(n_spots, dtype=np.float64)

    for start in range(0, n_spots, block_spectra):
        stop = min(start + block_spectra, n_spots)
        block_len = stop - start
        if n_peaks[start:stop].sum() == 0:
            continue
        mz = np.concatenate(mz_vectors[start:stop]).astype(np.float64, copy=False)
        intensity = np.concatenate(intensity_vectors[start:stop]).astype(
            np.float64, copy=False
        )
        spot = np.repeat(np.arange(block_len), n_peaks[start:stop])

        tic[start:stop] = np.bincount(spot, weights=intensity, minlength=block_len)

        positive = mz > 0.0
        is_background = np.zeros(mz.size, dtype=bool)
        if lookup.size:
            bins = np.floor(np.log(mz[positive]) / log_bin_width).astype(np.int64)
            index = bins - lookup_lo
            inside = (index >= 0) & (index < lookup.size)
            hit = np.zeros(bins.size, dtype=bool)
            hit[inside] = lookup[index[inside]]
            is_background[positive] = hit
        is_cell = positive & ~is_background

        cell_intensity[start:stop] = np.bincount(
            spot[is_cell], weights=intensity[is_cell], minlength=block_len
        )
        del mz, intensity, spot, positive, is_background, is_cell

    cell_fraction = np.divide(
        cell_intensity, tic, out=np.zeros(n_spots, dtype=np.float64), where=tic > 0
    )
    # TIC itself is left out: on a microgrid it is dominated by matrix ions, and ion suppression can
    # make it lower on cells than on the surrounding background.
    spot_features: list[np.ndarray] = [
        np.log1p(np.clip(cell_intensity, 0.0, None)),
        cell_fraction,
    ]

    # ------------------------------------------------------------------
    # 3. Local background correction and robust z-scores
    # ------------------------------------------------------------------
    window = _settings._MICROGRID_BACKGROUND_WINDOW
    pixel_coords = pixel_coordinates  # (N, 2): (y, x)
    grid_yx = None
    if pixel_coords is not None and len(pixel_coords) == n_spots:
        grid_yx = np.asarray(pixel_coords, dtype=np.int64)
        grid_yx = grid_yx - grid_yx.min(axis=0)
        grid_shape = tuple(int(v) for v in grid_yx.max(axis=0) + 1)
    spatial = grid_yx is not None and min(grid_shape) >= window

    def robust_z(values: np.ndarray) -> np.ndarray | None:
        """Return robust standardized intensities, or None for a constant feature."""
        center = np.median(values)
        scale = 1.4826 * np.median(np.abs(values - center))
        if scale <= 1e-12:
            scale = values.std()
        if scale <= 1e-12:
            return None
        return cast(np.ndarray | None, (values - center) / scale)

    valid_yx = grid_yx[valid] if grid_yx is not None else None
    z_sum = np.zeros(n_valid, dtype=np.float64)
    n_used = 0
    for feat in spot_features:
        values = feat[valid]
        if spatial:
            raster = np.full(grid_shape, np.median(values), dtype=np.float64)
            raster[
                cast(np.ndarray, valid_yx)[:, 0], cast(np.ndarray, valid_yx)[:, 1]
            ] = values
            local_background = median_filter(raster, size=window, mode="reflect")
            values = (
                values
                - local_background[
                    cast(np.ndarray, valid_yx)[:, 0], cast(np.ndarray, valid_yx)[:, 1]
                ]
            )
        z = robust_z(values)
        if z is not None:
            z_sum += z
            n_used += 1

    none = np.zeros(n_spots, dtype=bool)
    if n_used == 0:
        return ForegroundResult(none, zero_score, False, "constant spot features")
    score = robust_z(z_sum / n_used)
    if score is None:
        return ForegroundResult(none, zero_score, False, "constant score")

    # ------------------------------------------------------------------
    # 4. Hysteresis threshold
    # ------------------------------------------------------------------
    seed_z = max(
        _settings._MICROGRID_SEED_Z,
        float(normal_distribution.isf(_settings._MICROGRID_SEED_ALPHA / n_valid)),
    )
    grow_z = min(_settings._MICROGRID_GROW_Z, seed_z)
    valid_idx = np.flatnonzero(valid)
    seeds = score >= seed_z

    if grid_yx is not None:
        grow_raster = np.zeros(grid_shape, dtype=bool)
        grow_raster[
            cast(np.ndarray, valid_yx)[:, 0], cast(np.ndarray, valid_yx)[:, 1]
        ] = score >= grow_z
        components, _ = label_components(
            grow_raster, structure=np.ones((3, 3), dtype=bool)
        )
        spot_components = components[
            cast(np.ndarray, valid_yx)[:, 0], cast(np.ndarray, valid_yx)[:, 1]
        ]
        seed_components = np.unique(spot_components[seeds])
        seed_components = seed_components[seed_components > 0]
        cell = np.isin(spot_components, seed_components)
        component_sizes = (
            np.bincount(spot_components[cell])[seed_components]
            if seed_components.size
            else np.array([], dtype=np.int64)
        )
    else:
        cell = seeds
        component_sizes = np.ones(int(seeds.sum()), dtype=np.int64)

    kept = valid_idx[cell]
    mask = np.zeros(n_spots, dtype=bool)
    mask[kept] = True
    spot_score = np.zeros(n_spots, dtype=np.float32)
    spot_score[valid] = score
    n_components = component_sizes.size
    n_large = int((component_sizes > _settings._MICROGRID_MAX_COMPONENT_SPOTS).sum())
    size_info = (
        f", {int(component_sizes.min())} to {int(component_sizes.max())} spots each"
        if n_components
        else ""
    )
    if n_large:
        get_reporter().warning(
            f"Microgrid: {n_large} cell(s) exceed {_settings._MICROGRID_MAX_COMPONENT_SPOTS} "
            "spots; check for debris or matrix crystals"
        )
    return ForegroundResult(
        mask,
        spot_score,
        bool(kept.size),
        f"{n_components} cell(s){size_info} against {n_background_ions} background ions "
        f"(seed z {seed_z:.2f}, grow z {grow_z:.2f}, local background "
        f"{'on' if spatial else 'off'})",
    )
