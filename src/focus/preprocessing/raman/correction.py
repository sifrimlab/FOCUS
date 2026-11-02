"""Coordinate Raman illumination correction and background segmentation with their caches."""

from __future__ import annotations
from focus.preprocessing.raman.segmentation import tissue_mask
from focus.preprocessing.raman.tool_execution import require_basic_tool, correct_channel
from typing import cast
from focus.preprocessing.raman.metadata import RamanMetadata
from typing import TYPE_CHECKING
import focus.preprocessing.raman.settings as _settings

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage

import os
from focus.runtime.reporter_context import get_reporter
import numpy as np
import concurrent.futures


class RamanCorrection:
    """Coordinate illumination and background correction on a sample's loaded Raman tiles."""

    def __init__(self, sample: RamanImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def basic_correct(self, force_recomputing: bool = False) -> None:
        """Apply BaSiC illumination correction to raw tiles via external conda environment."""
        cache_file = os.path.join(self.sample.output_path, "basic_corrected_tiles.npy")

        if force_recomputing or not os.path.exists(cache_file):
            if self.sample._raw_tiles is None:
                raise ValueError("No raw tiles to correct. Load the data first.")

            env_name, main_script = require_basic_tool()

            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            self.sample._basic_corrected_tiles = np.zeros_like(
                self.sample._raw_tiles, dtype=np.float32
            )

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=self.sample._max_workers
            ) as executor:
                futures = {
                    executor.submit(
                        correct_channel, self.sample, env_name, main_script, idx
                    ): idx
                    for idx in range(self.sample._raw_tiles.shape[1])
                }
                for future in _reporter.tqdm(
                    concurrent.futures.as_completed(futures),
                    2,
                    5,
                    "BaSiC correction",
                    total=len(futures),
                    unit="channel",
                ):
                    channel_idx, corrected_channel = future.result()
                    self.sample._basic_corrected_tiles[:, channel_idx, :, :] = (
                        corrected_channel
                    )

            normalize_corrected_tiles(self.sample._basic_corrected_tiles)

            np.save(cache_file, self.sample._basic_corrected_tiles)
        else:
            self.sample._basic_corrected_tiles = np.load(cache_file)
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(2, 5, "BaSiC correction", cached=True)

    def remove_background(
        self,
        force_recomputing: bool = False,
        bg_min_area_fraction: float = _settings._BG_MIN_AREA_FRACTION,
        otsu_threshold_factor: float = _settings._OTSU_THRESHOLD_FACTOR,
        min_object_size: int = _settings._MIN_OBJECT_SIZE,
    ) -> None:
        """
        Remove background from BaSiC corrected tiles using Otsu thresholding on a quick-stitched mosaic.

        Parameters
        ----------
        force_recomputing : bool
                Force reprocessing even if cached results exist.
        bg_min_area_fraction : float
                Minimum contour area as fraction of total image area.
        otsu_threshold_factor : float
                Multiplicative factor applied to Otsu threshold.
        min_object_size : int
                Minimum connected component size in pixels.
        """
        if self.sample._basic_corrected_tiles is None:
            raise RuntimeError("No BaSiC corrected tiles. Run basic_correct() first.")

        cache_file = os.path.join(self.sample.output_path, "segmented_tiles.npy")

        if force_recomputing or not os.path.exists(cache_file):
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(3, 5, "Removing background")

            self.sample.stitcher._quick_stitch()

            segmentation_mask = tissue_mask(
                cast(np.ndarray, self.sample._quick_mosaic),
                bg_min_area_fraction,
                otsu_threshold_factor,
                min_object_size,
            )

            tiles_masks = self.sample.stitcher._extract_tiles_segmentation_from_mosaic(
                mosaic=segmentation_mask,
                coordinates=cast(np.ndarray, self.sample._tiles_coordinates),
                pixel_size=cast(
                    np.ndarray, cast(RamanMetadata, self.sample.metadata).pixel_size
                )[0],
                tile_size=(
                    cast(int, cast(RamanMetadata, self.sample.metadata).scan_height),
                    cast(int, cast(RamanMetadata, self.sample.metadata).scan_width),
                ),
            )

            segmented_tiles = self.sample._basic_corrected_tiles * tiles_masks
            np.save(cache_file, segmented_tiles)
            self.sample._basic_corrected_tiles = segmented_tiles
        else:
            self.sample._basic_corrected_tiles = np.load(cache_file)
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(3, 5, "Removing background", cached=True)


def normalize_corrected_tiles(tiles: np.ndarray) -> None:
    """Normalize corrected (tiles, C, H, W) float32 values in place using their global range."""
    # Global normalization to [0, 1]
    min_val = tiles.min()
    max_val = tiles.max()
    if max_val > min_val:
        tiles -= min_val
        tiles /= max_val - min_val
