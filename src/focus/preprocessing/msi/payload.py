"""Read MSI spectra and apply per-mode recalibration."""

from __future__ import annotations
from numpy.typing import DTypeLike
from typing import TYPE_CHECKING
import numpy as np
from focus.config.keys import MsiMetadata
from focus.preprocessing.msi.recalibration import (
    describe_recalibration,
    estimate_recalibration,
)
from focus.preprocessing.msi.settings import _RECAL_WARN_MATCHED
from focus.runtime.reporter_context import get_reporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

Spectra = dict[str, list[np.ndarray]]


def read_vectors(sample: MsiSample) -> tuple[Spectra, Spectra]:
    """
    Read m/z arrays before intensities, retaining polarity and spectrum order.

    Integer intensity arrays are cast to float32. Peaks with m/z at or below 0 are dropped: the
    logarithmic m/z grid and the ppm tolerance are undefined there.
    """
    mz_vectors: Spectra = {}
    intensity_vectors: Spectra = {}
    for mode in sample._metadata.keys():
        metadata = sample._metadata[mode]
        mz_list = read_ranges(
            sample._binary_files[mode],
            metadata[MsiMetadata.MZ_DTYPE],
            metadata[MsiMetadata.MZ_BINARY_METADATA],
        )
        intensity_list = read_ranges(
            sample._binary_files[mode],
            metadata[MsiMetadata.INTENSITIES_DTYPE],
            metadata[MsiMetadata.INTENSITIES_BINARY_METADATA],
        )
        if not np.issubdtype(metadata[MsiMetadata.INTENSITIES_DTYPE], np.floating):
            intensity_list = [values.astype(np.float32) for values in intensity_list]
        for k, mz in enumerate(mz_list):
            if mz.size and (mz <= 0).any():
                positive = mz > 0
                mz_list[k], intensity_list[k] = (
                    mz[positive],
                    intensity_list[k][positive],
                )
        mz_vectors[mode] = mz_list
        intensity_vectors[mode] = intensity_list
    return mz_vectors, intensity_vectors


def recalibrate_vectors(
    sample: MsiSample,
    mz_vectors: Spectra,
    intensity_vectors: Spectra,
    mass_tolerance: float | None,
) -> None:
    """Reuse per-polarity models and replace m/z vectors with their corrected copies."""
    if sample.recalibration_reference is not None and mass_tolerance is not None:
        for mode in sample._metadata.keys():
            reference = sample.recalibration_reference.get(mode)
            if reference is None or reference.size == 0:
                continue
            if mode not in sample.recalibration_model:
                sample.recalibration_model[mode] = estimate_recalibration(
                    mz_list=mz_vectors[mode],
                    intensity_list=intensity_vectors[mode],
                    pixel_coordinates=sample._metadata[mode][
                        MsiMetadata.PIXEL_COORDINATES
                    ],
                    line_axis=sample._metadata[mode].get(MsiMetadata.LINE_AXIS, 0),
                    reference_mz=reference,
                    mass_tolerance=mass_tolerance,
                )
                model = sample.recalibration_model[mode]
                message = f"Recalibration ({str(mode).upper()}): {describe_recalibration(model)}"
                if model.summary.get("pixels_matched", 0.0) < _RECAL_WARN_MATCHED:
                    get_reporter().warning(
                        message
                        + f"; fewer than {_RECAL_WARN_MATCHED:.0%} of the spectra "
                        f"matched a reference, so most of this sample is corrected "
                        f"from its neighbours or not at all"
                    )
                else:
                    get_reporter().detail(message)
            mz_vectors[mode] = sample.recalibration_model[mode].apply(mz_vectors[mode])


def read_ranges(path: str, dtype: DTypeLike, ranges: np.ndarray) -> list[np.ndarray]:
    """Read one polarity's spectrum arrays from their declared binary ranges.

    Parameters
    ----------
    path
        IBD binary input file, opened by NumPy for each spectrum.
    dtype
        Original floating-point dtype declared by imzML.
    ranges
        Integer ``(N, 3)`` metadata: value count, encoded bytes, and byte offset.

    Returns
    -------
    list of ndarray
        Independent variable-length arrays in the original dtype and spectrum order.
    """
    return [
        np.fromfile(path, dtype=dtype, count=ranges[i, 0], offset=ranges[i, 2])
        for i in range(ranges.shape[0])
    ]
