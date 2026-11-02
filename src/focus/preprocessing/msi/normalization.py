"""Apply the original per-sample, per-ion-mode binned-intensity normalizations."""

from typing import cast

import numpy as np
from focus.runtime.reporter_context import get_reporter
import warnings

from focus.config.options import MsiIntensityNormalization
from focus.preprocessing.msi.recalibration import neighbourhood_nanmedian


def normalize_intensities(
    raw: np.ndarray,
    method: str,
    pixel_coordinates: np.ndarray | None = None,
    standard_columns: np.ndarray | None = None,
) -> np.ndarray:
    """
    Normalize the binned intensities of one sample and ion mode (rows = spectra).

    Parameters
    ----------
    raw : np.ndarray
            (n_spectra, n_features) binned intensities; not modified.
    method : str
            One of MsiIntensityNormalization.list().
    pixel_coordinates : np.ndarray | None
            (n_spectra, 2) raster coordinates; required by 'internal_standard' to fill empty divisors.
    standard_columns : np.ndarray | None
            Feature columns of the internal standards; required by 'internal_standard'.

    Every method returns the dtype of `raw`: the per-spectrum divisors are cast to it, so a float32
    matrix is never promoted to a float64 copy.
    """
    if method == MsiIntensityNormalization.NONE or raw.size == 0:
        return raw
    if method == MsiIntensityNormalization.TIC:
        tic = raw.sum(axis=1, keepdims=True)
        tic[tic == 0] = 1
        return cast(np.ndarray, raw / tic)
    if method == MsiIntensityNormalization.LOG:
        return cast(np.ndarray, np.log1p(raw))
    if method == MsiIntensityNormalization.CLR:
        # Sparsity-preserving CLR: center the log-intensities over each row's nonzero (observed)
        # support only, leaving structural zeros at exactly 0 so the matrix stays sparse.
        nz = raw > 0
        log_x = np.log(raw, where=nz, out=np.zeros_like(raw))
        counts = nz.sum(axis=1, keepdims=True)
        counts[counts == 0] = 1
        row_mean = (log_x.sum(axis=1, keepdims=True) / counts).astype(
            raw.dtype, copy=False
        )
        return np.where(nz, log_x - row_mean, 0.0)
    if method == MsiIntensityNormalization.TIC_MEAN_SCALED:
        # Rescale every spectrum so its total ion current equals the mean TIC over this sample's
        # spots (this ion mode): TIC normalization multiplied by a per-sample constant. The mean is
        # taken within this sample and ion mode only, so it does not harmonise samples.
        tic = raw.sum(axis=1, keepdims=True)
        mean_tic = tic.mean()
        if mean_tic == 0:
            mean_tic = 1.0
        scaling = tic / mean_tic
        scaling[scaling == 0] = 1.0  # empty spots stay all-zero
        return cast(np.ndarray, raw / scaling)
    if method == MsiIntensityNormalization.MEDIAN:
        # Median over each spectrum's nonzero features: binned centroid spectra are sparse, so the
        # median over all features would be 0. Spectra without signal use divisor 1.
        median = np.ones((raw.shape[0], 1), dtype=raw.dtype)
        for start in range(0, raw.shape[0], 10_000):
            block = raw[start : start + 10_000]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-zero spectra
                block_median = np.nanmedian(np.where(block > 0, block, np.nan), axis=1)
            median[start : start + block.shape[0], 0] = np.where(
                np.isfinite(block_median), block_median, 1.0
            )
        return raw / median
    if method == MsiIntensityNormalization.INTERNAL_STANDARD:
        if (
            standard_columns is None
            or len(standard_columns) == 0
            or pixel_coordinates is None
        ):
            raise ValueError(
                "internal_standard normalization needs the internal standard columns and pixel coordinates."
            )
        divisor = raw[:, standard_columns].sum(axis=1).astype(np.float64)
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
        return cast(np.ndarray, raw / divisor[:, None].astype(raw.dtype))
    raise ValueError(
        f"Invalid intensity normalization method '{method}'. Expected one of {MsiIntensityNormalization.list()}."
    )
