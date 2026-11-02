"""Detect sparse microgrid cells as local outliers against a background-ion model."""

from __future__ import annotations
from typing import cast
from focus.preprocessing.msi.features import peak_histogram
from typing import TYPE_CHECKING
import focus.preprocessing.msi.settings as _settings

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
from focus.runtime.reporter_context import get_reporter
import pandas as pd
from scipy.ndimage import (
    median_filter,
    label as label_components,
)
from scipy.stats import norm as normal_distribution

from focus.config.keys import MsiMetadata


def detect_microgrid_spots(
    sample: MsiSample,
    mz_vectors: list[np.ndarray],
    intensity_vectors: list[np.ndarray],
    ion_mode: str,
    database: pd.DataFrame | None = None,
    mass_tolerance: int | None = None,
) -> list[int]:
    """
    Identify cell spots on a microgrid slide as sparse local outliers against the background.

    A microgrid acquisition covers the whole slide, so background spots are the large majority and
    cells cover one or a few spots each. The detection therefore models the background and flags the
    spots that depart from it, instead of splitting the score distribution into two classes.

    1. Background ions: m/z bins (log-ppm grid, one mass tolerance wide) present in at least
       _MICROGRID_BACKGROUND_OCCUPANCY of the spots, dilated by one bin. Ions found on most of the
       slide come from matrix, sprayed standards and slide coating.
    2. Per-spot features: log intensity of the non-background (cell) ions, cell-ion fraction of the
       TIC and, when a database is given, log intensity of the DB-matched cell ions.
    3. Local background correction: each feature is rasterised on the pixel grid and the median over a
       _MICROGRID_BACKGROUND_WINDOW window is subtracted. Cells cover less than half of the window, so
       the median follows the background and removes deposition gradients and row drift. The residual
       is converted to a robust z-score (median / MAD).
    4. Hysteresis threshold on the mean z-score: seeds need z >= max(_MICROGRID_SEED_Z, the Gaussian
       quantile giving _MICROGRID_SEED_ALPHA expected noise seeds on the slide). Spots with
       z >= _MICROGRID_GROW_Z that are 8-connected to a seed join it, so a cell spanning a few spots is
       kept whole.

    Returns
    -------
    list[int]
            Indices of spots classified as foreground (cell).
    """
    n_spots = len(mz_vectors)
    n_peaks = np.fromiter((len(mz) for mz in mz_vectors), dtype=np.int64, count=n_spots)
    valid = n_peaks > 0
    n_valid = int(valid.sum())
    if n_valid == 0:
        return []

    tolerance_ppm = (
        mass_tolerance
        if mass_tolerance is not None
        else _settings._MICROGRID_DEFAULT_TOLERANCE_PPM
    )
    log_bin_width = tolerance_ppm * 1e-6
    block_spectra = _settings._MICROGRID_BLOCK_SPECTRA

    # ------------------------------------------------------------------
    # 1. Background ion set
    # ------------------------------------------------------------------
    occupied_bins, bin_counts, _ = peak_histogram(mz_vectors, log_bin_width)
    background_bins = occupied_bins[
        bin_counts >= _settings._MICROGRID_BACKGROUND_OCCUPANCY * n_valid
    ].astype(np.int64)
    n_background_ions = background_bins.size
    # Dilate by one bin: a background ion near a bin edge spreads over two adjacent bins.
    background_bins = np.unique(
        np.concatenate([background_bins - 1, background_bins, background_bins + 1])
    )

    db_masses = None
    if database is not None and mass_tolerance is not None:
        db_subset = database[database["ion_mode"] == ion_mode]
        if not db_subset.empty:
            db_masses = np.sort(db_subset["ionized_mass"].to_numpy(dtype=np.float64))

    # ------------------------------------------------------------------
    # 2. Per-spot features
    # ------------------------------------------------------------------
    tic = np.zeros(n_spots, dtype=np.float64)
    cell_intensity = np.zeros(n_spots, dtype=np.float64)
    db_cell_intensity = (
        np.zeros(n_spots, dtype=np.float64) if db_masses is not None else None
    )

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
        bins = np.floor(np.log(mz[positive]) / log_bin_width).astype(np.int64)
        if background_bins.size > 0:
            idx = np.clip(
                np.searchsorted(background_bins, bins), 0, background_bins.size - 1
            )
            is_background[positive] = background_bins[idx] == bins
        is_cell = positive & ~is_background

        cell_intensity[start:stop] = np.bincount(
            spot[is_cell], weights=intensity[is_cell], minlength=block_len
        )

        if db_cell_intensity is not None:
            cell_mz = mz[is_cell]
            idx = np.searchsorted(cast(np.ndarray, db_masses), cell_mz)
            left = cast(np.ndarray, db_masses)[
                np.clip(idx - 1, 0, cast(np.ndarray, db_masses).size - 1)
            ]
            right = cast(np.ndarray, db_masses)[
                np.clip(idx, 0, cast(np.ndarray, db_masses).size - 1)
            ]
            within = (np.abs(cell_mz - left) <= left * mass_tolerance * 1e-6) | (
                np.abs(cell_mz - right) <= right * mass_tolerance * 1e-6
            )
            db_cell_intensity[start:stop] = np.bincount(
                spot[is_cell][within],
                weights=intensity[is_cell][within],
                minlength=block_len,
            )

        del mz, intensity, spot, positive, is_background, is_cell, bins

    cell_fraction = np.divide(
        cell_intensity, tic, out=np.zeros(n_spots, dtype=np.float64), where=tic > 0
    )
    # TIC itself is left out: on a microgrid it is dominated by matrix ions, and ion suppression can
    # make it lower on cells than on the surrounding background.
    features: list[np.ndarray] = [
        np.log1p(np.clip(cell_intensity, 0.0, None)),
        cell_fraction,
    ]
    if db_cell_intensity is not None:
        features.append(np.log1p(np.clip(db_cell_intensity, 0.0, None)))

    # ------------------------------------------------------------------
    # 3. Local background correction and robust z-scores
    # ------------------------------------------------------------------
    window = _settings._MICROGRID_BACKGROUND_WINDOW
    pixel_coords = sample._metadata[ion_mode].get(
        MsiMetadata.PIXEL_COORDINATES
    )  # (N, 2): (y, x)
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
    for feat in features:
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

    if n_used == 0:
        get_reporter().detail(
            f"Tissue detection (microgrid): constant features -- no cell spots detected among {n_spots} spots."
        )
        return []
    score = robust_z(z_sum / n_used)
    if score is None:
        get_reporter().detail(
            f"Tissue detection (microgrid): constant score -- no cell spots detected among {n_spots} spots."
        )
        return []

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
    n_components = component_sizes.size
    n_large = int((component_sizes > _settings._MICROGRID_MAX_COMPONENT_SPOTS).sum())
    size_info = (
        f"sizes={int(component_sizes.min())}-{int(component_sizes.max())}"
        if n_components
        else "sizes=n/a"
    )
    get_reporter().detail(
        f"Tissue detection (microgrid): kept {kept.size} / {n_spots} spots in {n_components} cell(s) "
        f"(seed_z={seed_z:.2f}, grow_z={grow_z:.2f}, {size_info}, background_ions={n_background_ions}, "
        f"local_background={'on' if spatial else 'off'}, n_features={len(features)}, ion_mode={ion_mode})"
    )
    if n_large:
        get_reporter().warning(
            f"Tissue detection (microgrid): {n_large} component(s) exceed "
            f"{_settings._MICROGRID_MAX_COMPONENT_SPOTS} spots; check for debris or matrix crystals."
        )
    if kept.size == 0:
        get_reporter().detail(
            "Tissue detection (microgrid): no cell spots detected; every spot will be treated as foreground."
        )
    return cast(list[int], kept.tolist())
