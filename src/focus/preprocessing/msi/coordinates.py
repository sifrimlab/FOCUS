"""Pair MSI acquisition spots and transform their physical and raster coordinates."""

from __future__ import annotations
from typing import cast
from numpy.typing import NDArray
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
from focus.runtime.reporter_context import get_reporter
from sklearn.linear_model import LinearRegression

from focus.config.keys import MsiMetadata
from focus.config.options import MsiIonMode
from focus.preprocessing.msi.pairing import (
    PolarityPairingError,
    Registration,
    describe,
    pair,
    register,
)
from focus.preprocessing.msi.pairing_signal import lattice_steps


class MsiCoordinates:
    """Reconcile the sample's acquisition geometry and pair positive/negative spectra."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def _correct_rotation_error(
        self, physical_coords: NDArray[np.float32], pixel_coords: NDArray[np.int32]
    ) -> NDArray[np.float32]:
        """
        Compute and correct for rotation error between physical coordinates and pixel coordinates.

        Parameters
        ----------
        physical_coords : np.ndarray
                Array of shape (N, 2) with physical coordinates (x, y).
        pixel_coords : np.ndarray
                Array of shape (N, 2) with pixel coordinates (x, y).

        Returns
        -------
        rotated_physical : np.ndarray
                Array of shape (N, 2) with rotated physical coordinates.
        """

        # Select the X coordinate from the pixel set based on the line with the most points
        x_coords, count = np.unique(pixel_coords[:, 0], return_counts=True)
        fixed_pixel_x = x_coords[np.argmax(count)]

        # Select points corresponding to this fixed pixel X
        mask = pixel_coords[:, 0] == fixed_pixel_x
        points_line = physical_coords[mask]

        # Fit a line y = ax + b
        model = LinearRegression().fit(
            points_line[:, 0].reshape(-1, 1), points_line[:, 1]
        )
        slope = model.coef_[0]
        angle_rad = np.arctan(slope)  # angle of line from horizontal

        # Define helper function (for code clarity)
        def rotate(points: np.ndarray, angle: float, center: np.ndarray) -> np.ndarray:
            """Rotate physical coordinates around the supplied center without modifying the input."""
            R = np.array(
                [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
            )
            shifted = points - center
            rotated = shifted.dot(R.T) + center
            return cast(np.ndarray, rotated)

        # Rotate all physical points by -angle_rad around centroid
        centroid = physical_coords.mean(axis=0)
        rotated_physical = rotate(physical_coords, -angle_rad, centroid)
        return rotated_physical

    def _register(self) -> tuple[Registration, NDArray[np.float64]]:
        """Register the two polarities on their spectral content; the pixel pitch of the pairing.

        The pairing bands are consumed. Raises PolarityPairingError, naming the sample, when the pose
        cannot be verified.
        """
        positive = self.sample._metadata[MsiIonMode.POSITIVE]
        negative = self.sample._metadata[MsiIonMode.NEGATIVE]
        steps = np.maximum(
            lattice_steps(
                positive[MsiMetadata.PIXEL_COORDINATES],
                positive[MsiMetadata.PHYSICAL_COORDINATES],
            ),
            lattice_steps(
                negative[MsiMetadata.PIXEL_COORDINATES],
                negative[MsiMetadata.PHYSICAL_COORDINATES],
            ),
        )
        try:
            registration = register(
                positive[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64),
                negative[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64),
                positive.pop(MsiMetadata.PAIRING_BANDS),
                negative.pop(MsiMetadata.PAIRING_BANDS),
                steps,
                allow_mirror=not self._stage_positions(),
            )
        except PolarityPairingError as error:
            raise PolarityPairingError(
                f"Sample '{self.sample.sample_id}': {error}. Check that both imzML files come from "
                f"the same section, or process a single polarity."
            ) from None
        return registration, steps

    def _stage_positions(self) -> bool:
        """True when every spectrum of both polarities carries its stage position."""
        return all(
            self.sample._metadata[mode].get(MsiMetadata.STAGE_COORDINATES)
            for mode in (MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE)
        )

    def _filter_unpaired_spots(self) -> None:
        """
        Pair the spectra of the two ion modes and drop the unpaired ones (spots acquired in one mode only).

        The POSITIVE positions are registered onto the NEGATIVE ones from the spectral content of both
        acquisitions (:func:`focus.preprocessing.msi.pairing.register`): rotation, translation and,
        without stage positions, the handedness of the pixel axes. Already paired acquisitions,
        shifted or rotated stage frames and partially overlapping scans are handled alike. A pose that
        cannot be verified stops the sample with PolarityPairingError. Each mode keeps its own pixel
        coordinates, which only describe that mode's own raster (scan lines and pixel neighbourhoods).
        """
        if not self.sample.double_ion_mode:
            return
        positive = self.sample._metadata[MsiIonMode.POSITIVE]
        negative = self.sample._metadata[MsiIonMode.NEGATIVE]
        registration, steps = self._register()
        pos_xy = positive[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64)
        neg_xy = negative[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64)
        pos_indices, neg_indices, residual = pair(
            pos_xy, neg_xy, registration.pose, steps
        )
        if len(pos_indices) == 0:
            raise PolarityPairingError(
                f"Sample '{self.sample.sample_id}': no POSITIVE spectrum has a NEGATIVE counterpart."
            )
        reporter = get_reporter()
        reporter.detail(
            f"POSITIVE and NEGATIVE registered on spectral content: {describe(registration)}; "
            f"{len(pos_indices):,} pairs, median residual {np.median(residual):.1f} um; unpaired: "
            f"{len(pos_xy) - len(pos_indices):,} POSITIVE, {len(neg_xy) - len(neg_indices):,} NEGATIVE"
        )
        moved = float(
            np.median(np.linalg.norm(registration.pose.apply(pos_xy) - pos_xy, axis=1))
        )
        if self._stage_positions() and moved > steps.max():
            reporter.detail(
                f"The recorded stage positions of the two polarities differ by {moved:,.0f} um from "
                f"the registered pose; the stage frames are not shared (slide reloaded?)"
            )
        for metadata, indices in ((positive, pos_indices), (negative, neg_indices)):
            for key in (
                MsiMetadata.PIXEL_COORDINATES,
                MsiMetadata.PHYSICAL_COORDINATES,
                MsiMetadata.MZ_BINARY_METADATA,
                MsiMetadata.INTENSITIES_BINARY_METADATA,
            ):
                metadata[key] = metadata[key][indices]


def compute_raster_coordinates(
    physical_coords: np.ndarray, raster_size: NDArray[np.floating]
) -> NDArray[np.int32]:
    """
    Convert physical coordinates (in microns) to raster pixel coordinates. This method assumes a raster pixel size of 1 micron.

    Parameters
    ----------
    physical_coords : np.ndarray
            An (N, 2) array of physical coordinates in microns.
    raster_size : np.ndarray[np.floating]
            A (2,) array with the pixel size (x, y) in microns.

    Returns
    -------
    np.ndarray[np.int32]
            An (N, 2, 2) array representing, for each physical point, the top-left and bottom-right pixel coordinates of the corresponding rectangle in the raster.
            All the raster pixels covered by the rectangle refers to the same physical point.
    """

    # Find which block each coordinate belongs to
    # tile_x, tile_y are the block indices
    tile_x = np.floor_divide(physical_coords[:, 0], raster_size[0]).astype(int)
    tile_y = np.floor_divide(physical_coords[:, 1], raster_size[1]).astype(int)

    # Top-left coordinate of the block in raster
    x1 = tile_x * raster_size[0]
    y1 = tile_y * raster_size[1]

    # Bottom-right coordinate
    x2 = x1 + raster_size[0]
    y2 = y1 + raster_size[1]

    # Output shape: (N,2,2): [[x1,y1],[x2,y2]] for each coord
    pixel_coords = np.zeros((physical_coords.shape[0], 2, 2), dtype=np.int32)
    pixel_coords[:, 0, :] = np.stack([x1, y1], axis=1)  # top-left
    pixel_coords[:, 1, :] = np.stack([x2, y2], axis=1)  # bottom-right

    return pixel_coords
