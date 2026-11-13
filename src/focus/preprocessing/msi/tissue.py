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
    feature_intensity_fraction,
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
    LIPID_MAX_BACKGROUND_RATIO,
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


def lipid_fraction(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    context: SpectraContext,
    annotated: np.ndarray,
) -> np.ndarray:
    """Per spectrum, the share of its peak intensity in the `annotated` recurrent ions."""
    lookup_lo, lookup = feature_lookup(context.features.left, context.features.right)
    flat_mz, flat_intensity, offsets = _flatten_spectra(mz_list, intensity_list)
    return feature_intensity_fraction(
        flat_mz,
        flat_intensity,
        offsets,
        context.log_bin_width,
        lookup_lo,
        lookup,
        np.asarray(annotated, dtype=np.bool_),
    )


def _on_raster(
    score: np.ndarray, grid: np.ndarray, shape: tuple[int, int], size: int
) -> np.ndarray:
    """The score median-filtered over `size` x `size` pixels of the raster (unchanged below 2)."""
    if size <= 1:
        return score
    raster = np.zeros(shape, dtype=np.float64)
    raster[grid[:, 0], grid[:, 1]] = score
    return np.asarray(
        median_filter(raster, size=size, mode="nearest")[grid[:, 0], grid[:, 1]],
        dtype=np.float64,
    )


@dataclass
class _Modes:
    """The two-component split of a score, or why every spectrum stays foreground."""

    model: GaussianMixture | None
    summary: str


def _two_modes(values: np.ndarray, content: str, max_ratio: float) -> _Modes:
    """
    Split `values` into a background and a foreground mode (§5.2 of the MSI methods).

    `content` names what the score measures: "ions" (log recurrent ions; the content of a mode is
    expm1 of its mean) or "lipids" (the lipid fraction itself). The split is refused when BIC prefers
    one component, when the modes overlap (Ashman's D below `MIN_SEPARATION`), or when the lower mode
    holds more than `max_ratio` of the content of the higher one (two tissue regions).
    """
    if values.size < 4 or np.ptp(values) < 1e-12:
        return _Modes(None, "degenerate score")
    column = values.reshape(-1, 1)
    one = GaussianMixture(n_components=1, random_state=0).fit(column)
    two = GaussianMixture(n_components=2, random_state=0, n_init=3).fit(column)
    bic_one, bic_two = one.bic(column), two.bic(column)
    if bic_one <= bic_two:
        return _Modes(None, f"unimodal score (BIC {bic_one:.0f} <= {bic_two:.0f})")
    means, variances = two.means_.ravel(), two.covariances_.ravel()
    separation = float(np.sqrt(2) * abs(means[0] - means[1]) / np.sqrt(variances.sum()))
    if separation < MIN_SEPARATION:
        return _Modes(
            None,
            f"overlapping score modes (Ashman's D {separation:.2f} < {MIN_SEPARATION:g})",
        )
    levels = np.sort(np.expm1(means) if content == "ions" else means)
    described = (
        f"about {levels[0]:.0f} and {levels[1]:.0f} recurrent ions per spectrum"
        if content == "ions"
        else f"lipid fraction about {levels[0]:.2f} and {levels[1]:.2f}"
    )
    if levels[0] > max_ratio * levels[1]:
        return _Modes(None, f"two tissue-like score modes ({described})")
    return _Modes(
        two,
        f"bimodal score (BIC {bic_one:.0f} > {bic_two:.0f}, Ashman's D {separation:.2f}), "
        + described,
    )


def detect_foreground(
    mz_list: list[np.ndarray],
    intensity_list: list[np.ndarray],
    pixel_coordinates: np.ndarray,
    context: SpectraContext,
    profile: SampleProfile,
    lipid_score: np.ndarray | None = None,
) -> ForegroundResult:
    """
    Foreground spectra of a tissue section or a cell culture.

    Score: log(1 + the number of recurrent ions of the sample a spectrum holds above its noise gate,
    :func:`recurrent_detections`). Noise centroids never recur, so a background of noise scores low.
    A background of matrix ions can hold about as many recurrent ions as tissue (matrix clusters);
    with a lipid database, `lipid_score` (:func:`lipid_fraction`) separates it instead and is tried
    first, the recurrent-ion score deciding when it does not split. With ``profile.score_median``
    each score is median-filtered on the raster first. :func:`_two_modes` decides whether a score
    separates a background; if none does, every spectrum with peaks is foreground, otherwise the
    spectra whose posterior of the higher-mean component is at least 0.5. The preset's spatial
    cleanup follows. ``score`` is always the recurrent-ion score.
    """
    n_peaks = np.fromiter(
        (mz.size for mz in mz_list), dtype=np.int64, count=len(mz_list)
    )
    valid = n_peaks > 0
    grid, shape = _raster(pixel_coordinates)
    ions = _on_raster(
        np.log1p(recurrent_detections(mz_list, intensity_list, context)).astype(
            np.float64
        ),
        grid,
        shape,
        profile.score_median,
    )
    candidates: list[tuple[str, np.ndarray, str, float]] = []
    if lipid_score is not None:
        lipids = _on_raster(
            np.asarray(lipid_score, dtype=np.float64), grid, shape, profile.score_median
        )
        candidates.append(
            ("lipid fraction", lipids, "lipids", LIPID_MAX_BACKGROUND_RATIO)
        )
    candidates.append(("recurrent ions", ions, "ions", MAX_BACKGROUND_RATIO))
    mask, summary = _first_split(candidates, valid, grid, shape, profile)
    if mask is None:
        return ForegroundResult(valid, ions.astype(np.float32), False, summary)
    return ForegroundResult(
        mask, ions.astype(np.float32), bool((valid & ~mask).any()), summary
    )


def _first_split(
    candidates: list[tuple[str, np.ndarray, str, float]],
    valid: np.ndarray,
    grid: np.ndarray,
    shape: tuple[int, int],
    profile: SampleProfile,
) -> tuple[np.ndarray | None, str]:
    """
    The cleaned foreground of the first score (name, values, content, ratio) that separates a
    background, with a summary of every score tried; None when none does.
    """
    tried: list[str] = []
    for name, score, content, ratio in candidates:
        modes = _two_modes(score[valid], content, ratio)
        if modes.model is None:
            tried.append(f"{name}: {modes.summary}")
            continue
        high = int(np.argmax(modes.model.means_.ravel()))
        mask = np.zeros(valid.size, dtype=bool)
        posterior = modes.model.predict_proba(score[valid].reshape(-1, 1))[:, high]
        mask[valid] = posterior >= 0.5
        mask = _cleanup(mask, grid, shape, profile) & valid
        return mask, "; ".join([*tried, f"{name}: {modes.summary}"])
    return None, "; ".join(tried) + ", every spectrum kept"
