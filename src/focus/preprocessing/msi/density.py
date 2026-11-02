"""Density valleys and contiguous feature ranges on the m/z grid."""

import numpy as np
from numba import njit


# --- m/z backbone (docs/scientific/msi_methods.md §4.3) ---


@njit(cache=True)
def valley_heights(density: np.ndarray, maxima: np.ndarray) -> np.ndarray:
    """`density` at the lowest point between each pair of neighbouring maxima (n - 1 values)."""
    heights = np.empty(max(maxima.size - 1, 0), dtype=np.float64)
    for k in range(maxima.size - 1):
        heights[k] = np.min(density[maxima[k] : maxima[k + 1] + 1])
    return heights


@njit(cache=True)
def density_feature_ranges(
    density: np.ndarray, maxima: np.ndarray, half_width: float
) -> tuple[np.ndarray, np.ndarray]:
    """
    Bin range [left, right] of every density maximum.

    A range reaches the lowest point of `density` between the maximum and each neighbouring maximum
    (the valley belongs to the lower-mass feature), and at most `half_width` bins from the maximum.
    """
    n = maxima.size
    left = np.empty(n, dtype=np.int64)
    right = np.empty(n, dtype=np.int64)
    for k in range(n):
        left[k] = max(maxima[k] - half_width, 0)
        right[k] = min(maxima[k] + half_width, density.size - 1)
    for k in range(n - 1):
        start = maxima[k]
        valley = start + np.argmin(density[start : maxima[k + 1] + 1])
        right[k] = min(right[k], valley)
        left[k + 1] = max(left[k + 1], valley + 1)
    return left, right
