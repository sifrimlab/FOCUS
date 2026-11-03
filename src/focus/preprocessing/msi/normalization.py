"""Row normalizations of the binned MSI intensities, each at a fixed scale shared by every sample."""

from typing import cast, overload

import numpy as np
import scipy.sparse as sp

from focus.config.options import MsiIntensityNormalization
from focus.preprocessing.msi.recalibration import neighbourhood_nanmedian
from focus.runtime.reporter_context import get_reporter


def _row_median_nonzero(raw: np.ndarray | sp.csr_matrix) -> np.ndarray:
    """Median of each row's nonzero entries; NaN for a row without any."""
    median = np.full(raw.shape[0], np.nan, dtype=np.float64)
    if sp.issparse(raw):
        csr = sp.csr_matrix(raw)
        for i in range(csr.shape[0]):
            values = csr.data[csr.indptr[i] : csr.indptr[i + 1]]
            values = values[values != 0]
            if values.size:
                median[i] = np.median(values)
        return median
    for i, row in enumerate(raw):
        values = row[row != 0]
        if values.size:
            median[i] = np.median(values)
    return median


def _divisor(
    raw: np.ndarray | sp.csr_matrix,
    method: str,
    pixel_coordinates: np.ndarray | None,
    standard_columns: np.ndarray | None,
) -> np.ndarray:
    """Per-spectrum divisor of `method`; 1 for a spectrum without signal."""
    n_features = raw.shape[1]
    if method == MsiIntensityNormalization.TIC:
        divisor = np.asarray(raw.sum(axis=1), dtype=np.float64).ravel() / n_features
    elif method == MsiIntensityNormalization.MEDIAN:
        divisor = _row_median_nonzero(raw)
    elif method == MsiIntensityNormalization.RMS:
        squares = (
            raw.multiply(raw).sum(axis=1)
            if sp.issparse(raw)
            else (raw.astype(np.float64) ** 2).sum(axis=1)
        )
        divisor = np.sqrt(np.asarray(squares, dtype=np.float64).ravel() / n_features)
    elif method == MsiIntensityNormalization.INTERNAL_STANDARD:
        if (
            standard_columns is None
            or len(standard_columns) == 0
            or pixel_coordinates is None
        ):
            raise ValueError(
                "internal_standard normalization needs the internal standard columns and pixel coordinates."
            )
        divisor = np.asarray(
            raw[:, standard_columns].sum(axis=1), dtype=np.float64
        ).ravel()
        missing = divisor <= 0
        if missing.all():
            raise ValueError(
                "The internal standard is absent from every spectrum of this sample."
            )
        if missing.any():
            # Spectra without the standard take the 3 x 3 neighbourhood median, then the sample median.
            neighbourhood = neighbourhood_nanmedian(
                np.where(missing, np.nan, divisor), pixel_coordinates
            )
            divisor[missing] = neighbourhood[missing]
            still_missing = ~np.isfinite(divisor)
            divisor[still_missing] = np.median(divisor[~missing])
            get_reporter().detail(
                f"Internal standard absent from {int(missing.sum()):,} spectra: "
                f"{int((missing & ~still_missing).sum()):,} filled from neighbours, "
                f"{int(still_missing.sum()):,} from the sample median"
            )
        return divisor
    else:
        raise ValueError(
            f"Invalid intensity normalization method '{method}'. Expected one of {MsiIntensityNormalization.list()}."
        )
    return np.where(np.isfinite(divisor) & (divisor > 0), divisor, 1.0)


@overload
def normalize_intensities(
    raw: np.ndarray,
    method: str,
    pixel_coordinates: np.ndarray | None = None,
    standard_columns: np.ndarray | None = None,
) -> np.ndarray: ...


@overload
def normalize_intensities(
    raw: sp.csr_matrix,
    method: str,
    pixel_coordinates: np.ndarray | None = None,
    standard_columns: np.ndarray | None = None,
) -> sp.csr_matrix: ...


def normalize_intensities(
    raw: np.ndarray | sp.csr_matrix,
    method: str,
    pixel_coordinates: np.ndarray | None = None,
    standard_columns: np.ndarray | None = None,
) -> np.ndarray | sp.csr_matrix:
    """
    Normalize the binned intensities of one sample and ion mode (rows = spectra).

    Every method divides each spectrum by one divisor, at a fixed scale, so samples share it without
    a pass over the whole dataset:

    - 'tic': total ion current over the features divided by their number (a row sums to the number
      of features, its mean feature is 1).
    - 'median': median of the nonzero features (a typical peak is 1).
    - 'rms': root mean square over the features.
    - 'internal_standard': summed intensity of the internal standard columns; spectra without it take
      the 3 x 3 neighbourhood median of the divisors, then the sample median.

    Parameters
    ----------
    raw : np.ndarray | scipy.sparse.csr_matrix
            (n_spectra, n_features) binned intensities; not modified.
    method : str
            One of MsiIntensityNormalization.list().
    pixel_coordinates : np.ndarray | None
            (n_spectra, 2) raster coordinates; required by 'internal_standard' to fill empty divisors.
    standard_columns : np.ndarray | None
            Feature columns of the internal standards; required by 'internal_standard'.

    Returns the type and dtype of `raw`.
    """
    if (
        method == MsiIntensityNormalization.NONE
        or raw.shape[0] == 0
        or raw.shape[1] == 0
    ):
        if method not in MsiIntensityNormalization.list():
            raise ValueError(
                f"Invalid intensity normalization method '{method}'. Expected one of {MsiIntensityNormalization.list()}."
            )
        return raw
    divisor = _divisor(raw, method, pixel_coordinates, standard_columns)
    if sp.issparse(raw):
        out = sp.csr_matrix(raw, copy=True)
        out.data = (
            out.data / np.repeat(divisor, np.diff(out.indptr)).astype(out.dtype)
        ).astype(out.dtype, copy=False)
        return out
    return cast(
        np.ndarray,
        (raw / divisor[:, None].astype(raw.dtype)).astype(raw.dtype, copy=False),
    )
