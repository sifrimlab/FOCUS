"""Read MSI spectra, select foreground, and apply per-mode recalibration."""

from __future__ import annotations
from numpy.typing import DTypeLike
from typing import TYPE_CHECKING
import numpy as np
import pandas as pd
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
    """Read m/z arrays before intensities, retaining polarity and spectrum order."""
    mz_vectors: Spectra = {}
    intensity_vectors: Spectra = {}
    for mode in sample._metadata.keys():
        metadata = sample._metadata[mode]
        mz_vectors[mode] = read_ranges(
            sample._binary_files[mode],
            metadata[MsiMetadata.MZ_DTYPE],
            metadata[MsiMetadata.MZ_BINARY_METADATA],
        )
        intensity_vectors[mode] = read_ranges(
            sample._binary_files[mode],
            metadata[MsiMetadata.INTENSITIES_DTYPE],
            metadata[MsiMetadata.INTENSITIES_BINARY_METADATA],
        )
    return mz_vectors, intensity_vectors


def select_foreground(
    sample: MsiSample,
    mz_vectors: Spectra,
    intensity_vectors: Spectra,
    annotation_db: pd.DataFrame | None,
    mass_tolerance: int | None,
    detect_background: bool,
) -> None:
    """Set shared foreground indices once; retain their original sorted ordering."""
    if annotation_db is not None:
        if sample.filtered_idx is None:
            keep_indices: set[int] = set()

            for mode in sample._metadata.keys():
                if detect_background:
                    keep_indices.update(
                        sample.foreground._detect_tissue_spots(
                            mz_vectors=mz_vectors[mode],
                            intensity_vectors=intensity_vectors[mode],
                            ion_mode=mode,
                            database=annotation_db,
                            mass_tolerance=mass_tolerance,
                        )
                    )
                else:
                    # Keep all the datapoints
                    keep_indices.update(range(len(mz_vectors[mode])))

            # Save the filtered indices
            sample.filtered_idx = sorted(list(keep_indices))


def recalibrate_vectors(
    sample: MsiSample,
    mz_vectors: Spectra,
    intensity_vectors: Spectra,
    mass_tolerance: int | None,
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


def filter_vectors(
    sample: MsiSample, mz_vectors: Spectra, intensity_vectors: Spectra
) -> tuple[Spectra, Spectra]:
    """Select paired spectrum lists using the established shared foreground indices."""
    filtered_mz: Spectra = {}
    filtered_intensity: Spectra = {}
    for mode in sample._metadata.keys():
        indices = (
            sample.filtered_idx
            if sample.filtered_idx is not None
            else range(len(mz_vectors[mode]))
        )
        filtered_mz[mode] = [mz_vectors[mode][i] for i in indices]
        filtered_intensity[mode] = [intensity_vectors[mode][i] for i in indices]
    return filtered_mz, filtered_intensity


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
