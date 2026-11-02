"""Detect MSI tissue from spectral-complexity features and spatial mask cleanup."""

from __future__ import annotations
from typing import cast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
from focus.runtime.reporter_context import get_reporter
from sklearn.mixture import GaussianMixture
import pandas as pd
from scipy.ndimage import (
    binary_fill_holes,
    binary_opening,
)

from focus.config.keys import MsiMetadata
from focus.config.options import MsiSampleType


def detect_tissue_spots(
    sample: MsiSample,
    mz_vectors: list[np.ndarray],
    intensity_vectors: list[np.ndarray],
    ion_mode: str,
    database: pd.DataFrame | None = None,
    mass_tolerance: int | None = None,
) -> list[int]:
    """
    Identify tissue/cell spots from background using spectral complexity features.

    Tissue spots have richer, more diverse spectra than background spots (which are
    dominated by uniformly sprayed standards and slide coating). Three parameter-free
    features capture this: Shannon entropy, number of detected peaks, and log(TIC).
    An optional annotation DB hit ratio is added as a 4th feature when a database and
    mass_tolerance are provided.

    Behavior adapts based on self._sample_type:
      "tissue"   -- contiguous tissue section. A 2-component GMM is fit; BIC selects
                               between 1-component (unimodal → keep all) and 2-component (bimodal →
                               classify by posterior). This handles class-imbalanced distributions
                               (e.g. 95 % tissue / 5 % background) where Otsu splits the dominant
                               tissue mode instead of separating it from background. Morphological
                               cleanup (hole filling + opening) is applied afterwards.
      "microgrid" -- isolated single cells on a mostly empty slide. Delegated to
                               _detect_microgrid_spots, which models the background explicitly and
                               detects cells as sparse local outliers.

    Returns
    -------
    list[int]
            Indices of spots classified as foreground (tissue/cell).
    """
    n_spots = len(mz_vectors)
    if len(intensity_vectors) != n_spots:
        raise ValueError("intensity_vectors must have same length as mz_vectors")
    if n_spots == 0:
        return []

    if sample._sample_type == MsiSampleType.MICROGRID:
        return sample.foreground._detect_microgrid_spots(
            mz_vectors=mz_vectors,
            intensity_vectors=intensity_vectors,
            ion_mode=ion_mode,
            database=database,
            mass_tolerance=mass_tolerance,
        )

    # ------------------------------------------------------------------
    # 1. Per-spot spectral complexity features
    # ------------------------------------------------------------------
    n_peaks = np.array([len(mz) for mz in mz_vectors], dtype=np.int32)
    tic = np.zeros(n_spots, dtype=np.float64)
    entropy = np.zeros(n_spots, dtype=np.float64)

    for i in range(n_spots):
        if n_peaks[i] == 0:
            continue
        iv = np.asarray(intensity_vectors[i], dtype=np.float64)
        tic[i] = iv.sum()
        if n_peaks[i] < 2 or tic[i] <= 0:
            continue
        p = iv / tic[i]

        p = p[p > 0]
        entropy[i] = -np.sum(p * np.log2(p))

    valid = n_peaks > 0
    if not np.any(valid):
        return []

    # ------------------------------------------------------------------
    # 2. Optional annotation DB hit-ratio feature
    # ------------------------------------------------------------------
    features: list[np.ndarray] = [
        entropy,
        n_peaks.astype(np.float64),
        np.log1p(tic),
    ]

    if database is not None and mass_tolerance is not None:
        db_subset = database[database["ion_mode"] == ion_mode]
        if not db_subset.empty:
            masses = np.sort(db_subset["ionized_mass"].to_numpy(dtype=np.float64))
            hit_ratio = np.zeros(n_spots, dtype=np.float64)
            for i in range(n_spots):
                if n_peaks[i] == 0:
                    continue
                mz = np.asarray(mz_vectors[i], dtype=np.float64)
                idx = np.searchsorted(masses, mz)
                left = np.clip(idx - 1, 0, masses.size - 1)
                right = np.clip(idx, 0, masses.size - 1)
                tol_l = masses[left] * mass_tolerance * 1e-6
                tol_r = masses[right] * mass_tolerance * 1e-6
                within_l = np.abs(mz - masses[left]) <= tol_l
                within_r = np.abs(mz - masses[right]) <= tol_r
                within = within_l | within_r
                if np.any(within):
                    w = np.where(within)[0]
                    chosen = np.where(within_l[w], left[w], right[w])
                    hit_ratio[i] = len(np.unique(chosen)) / n_peaks[i]
            features.append(hit_ratio)

    # ------------------------------------------------------------------
    # 3. Min-max normalise each feature and average into a single score
    # ------------------------------------------------------------------
    score = np.zeros(n_spots, dtype=np.float64)
    for feat in features:
        vals = feat[valid]
        lo, hi = vals.min(), vals.max()
        if hi - lo > 1e-12:
            norm = np.zeros(n_spots, dtype=np.float64)
            norm[valid] = (feat[valid] - lo) / (hi - lo)
            score += norm
    score /= len(features)

    # ------------------------------------------------------------------
    # 4. Threshold: GMM + BIC model selection
    # ------------------------------------------------------------------
    valid_scores = score[valid]
    lo, hi = valid_scores.min(), valid_scores.max()

    tissue_mask = np.zeros(n_spots, dtype=bool)

    # Tissue section: use GMM + BIC to decide between unimodal (all tissue)
    # and bimodal (tissue + background).  This handles imbalanced distributions
    # (e.g. 95 % tissue / 5 % background) where Otsu tends to split the
    # dominant tissue mode rather than separating it from background.
    n_valid = valid_scores.shape[0]
    if n_valid < 4 or hi - lo < 1e-12:
        get_reporter().detail(
            f"Tissue detection (tissue): degenerate score distribution -- treating all {n_spots} spots as foreground."
        )
        return cast(list[int], np.where(valid)[0].tolist())

    vs2d = valid_scores.reshape(-1, 1)
    gmm1 = GaussianMixture(n_components=1, random_state=0).fit(vs2d)
    gmm2 = GaussianMixture(n_components=2, random_state=0, n_init=3).fit(vs2d)
    bic1, bic2 = gmm1.bic(vs2d), gmm2.bic(vs2d)

    if bic1 <= bic2:
        # BIC prefers unimodal model -- keep all valid spots as tissue
        get_reporter().detail(
            f"Tissue detection (tissue): GMM BIC prefers 1-component "
            f"(BIC1={bic1:.1f} <= BIC2={bic2:.1f}) -- treating all {n_spots} spots as foreground, "
            f"n_features={len(features)}, ion_mode={ion_mode}"
        )
        return cast(list[int], np.where(valid)[0].tolist())

    # 2-component model wins: classify by posterior on the higher-mean component
    tissue_comp = int(np.argmax(gmm2.means_.ravel()))
    posteriors = gmm2.predict_proba(vs2d)[:, tissue_comp]
    tissue_mask[valid] = posteriors >= 0.5
    mu0, mu1 = gmm2.means_.ravel()
    pi0, pi1 = gmm2.weights_
    _log_info = (
        f"GMM 2-comp (BIC1={bic1:.1f}, BIC2={bic2:.1f}), "
        f"means=[{mu0:.3f},{mu1:.3f}], weights=[{pi0:.2f},{pi1:.2f}]"
    )

    # ------------------------------------------------------------------
    # 5. Spatial morphological cleanup (tissue section only)
    # ------------------------------------------------------------------
    if sample._sample_type == MsiSampleType.TISSUE:
        pixel_coords = sample._metadata[ion_mode][
            MsiMetadata.PIXEL_COORDINATES
        ]  # (N, 2): (y, x)
        if pixel_coords is not None and len(pixel_coords) == n_spots:
            grid_h = int(pixel_coords[:, 0].max()) + 1
            grid_w = int(pixel_coords[:, 1].max()) + 1
            raster = np.zeros((grid_h, grid_w), dtype=bool)
            for i in range(n_spots):
                if tissue_mask[i]:
                    raster[pixel_coords[i, 0], pixel_coords[i, 1]] = True
            raster = binary_fill_holes(raster)
            raster = binary_opening(raster, structure=np.ones((3, 3), dtype=bool))
            for i in range(n_spots):
                tissue_mask[i] = raster[pixel_coords[i, 0], pixel_coords[i, 1]]

    kept = np.where(tissue_mask)[0]
    get_reporter().detail(
        f"Tissue detection ({sample._sample_type}): kept {kept.size} / {n_spots} spots "
        f"({_log_info}, n_features={len(features)}, ion_mode={ion_mode})"
    )
    return cast(list[int], kept.tolist())
