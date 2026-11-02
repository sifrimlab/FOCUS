"""Hold one Raman source, acquisition metadata and loaded/intermediate image arrays."""

from numpy.typing import NDArray

from functools import cached_property

from focus.preprocessing.raman.reader import RamanReader
from focus.preprocessing.raman.tiles import RamanTiles
from focus.preprocessing.raman.correction import RamanCorrection
from focus.preprocessing.raman.stitching import RamanStitcher
import focus.preprocessing.raman.settings as _settings
import tifffile
import os
from focus.runtime.reporter_context import get_reporter
import numpy as np

from focus.preprocessing.base import SampleSource
from focus.preprocessing.raman.metadata import RamanMetadata


class RamanImage(SampleSource):
    """
    Process Raman Spectral Images. Supports Leica LIF files.
    Pipeline: load → BaSiC correction → background removal → spectral cleaning → ASHLAR stitching → OME-TIFF.
    """

    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
        max_workers: int = _settings._MAX_WORKERS,
    ) -> None:
        """
        Parameters
        ----------
        source_path : str
                Path to the data source directory.
        sample_id : str
                Sample ID.
        modality_name : str
                Name of the modality.
        max_workers : int
                Maximum number of workers for parallel processing.
        """
        super().__init__(source_path, sample_id, modality_name)
        self.input_path = os.path.join(source_path, sample_id, modality_name)
        self._max_workers = max_workers

        # Intermediate data
        self._raw_tiles: NDArray[np.float32] | None = None
        self._basic_corrected_tiles: NDArray[np.float32] | None = None
        self._raman_corrected_tiles: NDArray[np.float32] | None = None
        self._quick_mosaic: NDArray[np.uint8] | None = None
        self._mosaic: NDArray[np.generic] | None = None
        self._metadata: RamanMetadata | None = None
        self._wavenumbers: NDArray[np.float32] | None = None
        self._tiles_coordinates: NDArray[np.float32] | None = None
        self._spectra_slices: list[tuple[int, int]] = []

    def load_source(self) -> None:
        """Load the source data. Looks for supported file formats (LIF) in the input directory."""
        os.makedirs(self.output_path, exist_ok=True)

        reporter = getattr(self, "_step_reporter", None) or get_reporter()
        found = False
        with os.scandir(self.input_path) as it:
            for entry in it:
                if entry.is_file() and entry.name.lower().endswith(".lif"):
                    reporter.step(1, 5, "Loading LIF data")
                    reporter.detail(entry.name)
                    self.reader._load_lif(os.path.join(self.input_path, entry.name))
                    found = True
                    break

        if not found:
            raise FileNotFoundError(
                f"No supported Raman source file found in {self.input_path}"
            )

    # --- Properties ---

    @property
    def raw(self) -> np.ndarray | None:
        """Raw tiles: (T, C, Y, X) float32."""
        return self._raw_tiles

    @property
    def corrected(self) -> np.ndarray | None:
        """Corrected tiles (BaSiC + background + RamanSPy): (T, C, Y, X) float32."""
        return self._raman_corrected_tiles

    @property
    def mosaic(self) -> np.ndarray | None:
        """Final stitched mosaic: (C, Y, X)."""
        return self._mosaic

    @property
    def metadata(self) -> RamanMetadata | None:
        """Return loaded Raman metadata, or None before source loading."""
        return self._metadata

    @property
    def wavenumbers(self) -> np.ndarray | None:
        """Wavenumber array: (W,) float32."""
        return self._wavenumbers

    @property
    def tiles_coordinates(self) -> np.ndarray | None:
        """Tile coordinates: (T, N_slices, 2) float32."""
        return self._tiles_coordinates

    @property
    def sample_id(self) -> str:
        """Read or validate the string identifier of this sample."""
        return self._sample_id

    @sample_id.setter
    def sample_id(self, value: str) -> None:
        """Read or validate the string identifier of this sample."""
        if not isinstance(value, str):
            raise TypeError("Sample ID must be a string.")
        self._sample_id = value

    def _force_mosaic_load(self, output_path: str, filename: str) -> None:
        """Load the existing stitched TIFF into the sample's mosaic state."""
        self._mosaic = tifffile.imread(
            os.path.join(output_path, f"{filename}.ome.tiff")
        )

    @cached_property
    def reader(self) -> RamanReader:
        """Return the RamanReader component bound to this sample."""
        return RamanReader(self)

    @cached_property
    def tiles(self) -> RamanTiles:
        """Return the RamanTiles component bound to this sample."""
        return RamanTiles(self)

    @cached_property
    def correction(self) -> RamanCorrection:
        """Return the RamanCorrection component bound to this sample."""
        return RamanCorrection(self)

    @cached_property
    def stitcher(self) -> RamanStitcher:
        """Return the RamanStitcher component bound to this sample."""
        return RamanStitcher(self)
