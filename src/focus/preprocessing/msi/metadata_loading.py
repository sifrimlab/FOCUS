"""Initialize MSI metadata, validate payloads, and reconcile acquisition coordinates."""

from __future__ import annotations
from typing import TYPE_CHECKING, cast
import os
import numpy as np
from sklearn.linear_model import LinearRegression
from focus.config.keys import MsiMetadata
from focus.config.options import MsiIonMode
from focus.io.discovery import find_imzml_pair
from focus.runtime.reporter_context import get_reporter
from focus.preprocessing.msi.binary import check_ibd_size
from focus.preprocessing.msi.centroiding import (
    CENTROID_INTENSITY_DTYPE,
    CENTROID_MZ_DTYPE,
    centroid_profile,
)
from focus.preprocessing.msi.coordinates import compute_raster_coordinates
from focus.preprocessing.msi.representation import (
    PROFILE,
    needs_centroiding,
    probe_representation,
)

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample


def initialize_sample(sample: MsiSample) -> None:
    """Load and validate each ion mode, then update shared physical/raster coordinates.

    Parameters
    ----------
    sample : MsiSample
        Mutable sample whose metadata and binary-file maps are populated in input order.

    Raises
    ------
    FileNotFoundError
        If an input directory has no complete imzML/IBD pair.
    ValueError
        If a binary payload fails validation.
    """
    for mode, input_path in sample.input_paths.items():
        _load_mode(sample, mode, input_path)
    _set_coordinates(sample, mode)


def _load_mode(sample: MsiSample, mode: str, input_path: str) -> None:
    """Populate one mode's metadata and remove spectra rejected by binary validation."""
    # Same resolver that decided which ion modes this sample has, so discovery and loading
    # can never disagree. It returns the real on-disk .ibd name, no extension guesswork.
    pair = find_imzml_pair(input_path)
    if pair is None:
        raise FileNotFoundError(
            f"No complete .imzML + .ibd file pair found in '{input_path}'."
        )
    sample._metadata_files[mode], sample._binary_files[mode] = pair

    # Parse the imzML file to extract the metadata and the parsed spectra
    sample._metadata[mode] = sample.reader._parse_imzml(sample._metadata_files[mode])

    # A sampled profile is peak-picked once into a scratch IBD, so that validation, pairing and every
    # later read see centroids. Spectra that cannot be picked join the corrupt ones below.
    unpicked = _centroid_profile(sample, mode)

    # Validate the binary payload before any downstream processing.
    # Raises ValueError on truncation; returns indices of NaN/Inf spectra.
    corrupt_indices = sample.binary._validate_binary_data(mode) | unpicked
    if corrupt_indices:
        _filter_corrupt_spectra(sample, mode, corrupt_indices)


def _centroid_profile(sample: MsiSample, mode: str) -> set[int]:
    """Replace a sampled profile by its centroids in a scratch IBD; return the spectra not picked.

    The content of the spectra decides, not the header (see representation.py). Centroid files are
    left as they are.
    """
    meta = sample._metadata[mode]
    path = sample._binary_files[mode]
    mz_meta = meta[MsiMetadata.MZ_BINARY_METADATA]
    int_meta = meta[MsiMetadata.INTENSITIES_BINARY_METADATA]
    mz_dtype = meta[MsiMetadata.MZ_DTYPE]
    int_dtype = meta[MsiMetadata.INTENSITIES_DTYPE]
    # Reading a truncated file through a memory map would fault, so the size is checked first.
    check_ibd_size(path, mz_meta, int_meta, mz_dtype, int_dtype, sample.sample_id, mode)
    representation = probe_representation(path, mz_dtype, int_dtype, mz_meta, int_meta)
    declared = meta.get(MsiMetadata.DECLARED_REPRESENTATION, "")
    name = os.path.basename(sample._metadata_files[mode])
    if not needs_centroiding(representation, declared, name):
        return set()
    assert representation.kind == PROFILE and representation.grid is not None

    scratch = os.path.join(sample.output_path, f".{sample.modality_name}_{mode}.ibd")
    sample._scratch_files.append(scratch)
    result = centroid_profile(
        path,
        mz_dtype,
        int_dtype,
        mz_meta,
        int_meta,
        representation.grid,
        scratch,
        f"Centroiding ({str(mode).upper()}, {sample.sample_id})",
    )
    sample._binary_files[mode] = scratch
    meta[MsiMetadata.MZ_BINARY_METADATA] = result.mz_meta
    meta[MsiMetadata.INTENSITIES_BINARY_METADATA] = result.intensity_meta
    meta[MsiMetadata.MZ_DTYPE] = CENTROID_MZ_DTYPE
    meta[MsiMetadata.INTENSITIES_DTYPE] = CENTROID_INTENSITY_DTYPE
    get_reporter().detail(
        f"Spectra ({str(mode).upper()}): sampled profile, "
        f"{representation.points_per_peak:.1f} points per peak; centroided to "
        f"{result.mean_peaks:,.0f} peaks per spectrum"
    )
    return result.corrupt


def _filter_corrupt_spectra(
    sample: MsiSample, mode: str, corrupt_indices: set[int]
) -> None:
    """Remove the same corrupt rows from all four metadata arrays and report the count."""
    n_total = sample._metadata[mode][MsiMetadata.MZ_BINARY_METADATA].shape[0]
    keep = np.array(
        [i for i in range(n_total) if i not in corrupt_indices],
        dtype=np.intp,
    )
    sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES] = sample._metadata[mode][
        MsiMetadata.PIXEL_COORDINATES
    ][keep]
    sample._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES] = sample._metadata[mode][
        MsiMetadata.PHYSICAL_COORDINATES
    ][keep]
    sample._metadata[mode][MsiMetadata.MZ_BINARY_METADATA] = sample._metadata[mode][
        MsiMetadata.MZ_BINARY_METADATA
    ][keep]
    sample._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA] = sample._metadata[
        mode
    ][MsiMetadata.INTENSITIES_BINARY_METADATA][keep]
    for key in (MsiMetadata.PAIRING_BANDS, MsiMetadata.STAGE_POSITIONS):
        if key in sample._metadata[mode]:
            sample._metadata[mode][key] = sample._metadata[mode][key][keep]
    get_reporter().warning(
        f"{str(mode).upper()} mode: filtered {len(corrupt_indices):,} corrupted datapoint(s) "
        f"({len(corrupt_indices) / n_total * 100:.1f}% of {n_total:,} spectra)"
    )


def _dual_coordinates(sample: MsiSample) -> np.ndarray:
    """Pair ion modes and return their affine-corrected physical midpoints, in µm."""
    # Filter out unpaired points (experimental artifacts)
    sample.coordinates._filter_unpaired_spots()

    # Compute the offset between the two physical coordinates sets
    pos_coords = sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES]
    neg_coords = sample._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES]

    # Use an affine transformation to account for the translation
    def affine_transform(points: np.ndarray) -> np.ndarray:
        """Apply the fitted physical-coordinate regression to all supplied points."""
        aug = np.hstack([points, np.ones((points.shape[0], 1))])
        x_new = model_x.predict(aug)
        y_new = model_y.predict(aug)
        return np.stack([x_new, y_new], axis=1)

    A = np.hstack([pos_coords, np.ones((pos_coords.shape[0], 1))])
    model_x = LinearRegression().fit(A, neg_coords[:, 0])
    model_y = LinearRegression().fit(A, neg_coords[:, 1])

    # Apply the transformation to the positive ion mode physical coordinates
    pos_coords_transformed = affine_transform(pos_coords)

    offset = _raster_offset(sample)

    # Apply the offset to the transformed coordinates and compute the average between pos and neg to have the coordinate of the raster's center
    pos_coords_transformed -= np.array(offset)
    final_physical_coords = np.mean(
        [pos_coords_transformed, neg_coords], axis=0
    )  # Midpoint between the two physical sets
    return cast(np.ndarray, final_physical_coords)


def _raster_offset(sample: MsiSample) -> tuple[float, float]:
    """Return the positive-mode half-raster shift along the acquisition's longer axes."""
    # Depending on the raster size, move the coordinates to the center
    if (
        sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0]
        == sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]
    ):
        # The raster is square, offset along both axes
        offset = (
            sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] / 2,
            sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] / 2,
        )
    elif (
        sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0]
        > sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]
    ):
        # The raster is rectangular, offset along the X axis
        offset = (
            sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] / 2,
            0,
        )
    else:
        # The raster is rectangular, offset along the Y axis
        offset = (
            0,
            sample._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] / 2,
        )

    return offset


def _set_coordinates(sample: MsiSample, mode: str) -> None:
    """Normalize shared coordinates in place using the last loaded mode's raster size."""
    if sample.double_ion_mode:
        final_physical_coords = _dual_coordinates(sample)
    else:
        final_physical_coords = sample._metadata[cast(str, sample.ion_mode)][
            MsiMetadata.PHYSICAL_COORDINATES
        ]

    # Normalize the physical coordinates to start from (0,0)
    final_physical_coords -= final_physical_coords.min(axis=0)

    # Shift them to be the center of the raster
    raster_offset = (
        sample._metadata[mode][MsiMetadata.RASTER_SIZE].astype(np.float64) / 2
    )
    final_physical_coords += raster_offset

    # Replace the physical coordinates in the metadata
    for mode in sample._metadata.keys():
        sample._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES] = final_physical_coords

        # Compute the raster pixel coordinates for each physical point
        sample._metadata[mode][MsiMetadata.RASTER_COORDINATES] = (
            compute_raster_coordinates(
                final_physical_coords, sample._metadata[mode][MsiMetadata.RASTER_SIZE]
            )
        )
