"""Process ordered microscopy samples and report sample-level failures."""

from focus.config.options import MicroscopyImageType, MicroscopyZProjection
from focus.preprocessing.base import DatasetState
from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter


class MicroscopyImageDataset(DatasetState[MicroscopyImage]):
    """Handle a collection of MicroscopyImage samples."""

    def __init__(self, path: str, samples: list[MicroscopyImage]) -> None:
        """Retain the dataset root and ordered modality sample objects.

        Raises
        ------
        ValueError
            If an item is not a MicroscopyImage, after binding the dataset state."""
        super().__init__(path, samples)
        for sample in samples:
            if not isinstance(sample, MicroscopyImage):
                raise ValueError(f"Invalid sample: {sample}. Expected MicroscopyImage.")

    def process_dataset(
        self,
        image_type: str = MicroscopyImageType.AUTO,
        z_projection: str = MicroscopyZProjection.AUTO,
        crop_to_tissue: bool = False,
        remove_background: bool = False,
        pixel_size: float | None = None,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """Preprocess every image with the same settings; a failing sample is reported and skipped.

        Returns
        -------
        dict[str, str]
            Maps sample IDs to output OME-TIFF paths.
        """
        reporter = step_reporter or get_reporter()
        settings = ImageSettings(
            image_type, z_projection, crop_to_tissue, remove_background, pixel_size
        )
        processed = {}
        for sample in reporter.samples(self.samples):
            sample._step_reporter = reporter
            try:
                processed[sample.sample_id] = sample.process_image(
                    settings, force_recomputing
                )
            except Exception as e:
                reporter.warning(f"Sample skipped: {e}")
        return processed
