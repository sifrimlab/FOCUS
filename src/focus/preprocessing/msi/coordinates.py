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
from scipy.spatial import cKDTree

from focus.config.keys import MsiMetadata
from focus.config.options import MsiIonMode


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

    @staticmethod
    def _pair_by_stage(
        pos_stage: np.ndarray, neg_stage: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Pair the POSITIVE and NEGATIVE spectra of one sample by stage position.

        Both rasters are in the instrument's stage frame and differ by one constant offset: the second
        polarity is acquired a fraction of a pixel away from the first, and the two acquisitions may
        cover different areas. The offset is the most common displacement from a POSITIVE spectrum to
        its nearest NEGATIVE one, taken on a grid of a quarter pitch (so that two equally near
        neighbours, at an offset of half a pitch, are not averaged into a displacement of 0), refined by
        the median of the displacements in that grid cell. Each POSITIVE spectrum is then paired with
        the NEGATIVE spectrum nearest to its position plus the offset, within half a pitch; a NEGATIVE
        spectrum claimed twice goes to the closer POSITIVE one.

        Returns
        -------
        pos_indices, neg_indices : np.ndarray
                Paired spectrum indices, in ascending POSITIVE order.
        offset : np.ndarray
                NEGATIVE minus POSITIVE stage position, in the stage unit (micrometres).
        residual : np.ndarray
                Distance of every pair from the offset, in the stage unit.
        """
        spacing = cast(
            tuple[np.ndarray, np.ndarray], cKDTree(pos_stage).query(pos_stage, k=2)
        )[0][:, 1]
        pitch = float(np.median(spacing[spacing > 0])) if np.any(spacing > 0) else 1.0
        tree = cKDTree(neg_stage)
        _, nearest = tree.query(pos_stage)
        displacement = neg_stage[nearest] - pos_stage
        _, cell, counts = np.unique(
            np.round(displacement / (pitch / 4)).astype(np.int64),
            axis=0,
            return_inverse=True,
            return_counts=True,
        )
        offset = np.median(displacement[cell.ravel() == np.argmax(counts)], axis=0)

        distance, partner = cast(
            tuple[np.ndarray, np.ndarray],
            tree.query(pos_stage + offset, distance_upper_bound=pitch / 2),
        )
        order = np.argsort(distance, kind="stable")
        order = order[np.isfinite(distance[order])]
        _, first = np.unique(partner[order], return_index=True)
        pos_indices = np.sort(order[first])
        return pos_indices, partner[pos_indices], offset, distance[pos_indices]

    def _pair_by_pixel(self) -> tuple[np.ndarray, np.ndarray]:
        """
        Pair the POSITIVE and NEGATIVE spectra by pixel index, for imzML files without stage positions.

        The NEGATIVE pixel axes are flipped to the POSITIVE orientation first (each mode's
        pixel-to-physical map is fitted and the flip that best matches the two maps is applied),
        mirroring the NEGATIVE pixel indices about their own range. That mirror maps each pixel onto
        its POSITIVE counterpart only when both modes cover the same raster extent, so a difference in
        extent is reported as a warning.

        Returns
        -------
        pos_indices, neg_indices : np.ndarray
                Paired spectrum indices, in ascending POSITIVE order.
        """
        pos_pixel_coords = self.sample._metadata[MsiIonMode.POSITIVE][
            MsiMetadata.PIXEL_COORDINATES
        ]
        neg_pixel_coords = self.sample._metadata[MsiIonMode.NEGATIVE][
            MsiMetadata.PIXEL_COORDINATES
        ]
        pos_phys_coords = self.sample._metadata[MsiIonMode.POSITIVE][
            MsiMetadata.PHYSICAL_COORDINATES
        ]
        neg_phys_coords = self.sample._metadata[MsiIonMode.NEGATIVE][
            MsiMetadata.PHYSICAL_COORDINATES
        ]

        # Detect pixel axis orientation mismatch by fitting a linear pixel->physical map for each mode.
        # T has shape (2, 2): row i contains how pixel axis i drives (phys_x, phys_y).
        # Flipping pixel axis i negates row i of T. We test all 4 flip combinations and pick the one
        # that makes the negative map best align (minimum Frobenius distance) with the positive map.
        def _fit_pixel_transform(pix: np.ndarray, phys: np.ndarray) -> np.ndarray:
            """Fit pixel-to-physical displacement axes by least squares."""
            A = np.column_stack([pix.astype(float), np.ones(len(pix))])
            return cast(
                np.ndarray, np.linalg.lstsq(A, phys.astype(float), rcond=None)[0][:2]
            )

        T_pos = _fit_pixel_transform(pos_pixel_coords, pos_phys_coords)
        T_neg = _fit_pixel_transform(neg_pixel_coords, neg_phys_coords)
        T_pos_norm = T_pos / np.linalg.norm(T_pos, "fro")
        T_neg_norm = T_neg / np.linalg.norm(T_neg, "fro")

        flip_candidates = {
            (False, False): np.array([1.0, 1.0]),
            (True, False): np.array([-1.0, 1.0]),
            (False, True): np.array([1.0, -1.0]),
            (True, True): np.array([-1.0, -1.0]),
        }
        flip_x, flip_y = min(
            flip_candidates,
            key=lambda k: np.linalg.norm(
                T_neg_norm * flip_candidates[k][:, None] - T_pos_norm, "fro"
            ),
        )

        if np.any(np.ptp(pos_pixel_coords, axis=0) != np.ptp(neg_pixel_coords, axis=0)):
            get_reporter().warning(
                f"POSITIVE and NEGATIVE cover different rasters ({np.ptp(pos_pixel_coords, axis=0) + 1} vs "
                f"{np.ptp(neg_pixel_coords, axis=0) + 1} pixels) and the imzML files have no stage positions: "
                f"spectra are paired by pixel index, which can pair different locations"
            )

        if flip_x or flip_y:
            neg_pixel_coords = neg_pixel_coords.copy()
            min_x, max_x = (
                int(neg_pixel_coords[:, 0].min()),
                int(neg_pixel_coords[:, 0].max()),
            )
            min_y, max_y = (
                int(neg_pixel_coords[:, 1].min()),
                int(neg_pixel_coords[:, 1].max()),
            )
            if flip_x:
                neg_pixel_coords[:, 0] = max_x + min_x - neg_pixel_coords[:, 0]
            if flip_y:
                neg_pixel_coords[:, 1] = max_y + min_y - neg_pixel_coords[:, 1]
            self.sample._metadata[MsiIonMode.NEGATIVE][
                MsiMetadata.PIXEL_COORDINATES
            ] = neg_pixel_coords
            get_reporter().detail(
                f"Pixel axis flip applied to the NEGATIVE mode (flip_x={flip_x}, flip_y={flip_y})"
            )

        # Keep the pixels present in both modes. The negative array is still in its original scan
        # order, which is the reverse of positive when a flip was applied, so the negative indices are
        # reordered: neg_pixel_coords[neg_indices[k]] == pos_pixel_coords[pos_indices[k]].
        neg_pix_to_idx = {
            (int(y), int(x)): ni for ni, (y, x) in enumerate(neg_pixel_coords)
        }
        pos_indices = np.array(
            [
                pi
                for pi, (y, x) in enumerate(pos_pixel_coords)
                if (int(y), int(x)) in neg_pix_to_idx
            ],
            dtype=np.intp,
        )
        neg_indices = np.array(
            [
                neg_pix_to_idx[(int(y), int(x))]
                for y, x in pos_pixel_coords[pos_indices]
            ],
            dtype=np.intp,
        )
        return pos_indices, neg_indices

    def _filter_unpaired_spots(self) -> None:
        """
        Pair the spectra of the two ion modes and drop the unpaired ones (spots acquired in one mode only).

        With stage positions in both imzML files the spectra are paired by stage position
        (:meth:`_pair_by_stage`), whatever area and pixel orientation each acquisition has; otherwise
        by pixel index (:meth:`_pair_by_pixel`). Each mode keeps its own pixel coordinates, which only
        describe that mode's own raster (scan lines and pixel neighbourhoods).
        """

        if not self.sample.double_ion_mode:
            return

        positive = self.sample._metadata[MsiIonMode.POSITIVE]
        negative = self.sample._metadata[MsiIonMode.NEGATIVE]
        if positive.get(MsiMetadata.STAGE_COORDINATES) and negative.get(
            MsiMetadata.STAGE_COORDINATES
        ):
            pos_indices, neg_indices, offset, residual = MsiCoordinates._pair_by_stage(
                positive[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64),
                negative[MsiMetadata.PHYSICAL_COORDINATES].astype(np.float64),
            )
            get_reporter().detail(
                f"POSITIVE and NEGATIVE paired by stage position: {len(pos_indices):,} pairs, NEGATIVE offset "
                f"({offset[0]:+.1f}, {offset[1]:+.1f}) um, median residual {np.median(residual):.1f} um; unpaired: "
                f"{len(positive[MsiMetadata.PIXEL_COORDINATES]) - len(pos_indices):,} POSITIVE, "
                f"{len(negative[MsiMetadata.PIXEL_COORDINATES]) - len(neg_indices):,} NEGATIVE"
            )
        else:
            pos_indices, neg_indices = self._pair_by_pixel()
        if len(pos_indices) == 0:
            raise ValueError(
                f"Sample '{self.sample.sample_id}': no POSITIVE spectrum has a NEGATIVE counterpart."
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
    physical_coords: np.ndarray, raster_size: NDArray[np.int16]
) -> NDArray[np.int32]:
    """
    Convert physical coordinates (in microns) to raster pixel coordinates. This method assumes a raster pixel size of 1 micron.

    Parameters
    ----------
    physical_coords : np.ndarray
            An (N, 2) array of physical coordinates in microns.
    raster_size : np.ndarray[np.int16]
            A (2,) array specifying the size of the raster in pixels (width, height).

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
