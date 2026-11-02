"""Interpolation for MSI preprocessing."""

from __future__ import annotations

import numpy as np


from focus.preprocessing.msi.binning import interpolate_single


from joblib import Parallel, delayed
from functools import partial

import focus.runtime.resources as resources


def interpolate_spectra(
    original_mzs_list: list[np.ndarray],
    original_intensities_list: list[np.ndarray],
    reference_mz: np.ndarray,
    mass_tolerance: float,
) -> np.ndarray:
    """
    Rebin intensities to reference M/Z vector. This method processes a chunk of spectra.
    Each original peak adds its whole intensity to the nearest reference M/Z bin within the mass tolerance
    (see interpolate_single). If a peak does not fall within the mass tolerance of any reference bin, it is ignored.

    Parameters
    ----------
    original_mzs_list : list of np.ndarray
            List of original M/Z arrays for each spectrum in the chunk.
    original_intensities_list : list of np.ndarray
            List of original intensity arrays for each spectrum in the chunk.
    reference_mz : np.ndarray
            The reference M/Z vector to interpolate to.
    mass_tolerance : float
            Mass tolerance in ppm for matching peaks to reference bins.

    Returns
    -------
    np.ndarray
            A 2D array of shape (n_datapoints, n_ref) containing the interpolated intensities.
    """
    n_datapoints = len(original_mzs_list)
    n_ref = len(reference_mz)

    if n_datapoints == 0 or n_ref == 0:
        fallback_dtype = (
            original_intensities_list[0].dtype
            if original_intensities_list
            else np.float32
        )
        return np.zeros((n_datapoints, n_ref), dtype=fallback_dtype)

    out_dtype = original_intensities_list[0].dtype
    result_matrix = np.zeros((n_datapoints, n_ref), dtype=out_dtype)

    for idx, (original_mz, original_intensity) in enumerate(
        zip(original_mzs_list, original_intensities_list)
    ):
        if original_mz.size == 0:
            continue

        mz = original_mz.astype(reference_mz.dtype, copy=False)
        intens = original_intensity

        if mz.size != intens.size:
            raise ValueError("mz and intensity must have same length")

        # Sort once (your existing logic)
        if not np.all(mz[:-1] <= mz[1:]):
            order = np.argsort(mz)
            mz = mz[order]
            intens = intens[order]

        # THE KEY CHANGE: Use searchsorted version
        result_matrix[idx] = interpolate_single(
            mz, intens, reference_mz, mass_tolerance
        )

    return result_matrix


def interpolate_mode(
    original_mzs: list[np.ndarray],
    intensities: list[np.ndarray],
    reference_mz: np.ndarray,
    dtype: type[np.float32] | type[np.float64],
    mass_tolerance: int,
) -> np.ndarray:
    """Bin one polarity using the unchanged ordered loky chunks."""
    merged_intensities = np.zeros((len(intensities), len(reference_mz)), dtype=dtype)

    intensities_mode = intensities
    original_mzs_mode = original_mzs
    datapoints = len(intensities_mode)

    num_workers = min(resources.available_cpus() or 1, datapoints)
    chunk_size = (datapoints + num_workers - 1) // num_workers  # ceil division

    chunks = [
        (original_mzs_mode[start:end], intensities_mode[start:end])
        for start, end in [
            (i * chunk_size, min((i + 1) * chunk_size, datapoints))
            for i in range(num_workers)
        ]
    ]

    # Reference via the class (not self) so joblib does not pickle the entire dataset.
    worker_func = partial(
        interpolate_spectra, reference_mz=reference_mz, mass_tolerance=mass_tolerance
    )

    results = Parallel(n_jobs=num_workers, backend="loky", verbose=0)(
        delayed(worker_func)(orig_chunk, intens_chunk)
        for orig_chunk, intens_chunk in chunks
    )

    current_idx = 0
    for chunk_result in results:
        chunk_len = chunk_result.shape[0]
        merged_intensities[current_idx : current_idx + chunk_len, :] = chunk_result
        current_idx += chunk_len
    return merged_intensities
