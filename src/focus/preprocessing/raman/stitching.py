"""Prepare Raman tile geometry and OME inputs, then coordinate ASHLAR mosaic output."""

from __future__ import annotations
from numpy.typing import NDArray
from focus.preprocessing.raman.tool_execution import stitch_tiles
from typing import cast
from focus.preprocessing.raman.metadata import RamanMetadata
from focus.preprocessing.raman.tools import logger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage

import tifffile
import os
from focus.runtime.reporter_context import get_reporter
import copy
import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt
from sklearn.decomposition import PCA

from focus.io.paths import preprocessed_file


class RamanStitcher:
    """Prepare physical tile placement, preview mosaics and external ASHLAR stitching."""

    def __init__(self, sample: RamanImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def _quick_stitch(self) -> None:
        """Stitch tiles into a quick mosaic (with blending) for background removal only."""
        if self.sample._basic_corrected_tiles is None:
            raise RuntimeError("No BaSiC corrected tiles to stitch.")
        tiles = self.sample._basic_corrected_tiles

        coords_px = np.zeros_like(
            cast(np.ndarray, self.sample._tiles_coordinates), dtype=np.int32
        )
        for slice_idx, _ in enumerate(self.sample._spectra_slices):
            coords_px[:, slice_idx, :] = (
                cast(np.ndarray, self.sample._tiles_coordinates)[:, slice_idx, :]
                / cast(
                    np.ndarray, cast(RamanMetadata, self.sample.metadata).pixel_size
                )[0]
            ).astype(np.int32)

        min_x, min_y = np.min(coords_px.reshape(-1, 2), axis=0)
        max_x = np.max(coords_px[:, :, 0] + tiles.shape[3])
        max_y = np.max(coords_px[:, :, 1] + tiles.shape[2])

        mosaic_width = max_x - min_x
        mosaic_height = max_y - min_y
        mosaic_shape = (tiles.shape[1], mosaic_height, mosaic_width)

        mosaic = np.zeros(mosaic_shape, dtype=np.float32)
        weights = np.zeros(mosaic_shape, dtype=np.float32)

        tile_weights = distance_transform_edt(
            np.ones((tiles.shape[2], tiles.shape[3]), dtype=np.float32)
        )
        tile_weights /= tile_weights.max()

        for t in range(tiles.shape[0]):
            tile_height, tile_width = tiles.shape[2], tiles.shape[3]

            for c in range(tiles.shape[1]):
                for slice_idx, (start_ch, end_ch) in enumerate(
                    self.sample._spectra_slices
                ):
                    if start_ch <= c <= end_ch:
                        x, y = coords_px[t, slice_idx, :] - [min_x, min_y]
                        break

                y_start, y_end = max(0, y), min(mosaic_height, y + tile_height)
                x_start, x_end = max(0, x), min(mosaic_width, x + tile_width)
                ty_start, ty_end = (
                    max(0, -y),
                    tile_height - max(0, y + tile_height - mosaic_height),
                )
                tx_start, tx_end = (
                    max(0, -x),
                    tile_width - max(0, x + tile_width - mosaic_width),
                )

                mosaic[c, y_start:y_end, x_start:x_end] += (
                    tiles[t, c, ty_start:ty_end, tx_start:tx_end]
                    * tile_weights[ty_start:ty_end, tx_start:tx_end]
                )
                weights[c, y_start:y_end, x_start:x_end] += tile_weights[
                    ty_start:ty_end, tx_start:tx_end
                ]

        mosaic = np.divide(
            mosaic, weights, out=np.zeros_like(mosaic), where=weights > 0
        )

        # PCA → grayscale → CLAHE for visualization
        pca = PCA(n_components=1)
        mosaic_hwc = mosaic.transpose((1, 2, 0))  # (H, W, C)
        H, W, C = mosaic_hwc.shape
        mosaic_flat = mosaic_hwc.reshape(-1, C)

        filter_mask = np.all(mosaic_flat == 0, axis=1)
        if np.all(filter_mask):
            raise RuntimeError("The mosaic is completely black.")

        mosaic_pca = pca.fit_transform(mosaic_flat[~filter_mask])

        p2, p98 = np.percentile(mosaic_pca, (2, 98))
        mosaic_pca_norm = np.clip((mosaic_pca - p2) / (p98 - p2 + 1e-9), 0, 1)

        result = np.zeros((H * W, 1), dtype=np.float32)
        result[~filter_mask] = mosaic_pca_norm
        mosaic_gray = result.reshape(H, W)

        mosaic_uint8 = (mosaic_gray * 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        # CLAHE preserves uint8 input; OpenCV's stub widens this return dtype.
        self.sample._quick_mosaic = cast(NDArray[np.uint8], clahe.apply(mosaic_uint8))

    def _extract_tiles_segmentation_from_mosaic(
        self,
        mosaic: np.ndarray,
        coordinates: np.ndarray,
        pixel_size: float,
        tile_size: tuple[int, int],
    ) -> np.ndarray:
        """Extract per-tile segmentation masks from the global mosaic mask."""
        tile_h, tile_w = tile_size
        n_tiles = coordinates.shape[0]
        n_channels = sum(e - s + 1 for s, e in self.sample._spectra_slices)

        coords_px = (coordinates / pixel_size).astype(np.int32)
        min_x = np.min(coords_px[:, :, 0])
        min_y = np.min(coords_px[:, :, 1])
        coords_px[:, :, 0] -= min_x
        coords_px[:, :, 1] -= min_y

        H, W = mosaic.shape
        tiles = np.zeros((n_tiles, n_channels, tile_h, tile_w), dtype=mosaic.dtype)

        channel_to_scan = {}
        for scan_idx, (start_ch, end_ch) in enumerate(self.sample._spectra_slices):
            for c in range(start_ch, end_ch + 1):
                channel_to_scan[c] = scan_idx

        for t in range(n_tiles):
            for c in range(n_channels):
                scan_idx = channel_to_scan[c]
                x = coords_px[t, scan_idx, 0]
                y = coords_px[t, scan_idx, 1]

                x_start, x_end = max(0, x), min(W, x + tile_w)
                y_start, y_end = max(0, y), min(H, y + tile_h)
                tx_start = x_start - x
                tx_end = tx_start + (x_end - x_start)
                ty_start = y_start - y
                ty_end = ty_start + (y_end - y_start)

                if (x_end > x_start) and (y_end > y_start):
                    tiles[t, c, ty_start:ty_end, tx_start:tx_end] = mosaic[
                        y_start:y_end, x_start:x_end
                    ]

        return tiles

    def _prepare_for_ashlar(
        self, tiles: np.ndarray, coordinates: np.ndarray
    ) -> list[int]:
        """Prepare tiles and coordinates for ASHLAR stitching.

        Returns the alignment channel of every cycle, as an index into that cycle's own channels
        (ASHLAR reads each cycle file separately).
        """
        # Flip y-axis (Leica → OME TIFF convention)
        for s, _ in enumerate(self.sample._spectra_slices):
            coordinates[:, s, 1] = np.max(coordinates[:, s, 1]) - (
                coordinates[:, s, 1] - np.min(coordinates[:, s, 1])
            )

        tiles = np.nan_to_num(tiles, nan=0.0)
        tiles = (tiles * 255).astype(np.uint8)

        # Alignment channel of each cycle: the channel with the highest per-tile mean intensity
        align_channels = []
        for start_ch, end_ch in self.sample._spectra_slices:
            mean_per_tile_channel = tiles[:, start_ch : end_ch + 1, :, :].mean(
                axis=(2, 3)
            )
            align_channels.append(int(np.argmax(mean_per_tile_channel.max(axis=0))))

        # Write OME-TIFF input files for each cycle
        for cycle, (start_ch, end_ch) in enumerate(self.sample._spectra_slices):
            tiles_cycle = tiles[:, start_ch : end_ch + 1, :, :]
            coordinates_cycle = coordinates[:, cycle, :]

            output_filename = os.path.join(
                self.sample.output_path, f"ashlar_input_cycle_{cycle + 1}.ome.tiff"
            )

            with tifffile.TiffWriter(output_filename, ome=True, bigtiff=True) as tif:
                tiles_cycle = tiles_cycle[:, np.newaxis, :, :, :]
                T, Z, C, Y, X = tiles_cycle.shape

                for t in range(T):
                    metadata = {
                        "Pixels": {
                            "PhysicalSizeX": float(
                                cast(
                                    np.ndarray,
                                    cast(
                                        RamanMetadata, self.sample.metadata
                                    ).pixel_size,
                                )[0]
                            ),
                            "PhysicalSizeY": float(
                                cast(
                                    np.ndarray,
                                    cast(
                                        RamanMetadata, self.sample.metadata
                                    ).pixel_size,
                                )[1]
                            ),
                            "PhysicalSizeXUnit": "µm",
                            "PhysicalSizeYUnit": "µm",
                            "SizeT": 1,
                            "SizeC": C,
                            "SizeY": Y,
                            "SizeX": X,
                            "SizeZ": 1,
                            "Type": "uint8",
                        },
                        "Channel": [{"Name": f"Channel_{i}"} for i in range(C)],
                        "Plane": [
                            {
                                "TheT": 0,
                                "TheC": c,
                                "TheZ": 0,
                                "PositionX": float(coordinates_cycle[t, 0]),
                                "PositionY": float(coordinates_cycle[t, 1]),
                                "PositionXUnit": "µm",
                                "PositionYUnit": "µm",
                            }
                            for c in range(C)
                        ],
                    }
                    tif.write(
                        tiles_cycle[t, 0, :, :, :],
                        metadata=metadata,
                        tile=(Y, X),
                        compression="zlib",
                    )

        return align_channels

    def ashlar_stitch(self, force_recomputing: bool = False) -> str:
        """
        Stitch corrected tiles into a final mosaic using ASHLAR.

        Returns
        -------
        str
                Path to the output OME-TIFF file.
        """
        output_file = preprocessed_file(
            self.sample.source_path,
            self.sample.sample_id,
            self.sample.modality_name,
            "ome.tiff",
        )

        if force_recomputing or not os.path.exists(output_file):
            if self.sample.corrected is None:
                raise ValueError(
                    "Run process_raw_tiles() before calling ashlar_stitch()."
                )

            env_name = "FOCUS_ASHLAR"

            align_channels = self._prepare_for_ashlar(
                tiles=self.sample.corrected,
                coordinates=copy.deepcopy(
                    cast(np.ndarray, self.sample.tiles_coordinates)
                ),
            )
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(5, 5, "Stitching with ASHLAR")
            _reporter.detail(
                "Reference channel per cycle "
                + ", ".join(str(channel) for channel in align_channels)
            )

            stitch_tiles(self.sample.output_path, align_channels, env_name)

            default_output = os.path.join(
                self.sample.output_path, "ashlar_output.ome.tiff"
            )
            os.rename(default_output, output_file)

            self.sample._mosaic = tifffile.imread(output_file)
            logger.debug(
                f"Sample {self.sample.sample_id}: stitched mosaic saved to {output_file}"
            )
        else:
            self.sample._mosaic = tifffile.imread(output_file)
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(5, 5, "Stitching with ASHLAR", cached=True)

        return output_file
