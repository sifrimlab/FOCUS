"""Bin one spectrum onto the shared m/z reference with the original interpolation rule."""

import numpy as np

from numba import njit


@njit(cache=True)
def interpolate_single(
    original_mz: np.ndarray,
    original_intensity: np.ndarray,
    reference_mz: np.ndarray,
    mass_tolerance: float,
) -> np.ndarray:
    """
    Bin one spectrum onto the reference m/z grid.

    Each peak adds its whole intensity to the single nearest reference feature, provided that
    feature lies within `mass_tolerance` ppm of the peak; peaks with no feature in reach are
    dropped. On an exact distance tie the lower feature wins. Because a peak is never split
    between features, a small residual m/z error does not move intensity between neighbouring
    features.
    """
    n_ref = len(reference_mz)
    result = np.zeros(n_ref, dtype=original_intensity.dtype)

    if len(original_mz) == 0 or n_ref == 0:
        return result

    for i in range(len(original_mz)):
        mz_peak = original_mz[i]
        right = np.searchsorted(reference_mz, mz_peak)
        best = -1
        best_ppm = float(mass_tolerance)
        if right > 0:
            ppm = abs(mz_peak - reference_mz[right - 1]) / mz_peak * 1e6
            if ppm <= best_ppm:
                best = right - 1
                best_ppm = ppm
        if right < n_ref:
            ppm = abs(reference_mz[right] - mz_peak) / mz_peak * 1e6
            if ppm < best_ppm or (best < 0 and ppm <= best_ppm):
                best = right
        if best >= 0:
            result[best] += original_intensity[i]

    return result
