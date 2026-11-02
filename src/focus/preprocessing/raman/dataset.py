"""Coordinate ordered Raman sample preprocessing and successful-run cache cleanup."""

from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.preprocessing.raman.workflow import process_sample, clean_intermediates

from focus.runtime.reporting import StepReporter
import focus.preprocessing.raman.settings as _settings

from focus.runtime.reporter_context import get_reporter

from focus.preprocessing.base import DatasetState
from focus.preprocessing.raman.sample import RamanImage


class RamanDataset(DatasetState[RamanImage]):
    """Handle a collection of RamanImage samples."""

    def __init__(self, path: str, samples: list[RamanImage]) -> None:
        """Retain the dataset root and ordered modality sample objects.

        Parameters
        ----------
        path : str
            Root used for existing per-sample and merged output paths.
        samples : list
            Modality sample objects retained by reference."""
        super().__init__(path, samples)

    def process_dataset(
        self,
        force_recomputing: bool = False,
        max_workers: int = _settings._MAX_WORKERS,
        savgol_window: int = _settings._SAVGOL_WINDOW,
        savgol_polyorder: int = _settings._SAVGOL_POLYORDER,
        bg_min_area_fraction: float = _settings._BG_MIN_AREA_FRACTION,
        otsu_threshold_factor: float = _settings._OTSU_THRESHOLD_FACTOR,
        min_object_size: int = _settings._MIN_OBJECT_SIZE,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Process each sample through the full Raman preprocessing pipeline.
        All processing parameters are forwarded to per-sample methods.

        Parameters
        ----------
        force_recomputing : bool
                Force reprocessing even if cached results exist.
        max_workers : int
                Maximum parallel workers for BaSiC and spectral cleaning.
        savgol_window : int
                Savitzky-Golay filter window length.
        savgol_polyorder : int
                Savitzky-Golay filter polynomial order.
        bg_min_area_fraction : float
                Minimum contour area as fraction of image area for background removal.
        otsu_threshold_factor : float
                Multiplicative factor for Otsu threshold adjustment.
        min_object_size : int
                Minimum connected component size in pixels for morphological cleanup.

        Returns
        -------
        dict[str, str]
                Maps sample IDs to output OME-TIFF paths.
        """
        reporter = step_reporter or get_reporter()
        settings = RamanRunSettings(
            force_recomputing,
            max_workers,
            savgol_window,
            savgol_polyorder,
            bg_min_area_fraction,
            otsu_threshold_factor,
            min_object_size,
        )
        processed_samples = {}
        for sample in reporter.samples(self.samples):
            sample._max_workers = max_workers
            sample._step_reporter = reporter
            try:
                output_file = process_sample(sample, settings, reporter)
                processed_samples[sample.sample_id] = output_file
                clean_intermediates(sample)
            except Exception as e:
                reporter.warning(f"Sample skipped: {e}")
        return processed_samples
