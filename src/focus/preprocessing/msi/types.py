"""Decoded imzML metadata shapes; units and array dtypes remain unchanged."""

from numpy.typing import NDArray

from typing import TypedDict

import numpy as np


class RecalibrationSummary(TypedDict, total=False):
    """Existing per-sample mass recalibration diagnostics."""

    lines: int
    lines_estimated: int
    lines_with_slope: int
    line_offset_ppm: tuple[float, float]
    pixel_residual_ppm: float
    pixels_matched: float
    pixels_filled: float


class BinaryRange(TypedDict):
    """Location and size of one spectrum array in an IBD file."""

    length: int
    encoded_length: int
    offset: int


class SpectrumRecord(TypedDict):
    """Spectrum metadata before construction of the sample arrays."""

    pixel_x: int | None
    pixel_y: int | None
    mzs: BinaryRange | None
    intensities: BinaryRange | None
    physical_x: float
    physical_y: float
    has_physical_coordinates: bool


class SampleMetadata(TypedDict, total=False):
    """Sample arrays keyed exactly as in the original imzML reader."""

    intensities_dtype: type[np.generic]
    mz_dtype: type[np.floating]
    raster_size: NDArray[np.float32]
    line_axis: int
    stage_coordinates: bool
    pixel_coordinates: NDArray[np.int32]
    physical_coordinates: NDArray[np.float32 | np.float64]
    raster_coordinates: NDArray[np.int32]
    mz_binary_metadata: NDArray[np.int64]
    intensities_binary_metadata: NDArray[np.int64]
