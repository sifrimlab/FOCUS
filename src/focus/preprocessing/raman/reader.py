"""Read tiled Leica scans, remove rescanned spectral overlaps and populate sample arrays."""

from __future__ import annotations
from focus.preprocessing.raman.lif_metadata import parse_lif_metadata
from typing import cast
from focus.preprocessing.raman.metadata import RamanMetadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage

from focus.runtime.reporter_context import get_reporter
import numpy as np
from readlif.reader import LifFile, LifImage


class RamanReader:
    """Decode tiled spectral scans and retain their shared acquisition state on the sample."""

    def __init__(self, sample: RamanImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    # --- Data loading ---

    def _load_lif(self, filename: str) -> None:
        """Load Raman data from a Leica LIF file."""
        lif_file = LifFile(filename)
        metadata_dict: dict[str, RamanMetadata] = parse_lif_metadata(lif_file)

        reference_metadata = RamanMetadata()
        reference_metadata.name = "reference"
        reference_metadata.index = 0

        raw_tiles: dict[str, np.ndarray] = {}
        wavenumbers: dict[str, np.ndarray] = {}
        coordinates: dict[str, np.ndarray] = {}
        pixel_size: dict[str, np.ndarray] = {}

        for name, metadata in metadata_dict.items():
            # Extract only tiled images, ignore automatic stitching
            if metadata.tile_number is None or metadata.tile_number < 2:
                continue

            if None in [
                metadata.tile_number,
                metadata.lambda_steps,
                metadata.scan_width,
                metadata.scan_height,
            ]:
                get_reporter().warning(
                    f"Image '{name}' is missing required metadata (probably a corrupted scan)"
                )
                continue

            self._append_scan(
                lif_file,
                name,
                metadata,
                raw_tiles,
                wavenumbers,
                coordinates,
                pixel_size,
            )

        self._store_scans(
            raw_tiles, wavenumbers, coordinates, pixel_size, reference_metadata
        )

    def _store_scans(
        self,
        raw_tiles: dict[str, np.ndarray],
        wavenumbers: dict[str, np.ndarray],
        coordinates: dict[str, np.ndarray],
        pixel_size: dict[str, np.ndarray],
        reference_metadata: RamanMetadata,
    ) -> None:
        """Merge scans in acquisition order, remove overlaps and store normalized float32 sample state."""
        # Merge all scans into single arrays
        stacked_tiles = np.concatenate([raw_tiles[name] for name in raw_tiles], axis=1)
        stacked_wavenumbers = np.concatenate(
            [wavenumbers[name] for name in wavenumbers], axis=-1
        )
        coords_array = np.stack([coordinates[name] for name in coordinates], axis=1)
        pixel_size_array = np.stack(
            [pixel_size[name] for name in pixel_size], axis=0
        ).mean(axis=0)

        stacked_tiles, stacked_wavenumbers = self._remove_overlap(
            stacked_tiles, stacked_wavenumbers
        )

        # Normalize to [0, 1] float32
        max_val = stacked_tiles.max()
        if max_val > 1.0 and max_val <= 255.0:
            stacked_tiles /= np.float32(255.0)
        elif max_val > 1.0 and max_val <= 65535.0:
            stacked_tiles /= np.float32(65535.0)
        elif max_val > 65535.0:
            raise ValueError("Expected input data in range [0, 255] or [0, 65535].")

        # Store results
        reference_metadata.scan_height = stacked_tiles.shape[-2]
        reference_metadata.scan_width = stacked_tiles.shape[-1]
        reference_metadata.tile_number = stacked_tiles.shape[0]
        reference_metadata.lambda_steps = stacked_tiles.shape[1]
        reference_metadata.tiles_coordinates = coords_array[0]
        reference_metadata.pixel_size = pixel_size_array

        self.sample._raw_tiles = stacked_tiles
        self.sample._wavenumbers = stacked_wavenumbers.astype(np.float32)
        self.sample._metadata = reference_metadata
        self.sample._tiles_coordinates = coords_array

    def _find_slice_index(self, channel_idx: int) -> int:
        """Find which spectra slice a channel index belongs to."""
        for i, (start, end) in enumerate(self.sample._spectra_slices):
            if start <= channel_idx <= end:
                return i
        return len(self.sample._spectra_slices) - 1

    def _check_wavenumbers_overlaps(
        self, wavenumbers: np.ndarray
    ) -> tuple[int | None, int | None]:
        """Locate a scanned spectral overlap, or return two None values."""
        if len(wavenumbers) < 2:
            return None, None

        is_asc = np.all(wavenumbers[:-1] <= wavenumbers[1:])
        is_desc = np.all(wavenumbers[:-1] >= wavenumbers[1:])
        if is_asc or is_desc:
            return None, None

        expected_asc = wavenumbers[1] >= wavenumbers[0]

        break_idx = None
        for i in range(1, len(wavenumbers)):
            if expected_asc and wavenumbers[i] < wavenumbers[i - 1]:
                break_idx = i
                break
            elif not expected_asc and wavenumbers[i] > wavenumbers[i - 1]:
                break_idx = i
                break

        if break_idx is None:
            return None, None

        target_val = wavenumbers[break_idx]
        left_slice = wavenumbers[:break_idx]
        closest_idx = int(np.argmin(np.abs(left_slice - target_val)))

        return break_idx, closest_idx

    @staticmethod
    def _compute_wavenumbers(
        lambda_begin: float, lambda_end: float, lambda_steps: int, lambda_stokes: float
    ) -> np.ndarray:
        """Compute Raman wavenumber array from wavelength metadata."""
        pump_wavelength = np.linspace(
            lambda_begin, lambda_end, lambda_steps, dtype=np.float32
        )
        raman_wavenumbers = ((1.0 / pump_wavelength) - (1.0 / lambda_stokes)) * 1e7
        return raman_wavenumbers

    def _append_scan(
        self,
        lif_file: LifFile,
        name: str,
        metadata: RamanMetadata,
        raw_tiles: dict[str, np.ndarray],
        wavenumbers: dict[str, np.ndarray],
        coordinates: dict[str, np.ndarray],
        pixel_size: dict[str, np.ndarray],
    ) -> None:
        """Read one tiled scan, append its spectral boundaries, then validate its physical metadata."""
        raw_tiles[name] = self._read_scan(lif_file, metadata)

        # Record spectra slice boundaries
        if len(self.sample._spectra_slices) == 0:
            self.sample._spectra_slices.append(
                (0, cast(int, metadata.lambda_steps) - 1)
            )
        else:
            prev_end = self.sample._spectra_slices[-1][1]
            self.sample._spectra_slices.append(
                (prev_end + 1, prev_end + cast(int, metadata.lambda_steps))
            )

        wavenumbers[name] = self._compute_wavenumbers(
            cast(float, metadata.lambda_begin),
            cast(float, metadata.lambda_end),
            cast(int, metadata.lambda_steps),
            cast(float, metadata.lambda_stokes),
        )

        if metadata.tiles_coordinates is not None:
            coordinates[name] = metadata.tiles_coordinates
        else:
            raise ValueError(f"Tiles coordinates not found for image '{name}'.")

        if metadata.pixel_size is not None and np.all(metadata.pixel_size > 0):
            pixel_size[name] = metadata.pixel_size
        else:
            raise ValueError(f"Pixel size not found or invalid for image '{name}'.")

    @staticmethod
    def _read_scan(lif_file: LifFile, metadata: RamanMetadata) -> np.ndarray:
        """Load a scan's planes as (tiles, channels, width, height) float32 in acquisition order."""
        image: LifImage = lif_file.get_image(metadata.index)
        if image is None:
            raise ValueError(
                f"Image with index {metadata.index} not found in the LIF file."
            )

        # Allocate as float32 (needed for BaSiC correction downstream)
        tiles = np.zeros(
            (
                cast(int, metadata.tile_number),
                cast(int, metadata.lambda_steps),
                cast(int, metadata.scan_width),
                cast(int, metadata.scan_height),
            ),
            dtype=np.float32,
        )

        for tile_idx in range(cast(int, metadata.tile_number)):
            for spectral_idx in range(cast(int, metadata.lambda_steps)):
                plane = image.get_plane(
                    display_dims=(1, 2),
                    c=0,
                    requested_dims={9: spectral_idx, 10: tile_idx},
                )
                tiles[tile_idx, spectral_idx, :, :] = plane

        return tiles

    def _remove_overlap(
        self,
        stacked_tiles: np.ndarray,
        stacked_wavenumbers: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Remove a rescanned interval and update inclusive slice indices; return the paired arrays."""
        # Handle wavenumber overlaps (re-scanned regions)
        break_idx, closest_idx = self._check_wavenumbers_overlaps(stacked_wavenumbers)

        if break_idx is not None and closest_idx is not None:
            get_reporter().detail(
                f"Overlapping wavenumbers at index {break_idx}: overlapping region removed"
            )
            stacked_wavenumbers = np.concatenate(
                [stacked_wavenumbers[:closest_idx], stacked_wavenumbers[break_idx:]]
            )
            stacked_tiles = np.concatenate(
                [
                    stacked_tiles[:, :closest_idx, :, :],
                    stacked_tiles[:, break_idx:, :, :],
                ],
                axis=1,
            )

            self._update_slices(closest_idx, break_idx)

        return stacked_tiles, stacked_wavenumbers

    def _update_slices(self, closest_idx: int, break_idx: int) -> None:
        """Shift scan boundaries after overlap removal, retaining the original lookup order."""
        # Update spectra slice indices
        new_slices = []
        shift = break_idx - closest_idx
        for index, s in enumerate(self.sample._spectra_slices):
            if index < self._find_slice_index(closest_idx):
                new_slices.append(s)
            elif index == self._find_slice_index(closest_idx):
                new_slices.append((s[0], int(closest_idx - 1)))
            elif index == self._find_slice_index(break_idx):
                new_slices.append((int(closest_idx), int(s[1] - shift)))
            else:
                new_slices.append((int(s[0] - shift), int(s[1] - shift)))
        self.sample._spectra_slices = new_slices
