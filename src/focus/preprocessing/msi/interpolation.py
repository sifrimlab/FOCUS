"""Parallel Gaussian binning of one sample and ion mode onto the backbone, as a CSR matrix."""

from __future__ import annotations

from functools import partial

import numpy as np
import scipy.sparse as sp
from joblib import Parallel, delayed

import focus.runtime.resources as resources
from focus.preprocessing.msi.binning import bin_spectra_csr
from focus.preprocessing.msi.matching import _flatten_spectra


def empty_csr(rows: int, cols: int) -> sp.csr_matrix:
    """An all-zero float32 CSR matrix of the given shape."""
    return sp.csr_matrix(
        (
            np.zeros(0, dtype=np.float32),
            np.zeros(0, dtype=np.int32),
            np.zeros(rows + 1, dtype=np.int64),
        ),
        shape=(rows, cols),
    )


def interpolate_spectra(
    original_mzs_list: list[np.ndarray],
    original_intensities_list: list[np.ndarray],
    reference_mz: np.ndarray,
    sigma_ppm: np.ndarray,
    mass_tolerance: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bin a chunk of spectra onto the reference m/z features (:func:`bin_spectra_csr`).

    Returns the chunk's CSR arrays (indptr, indices, float32 data), one row per spectrum.
    """
    flat_mz, flat_intensity, offsets = _flatten_spectra(
        original_mzs_list, original_intensities_list
    )
    return bin_spectra_csr(
        flat_mz,
        flat_intensity,
        offsets,
        np.asarray(reference_mz, dtype=np.float64),
        np.asarray(sigma_ppm, dtype=np.float64),
        float(mass_tolerance),
    )


def interpolate_mode(
    original_mzs: list[np.ndarray],
    intensities: list[np.ndarray],
    reference_mz: np.ndarray,
    sigma_ppm: np.ndarray,
    mass_tolerance: float,
) -> sp.csr_matrix:
    """Bin one polarity in ordered loky chunks; float32 CSR (n_spectra, n_features)."""
    datapoints = len(intensities)
    n_ref = len(reference_mz)
    if datapoints == 0 or n_ref == 0:
        return empty_csr(datapoints, n_ref)

    num_workers = min(resources.available_cpus() or 1, datapoints)
    chunk_size = (datapoints + num_workers - 1) // num_workers  # ceil division
    bounds = [
        (i * chunk_size, min((i + 1) * chunk_size, datapoints))
        for i in range(num_workers)
        if i * chunk_size < datapoints
    ]

    # Module-level worker so joblib does not pickle the dataset.
    worker_func = partial(
        interpolate_spectra,
        reference_mz=reference_mz,
        sigma_ppm=sigma_ppm,
        mass_tolerance=mass_tolerance,
    )
    results = Parallel(n_jobs=len(bounds), backend="loky", verbose=0)(
        delayed(worker_func)(original_mzs[start:end], intensities[start:end])
        for start, end in bounds
    )

    indptr_parts, index_parts, data_parts = [np.zeros(1, dtype=np.int64)], [], []
    nnz = 0
    for chunk_indptr, chunk_indices, chunk_data in results:
        indptr_parts.append(chunk_indptr[1:] + nnz)
        index_parts.append(chunk_indices)
        data_parts.append(chunk_data)
        nnz += int(chunk_indptr[-1])
    return sp.csr_matrix(
        (
            np.concatenate(data_parts),
            np.concatenate(index_parts),
            np.concatenate(indptr_parts),
        ),
        shape=(datapoints, n_ref),
    )
