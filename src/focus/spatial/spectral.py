"""Signal rank of a spectral matrix and NaN-aware block averaging, shared by Raman stages.

The rank is the number of singular values above the Gavish-Donoho optimal hard threshold for an
unknown noise level (Gavish and Donoho 2014), ``omega(beta) * median(singular values)``, with
``beta`` the aspect ratio of the matrix. Preprocessing uses it to choose the denoising subspace,
registration to set the number of endmembers of the unmixing model.
"""

from __future__ import annotations

import numpy as np

# Spectra are averaged over bins of this size (µm) before the rank is chosen: the cellular scale at
# which compositional components rise above the per-pixel noise. FOCUS design choice, calibrated on
# P762 (rank 0 to 1 per pixel, 2 to 3 at 4.5 µm).
CELLULAR_BIN_UM = 4.5
# Spectra sampled for a decomposition.
DECOMPOSITION_SAMPLES = 300_000


def omega(beta: float) -> float:
    """Gavish-Donoho threshold coefficient for an unknown noise level (aspect ratio ``beta``)."""
    return 0.56 * beta**3 - 0.95 * beta**2 + 1.82 * beta + 1.43


def hard_threshold_rank(singular: np.ndarray, rows: int, columns: int) -> int:
    """Number of ``singular`` values of a ``rows`` x ``columns`` matrix above the threshold (>= 1)."""
    beta = min(rows, columns) / max(rows, columns)
    return max(1, int((singular > omega(beta) * np.median(singular)).sum()))


def signal_rank(spectra: np.ndarray, seed: int = 0) -> tuple[int, np.ndarray]:
    """Rank of ``(N, C)`` spectra (finite rows, mean removed) and their singular values."""
    finite = spectra[np.isfinite(spectra).all(axis=1)].astype(np.float64)
    if len(finite) > DECOMPOSITION_SAMPLES:
        rng = np.random.default_rng(seed)
        finite = finite[rng.choice(len(finite), DECOMPOSITION_SAMPLES, replace=False)]
    if len(finite) < spectra.shape[1] + 1:
        raise ValueError("too few measured spectra to estimate the spectral rank")
    singular = np.linalg.svd(finite - finite.mean(axis=0), compute_uv=False)
    return hard_threshold_rank(singular, len(finite), spectra.shape[1]), singular


MIN_OFFSET = "min"
NO_OFFSET = "none"
AREA = "area"
NO_NORMALIZATION = "none"


def spot_spectra(spectra: np.ndarray, offset: str, normalization: str) -> np.ndarray:
    """Offset removal and normalisation of averaged ``(N, C)`` spectra (spot means, bins).

    ``offset`` ``"min"`` subtracts each spectrum's minimum over the channels (the nearly flat
    non-resonant SRS background); ``normalization`` ``"area"`` divides each spectrum by its mean
    over the channels (NaN when that mean is not positive). FOCUS applies both to spectra averaged
    over many pixels, never to single pixels: on a noisy pixel the minimum and the mean are biased
    by an amount that follows the local signal-to-noise ratio, so a per-pixel normalisation prints
    the illumination (and the tile grid) into the spectral shape.
    """
    out = np.asarray(spectra, dtype=np.float64).copy()
    if out.ndim != 2 or out.shape[1] < 2:
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        if offset == MIN_OFFSET:
            out -= np.min(out, axis=1, keepdims=True)
        if normalization == AREA:
            level = np.mean(out, axis=1, keepdims=True)
            out /= np.where(level > 0, level, np.nan)
    return out


def nan_block_mean(image: np.ndarray, factor: int) -> np.ndarray:
    """Mean over ``factor`` x ``factor`` blocks of the finite samples (NaN where none)."""
    from skimage.transform import downscale_local_mean

    if factor == 1:
        return image
    finite = np.isfinite(image)
    total = downscale_local_mean(np.where(finite, image, 0), (factor, factor))  # type: ignore[no-untyped-call]
    count = downscale_local_mean(finite.astype(np.float32), (factor, factor))  # type: ignore[no-untyped-call]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 0, total / count, np.nan).astype(np.float32)
