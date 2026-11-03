"""Detect MSI foreground spectra from their recurrent ions, with a preset-specific spatial cleanup."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import (
    binary_fill_holes,
    binary_opening,
    label as label_components,
    median_filter,
    uniform_filter,
)
from sklearn.mixture import GaussianMixture

from focus.preprocessing.msi.feature_stats import (
    detections_per_spectrum,
    feature_lookup,
)
from focus.preprocessing.msi.features import (
    MzFeatures,
    feature_bin_width,
    find_features,
    peak_histogram,
    recurrence_map,
)
from focus.preprocessing.msi.matching import _flatten_spectra, recalibration_search_ppm
from focus.preprocessing.msi.technology import (
    COMPONENT_MIN_FRACTION,
    MAX_BACKGROUND_RATIO,
    MIN_SEPARATION,
    SIGNAL_TO_NOISE,
    SampleProfile,
)


@dataclass
class SpectraContext:
    """The recurrent ions of one sample and ion mode, found over all its spectra."""

    features: MzFeatures
    recurrence: tuple[int, np.ndarray, float]  # recurrence_map of the features
    log_bin_width: float


@dataclass
class ForegroundResult:
    """Foreground of one sample and ion mode."""

    mask: np.ndarray  # (n,) bool
    score: np.ndarray  # (n,) float32
    background: bool  # the detector separated a background from the foreground
    summary: str


def spectra_context(mz_list: list[np.ndarray], mass_tolerance: float) -> SpectraContext:
    """Find the recurrent ions over every spectrum (:func:`find_features`) and their recurrence map."""
    log_bin_width = feature_bin_width(mass_tolerance)
    features = find_features(
        *peak_histogram(mz_list, log_bin_width), len(mz_list), mass_tolerance
    )
    recurrence = recurrence_map(
        features, log_bin_width, recalibration_search_ppm(mass_tolerance)
    )
    return SpectraContext(features, recurrence, log_bin_width)


def recurrent_detections(
    mz_list: list[np.ndarray], intensity_list: list[np.ndarray], context: SpectraContext
) -> np.ndarray:
    """Per spectrum, the number of recurrent ions it holds above its own noise gate (S/N 3)."""
    lookup_lo, lookup = feature_lookup(context.features.left, context.features.right)
    flat_mz, flat_intensity, offsets = _flatten_spectra(mz_list, intensity_list)
    recurrent_lo, recurrent, _ = context.recurrence
    return detections_per_spectrum(
        flat_mz,
        flat_intensity,
        offsets,
        context.log_bin_width,
        lookup_lo,
        lookup,
        context.features.mz.size,
        recurrent_lo,
        recurrent,
        SIGNAL_TO_NOISE,
    )


def _raster(pixel_coordinates: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """(y, x) raster indices from the origin of the pixel grid, and the raster shape."""
    grid = np.asarray(pixel_coordinates, dtype=np.int64)
    grid = grid - grid.min(axis=0)
    return grid, (int(grid[:, 0].max()) + 1, int(grid[:, 1].max()) + 1)


def _cleanup(
    mask: np.ndarray, grid: np.ndarray, shape: tuple[int, int], profile: SampleProfile
) -> np.ndarray:
    """Apply the preset's spatial cleanup to a spectrum mask on the raster."""
    if profile.cleanup == "none":
        return mask
    raster = np.zeros(shape, dtype=bool)
    raster[grid[:, 0], grid[:, 1]] = mask
    structure = np.ones((profile.cleanup_size, profile.cleanup_size), dtype=bool)
    if profile.cleanup == "majority":
        # A pixel takes the class of the majority of its neighbourhood, itself included.
        raster = uniform_filter(raster.astype(np.float64), profile.cleanup_size) > 0.5
    else:
        raster = binary_opening(binary_fill_holes(raster), structure=structure)
        if profile.cleanup == "fill_open_components":
            components, n = label_components(raster, structure=np.ones((3, 3)))
            if n > 1:
                sizes = np.bincount(components.ravel())[1:]
                keep = np.flatnonzero(sizes >= COMPONENT_MIN_FRACTION * sizes.max()) + 1
                raster = np.isin(components, keep)
    return np.asarray(raster[grid[:, 0], grid[:, 1]], dtype=bool)


def detect_foreground(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    pixel_coordinates: np.ndarray,
    context: SpectraContext,
    profile: SampleProfile,
) -> ForegroundResult:
    """
    Foreground spectra of a tissue section or a cell culture.

    Score: log(1 + the number of recurrent ions of the sample a spectrum holds above its noise gate,
    :func:`recurrent_detections`). Noise centroids never recur, so a background of noise scores low;
    a background of matrix ions holds only those, below the tissue that holds them and its own ions.
    With ``profile.score_median`` the score is median-filtered on the raster first. A 1- and a
    2-component Gaussian mixture are fitted to the scores of the spectra with peaks; when BIC
    prefers one component, when the two overlap (Ashman's D below `MIN_SEPARATION`) or when the lower
    one holds more than `MAX_BACKGROUND_RATIO` of the recurrent ions of the higher one (two tissue
    regions), every such spectrum is foreground; otherwise the spectra whose posterior of the
    higher-mean component is at least 0.5. The preset's spatial cleanup follows.
    """
    n_spectra = len(mz_list)
    n_peaks = np.fromiter((mz.size for mz in mz_list), dtype=np.int64, count=n_spectra)
    valid = n_peaks > 0
    score = np.log1p(recurrent_detections(mz_list, intensity_list, context)).astype(
        np.float64
    )
    grid, shape = _raster(pixel_coordinates)
    if profile.score_median > 1:
        raster = np.zeros(shape, dtype=np.float64)
        raster[grid[:, 0], grid[:, 1]] = score
        score = median_filter(raster, size=profile.score_median, mode="nearest")[
            grid[:, 0], grid[:, 1]
        ]
    values = score[valid]
    if values.size < 4 or np.ptp(values) < 1e-12:
        return ForegroundResult(
            valid,
            score.astype(np.float32),
            False,
            "degenerate score, every spectrum kept",
        )
    column = values.reshape(-1, 1)
    one = GaussianMixture(n_components=1, random_state=0).fit(column)
    two = GaussianMixture(n_components=2, random_state=0, n_init=3).fit(column)
    bic_one, bic_two = one.bic(column), two.bic(column)
    if bic_one <= bic_two:
        return ForegroundResult(
            valid,
            score.astype(np.float32),
            False,
            f"unimodal score (BIC {bic_one:.0f} <= {bic_two:.0f}), every spectrum kept",
        )
    means, variances = two.means_.ravel(), two.covariances_.ravel()
    separation = float(np.sqrt(2) * abs(means[0] - means[1]) / np.sqrt(variances.sum()))
    ions = np.sort(np.expm1(means))
    if separation < MIN_SEPARATION:
        return ForegroundResult(
            valid,
            score.astype(np.float32),
            False,
            f"overlapping score modes (Ashman's D {separation:.2f} < {MIN_SEPARATION:g}), "
            "every spectrum kept",
        )
    if ions[0] > MAX_BACKGROUND_RATIO * ions[1]:
        return ForegroundResult(
            valid,
            score.astype(np.float32),
            False,
            f"two tissue-like score modes (about {ions[0]:.0f} and {ions[1]:.0f} recurrent ions "
            "per spectrum), every spectrum kept",
        )
    high = int(np.argmax(means))
    mask = np.zeros(n_spectra, dtype=bool)
    mask[valid] = two.predict_proba(column)[:, high] >= 0.5
    mask = _cleanup(mask, grid, shape, profile) & valid
    return ForegroundResult(
        mask,
        score.astype(np.float32),
        bool((valid & ~mask).any()),
        f"bimodal score (BIC {bic_one:.0f} > {bic_two:.0f}, Ashman's D {separation:.2f}), "
        f"about {ions[0]:.0f} and {ions[1]:.0f} recurrent ions per spectrum",
    )
