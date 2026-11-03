"""Bin centroid spectra onto the shared m/z backbone, sharing peaks between features by Gaussian weight."""

import numpy as np

from numba import njit


@njit(cache=True)
def bin_spectra_csr(
    flat_mz: np.ndarray,
    flat_intensity: np.ndarray,
    offsets: np.ndarray,
    reference_mz: np.ndarray,
    sigma_ppm: np.ndarray,
    mass_tolerance: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bin spectra onto the reference m/z features; CSR rows (indptr, indices, float32 data).

    Peak p of m/z m and intensity I reaches every feature j whose m/z mu_j lies within
    `mass_tolerance` ppm, with the Gaussian weight G_pj = exp(-0.5 (d_pj / sigma_j)^2),
    d_pj = (m - mu_j) / mu_j * 1e6. Feature j of the spectrum receives I * G_pj / sum_k G_pk, the
    responsibility of j for the peak: a feature alone in reach takes the whole peak, whatever its mass
    error, and features closer than the tolerance share it smoothly by weight; the intensity of every
    peak in reach is conserved. Several peaks reaching one feature are summed. `offsets` delimit the
    spectra in the flattened arrays (n_spectra + 1).
    """
    n_spectra = offsets.size - 1
    n_ref = reference_mz.size
    indptr = np.zeros(n_spectra + 1, dtype=np.int64)
    capacity = max(16, int(offsets[-1]))
    indices = np.empty(capacity, dtype=np.int32)
    data = np.empty(capacity, dtype=np.float32)
    row = np.zeros(max(n_ref, 1), dtype=np.float64)
    mark = np.full(max(n_ref, 1), -1, dtype=np.int64)
    touched = np.empty(max(n_ref, 1), dtype=np.int64)
    tolerance = mass_tolerance * 1e-6
    nnz = 0
    for s in range(n_spectra):
        n_touched = 0
        if n_ref > 0:
            for k in range(offsets[s], offsets[s + 1]):
                mz = flat_mz[k]
                value = flat_intensity[k]
                if mz <= 0.0 or value == 0.0:
                    continue
                lo = np.searchsorted(reference_mz, mz / (1.0 + tolerance))
                hi = np.searchsorted(reference_mz, mz / (1.0 - tolerance), side="right")
                total = 0.0
                for j in range(lo, hi):
                    d = (mz - reference_mz[j]) / reference_mz[j] * 1e6
                    if abs(d) <= mass_tolerance:
                        total += np.exp(-0.5 * (d / sigma_ppm[j]) ** 2)
                if total <= 0.0:
                    continue
                scale = value / total
                for j in range(lo, hi):
                    d = (mz - reference_mz[j]) / reference_mz[j] * 1e6
                    if abs(d) > mass_tolerance:
                        continue
                    if mark[j] != s:
                        mark[j] = s
                        row[j] = 0.0
                        touched[n_touched] = j
                        n_touched += 1
                    row[j] += scale * np.exp(-0.5 * (d / sigma_ppm[j]) ** 2)
        if nnz + n_touched > capacity:
            capacity = max(2 * capacity, nnz + n_touched)
            grown_indices = np.empty(capacity, dtype=np.int32)
            grown_data = np.empty(capacity, dtype=np.float32)
            grown_indices[:nnz] = indices[:nnz]
            grown_data[:nnz] = data[:nnz]
            indices, data = grown_indices, grown_data
        columns = np.sort(touched[:n_touched])
        for t in range(n_touched):
            j = columns[t]
            indices[nnz] = j
            data[nnz] = row[j]
            nnz += 1
        indptr[s + 1] = nnz
    return indptr, indices[:nnz].copy(), data[:nnz].copy()


def bin_spectrum(
    mz: np.ndarray,
    intensity: np.ndarray,
    reference_mz: np.ndarray,
    sigma_ppm: np.ndarray | float,
    mass_tolerance: float,
) -> np.ndarray:
    """One spectrum binned onto the reference features (:func:`bin_spectra_csr`), as a dense row."""
    reference_mz = np.asarray(reference_mz, dtype=np.float64)
    indptr, indices, data = bin_spectra_csr(
        np.asarray(mz, dtype=np.float64),
        np.asarray(intensity, dtype=np.float64),
        np.array([0, np.asarray(mz).size], dtype=np.int64),
        reference_mz,
        np.broadcast_to(
            np.asarray(sigma_ppm, dtype=np.float64), reference_mz.shape
        ).copy(),
        float(mass_tolerance),
    )
    row = np.zeros(reference_mz.size, dtype=np.float32)
    row[indices] = data
    return row
