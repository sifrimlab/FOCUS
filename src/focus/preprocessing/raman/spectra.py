"""Spectral processing of one multi-channel acquisition: denoise, offset, normalisation, quantisation.

Denoising projects every pixel's spectrum on the principal components that are real at the cellular
scale. The components and the rank come from spectra averaged over ``RANK_BIN_UM`` bins (saturated
or partly measured bins left out); the rank is the number of singular values above the
Gavish-Donoho optimal hard threshold for an unknown noise level (Gavish and Donoho 2014),
``omega(beta) * median(singular values)``. At least the first component is kept. The spectral
directions left out are noise at the cellular scale; removing them reduces the per-pixel noise
without smoothing in space or along the spectrum.

The non-resonant SRS background (cross-phase modulation, thermal lensing, two-photon absorption) is
nearly flat across a spectral window (Berto et al. 2014); ``remove_offset`` subtracts each pixel's
minimum over the window. ``area`` normalisation divides each spectrum by its mean over the window,
so it describes composition independent of local density.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from focus.config.options import RamanNormalization
from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.spatial.spectral import hard_threshold_rank


@dataclass(frozen=True)
class SpectralModel:
    """Mean spectrum and the kept components (rows of ``components``), in output channel order."""

    mean: np.ndarray
    components: np.ndarray
    singular_values: np.ndarray

    @property
    def rank(self) -> int:
        """Number of kept components."""
        return int(self.components.shape[0])


def fit(spectra: np.ndarray) -> SpectralModel:
    """Model of ``(N, C)`` binned spectra (rows with a non-finite value are ignored)."""
    finite = spectra[np.isfinite(spectra).all(axis=1)].astype(np.float64)
    if len(finite) > tech.DECOMPOSITION_SAMPLES:
        rng = np.random.default_rng(0)
        finite = finite[
            rng.choice(len(finite), tech.DECOMPOSITION_SAMPLES, replace=False)
        ]
    channels = spectra.shape[1]
    if len(finite) < channels + 1:
        raise ValueError("too few measured spectra to model the acquisition")
    mean = finite.mean(axis=0)
    _, singular, vt = np.linalg.svd(finite - mean, full_matrices=False)
    rank = hard_threshold_rank(singular, len(finite), channels)
    return SpectralModel(
        mean=mean.astype(np.float32),
        components=vt[:rank].astype(np.float32),
        singular_values=singular.astype(np.float32),
    )


def quantization_step(settings: RamanRunSettings, channels: int) -> float:
    """Grid step of the stored values (detector counts, or area-normalised units)."""
    if settings.normalization == RamanNormalization.AREA and channels > 1:
        return tech.QUANTIZATION_AREA
    return tech.QUANTIZATION_COUNTS


def process_block(
    block: np.ndarray,
    model: SpectralModel | None,
    settings: RamanRunSettings,
) -> np.ndarray:
    """Process a ``(C, Y, X)`` block of flat-field corrected spectra (NaN where not measured)."""
    channels = block.shape[0]
    out = block.astype(np.float32, copy=True)
    if channels > 1:
        flat = out.reshape(channels, -1)
        if model is not None:
            centred = flat.T - model.mean
            projected = centred @ model.components.T @ model.components + model.mean
            flat[:] = projected.T
        if settings.remove_offset:
            with np.errstate(invalid="ignore"):
                flat -= np.min(flat, axis=0, keepdims=True)
        if settings.normalization == RamanNormalization.AREA:
            with np.errstate(invalid="ignore", divide="ignore"):
                level = np.mean(flat, axis=0, keepdims=True)
                flat /= np.where(level > 0, level, np.nan)
    step = quantization_step(settings, channels)
    quantised: np.ndarray = (np.rint(out / step) * step).astype(np.float32)
    return quantised
