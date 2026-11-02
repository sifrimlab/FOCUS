"""Execute serial or joblib Raman spectral-cleaning tasks and restore their scan slices."""

from __future__ import annotations
from typing import cast
from focus.preprocessing.raman.spectra import _process_tile_parallel
from typing import TYPE_CHECKING
import focus.preprocessing.raman.settings as _settings

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage

import os
from focus.runtime.reporter_context import get_reporter
import numpy as np
from joblib import Parallel, delayed


class RamanTiles:
    """Clean Raman spectral slices serially or through ordered process-worker tasks."""

    def __init__(self, sample: RamanImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    # --- Processing steps ---

    def process_raw_tiles(
        self,
        wavenumbers: np.ndarray | None = None,
        parallel: bool = True,
        force_recomputing: bool = False,
        savgol_window: int = _settings._SAVGOL_WINDOW,
        savgol_polyorder: int = _settings._SAVGOL_POLYORDER,
    ) -> None:
        """
        Process raw tiles using the RamanSPy spectral cleaning pipeline.

        Parameters
        ----------
        wavenumbers : np.ndarray, optional
                Wavenumber array. If None, uses the loaded metadata wavenumbers.
        parallel : bool
                Process tiles in parallel.
        force_recomputing : bool
                Force reprocessing even if cached results exist.
        savgol_window : int
                Savitzky-Golay filter window length.
        savgol_polyorder : int
                Savitzky-Golay filter polynomial order.
        """
        cache_file = os.path.join(self.sample.output_path, "raman_corrected_tiles.npy")

        if force_recomputing or not os.path.exists(cache_file):
            if self.sample._basic_corrected_tiles is None:
                raise ValueError(
                    "No corrected tiles to process. Run basic_correct() first."
                )

            if wavenumbers is None:
                wavenumbers = cast(np.ndarray, self.sample.wavenumbers)

            self.sample._raman_corrected_tiles = np.zeros_like(
                self.sample._basic_corrected_tiles, dtype=np.float32
            )

            if parallel:
                slice_result = self._parallel_clean(
                    wavenumbers, savgol_window, savgol_polyorder
                )
                self._assign_results(slice_result)
            else:
                self._serial_clean(wavenumbers, savgol_window, savgol_polyorder)

            np.save(cache_file, self.sample._raman_corrected_tiles)
        else:
            self.sample._raman_corrected_tiles = np.load(cache_file)
            _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
            _reporter.step(4, 5, "Cleaning spectra", cached=True)

    def _cleaning_units(
        self,
        wavenumbers: np.ndarray,
        savgol_window: int,
        savgol_polyorder: int,
    ) -> list[tuple[np.ndarray, np.ndarray, int, int, int, int]]:
        """Build ordered scan-major tasks containing tile views and matching wavenumber slices."""
        tiles = cast(np.ndarray, self.sample._basic_corrected_tiles)
        units = []
        for slice_idx, (start_ch, end_ch) in enumerate(self.sample._spectra_slices):
            for tile_idx in range(tiles.shape[0]):
                units.append(
                    (
                        tiles[tile_idx, start_ch : end_ch + 1, :, :],
                        wavenumbers[start_ch : end_ch + 1],
                        tile_idx,
                        slice_idx,
                        savgol_window,
                        savgol_polyorder,
                    )
                )

        return units

    def _parallel_clean(
        self,
        wavenumbers: np.ndarray,
        savgol_window: int,
        savgol_polyorder: int,
    ) -> list[tuple[np.ndarray, int, int]]:
        """Run ordered joblib generator results with the existing worker limit and progress events."""
        units = self._cleaning_units(wavenumbers, savgol_window, savgol_polyorder)
        _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
        slice_result = list(
            _reporter.tqdm(
                Parallel(n_jobs=self.sample._max_workers, return_as="generator")(
                    delayed(_process_tile_parallel)(*args) for args in units
                ),
                4,
                5,
                "Cleaning spectra",
                total=len(units),
                unit="tile",
            )
        )

        return slice_result

    def _assign_results(self, slice_result: list[tuple[np.ndarray, int, int]]) -> None:
        """Copy each cleaned tile into its original inclusive spectral slice."""
        for processed_tile, tile_index, slice_index in slice_result:
            start_ch, end_ch = self.sample._spectra_slices[slice_index]
            cast(np.ndarray, self.sample._raman_corrected_tiles)[
                tile_index, start_ch : end_ch + 1
            ] = processed_tile

    def _serial_clean(
        self,
        wavenumbers: np.ndarray,
        savgol_window: int,
        savgol_polyorder: int,
    ) -> None:
        """Clean tile-major spectral slices in the same serial order as the original workflow."""
        tiles = cast(np.ndarray, self.sample._basic_corrected_tiles)
        _reporter = getattr(self.sample, "_step_reporter", None) or get_reporter()
        for tile_idx in _reporter.tqdm(
            range(tiles.shape[0]),
            4,
            5,
            "Cleaning spectra",
            unit="tile",
        ):
            for slice_index, (start_ch, end_ch) in enumerate(
                self.sample._spectra_slices
            ):
                processed_tile, _, _ = _process_tile_parallel(
                    tiles[tile_idx, start_ch : end_ch + 1, :, :],
                    wavenumbers[start_ch : end_ch + 1],
                    tile_idx,
                    slice_index,
                    savgol_window,
                    savgol_polyorder,
                )
                cast(np.ndarray, self.sample._raman_corrected_tiles)[
                    tile_idx, start_ch : end_ch + 1
                ] = processed_tile
