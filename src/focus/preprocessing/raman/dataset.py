"""Process ordered Raman samples and report sample-level failures."""

from focus.config.options import RamanNormalization
from focus.preprocessing.base import DatasetState
from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.preprocessing.raman.sample import RamanImage
from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter


class RamanDataset(DatasetState[RamanImage]):
    """Handle a collection of RamanImage samples."""

    def __init__(self, path: str, samples: list[RamanImage]) -> None:
        """Retain the dataset root and ordered modality sample objects."""
        super().__init__(path, samples)

    def process_dataset(
        self,
        denoise: bool = True,
        remove_offset: bool = False,
        normalization: str = RamanNormalization.NONE,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """Preprocess every sample with the same settings; a failing sample is reported and skipped.

        Parameters
        ----------
        denoise : bool
            Project spectra on the components that are real at the cellular scale.
        remove_offset : bool
            Subtract each pixel's minimum over its spectral window.
        normalization : str
            ``none`` (quantitative) or ``area`` (each spectrum divided by its mean).
        force_recomputing : bool
            Recompute even when the output carries this fingerprint.

        Returns
        -------
        dict[str, str]
            Maps sample IDs to output OME-TIFF paths.
        """
        reporter = step_reporter or get_reporter()
        settings = RamanRunSettings(denoise, remove_offset, normalization)
        processed = {}
        for sample in reporter.samples(self.samples):
            sample._step_reporter = reporter
            try:
                processed[sample.sample_id] = sample.process(
                    settings, force_recomputing
                )
            except Exception as e:
                reporter.warning(f"Sample skipped: {e}")
        return processed
