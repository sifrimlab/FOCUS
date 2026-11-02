"""Process ordered microscopy samples and report sample-level failures."""

from focus.runtime.reporting import StepReporter
import focus.preprocessing.microscopy.settings as _settings


from focus.runtime.reporter_context import get_reporter
from focus.config.options import SegmentationBackgroundColor
from focus.preprocessing.base import DatasetState
from focus.preprocessing.microscopy.sample import MicroscopyImage


class MicroscopyImageDataset(DatasetState[MicroscopyImage]):
    """Handle a collection of MicroscopyImage samples."""

    def __init__(self, path: str, samples: list[MicroscopyImage]) -> None:
        """Retain the dataset root and ordered modality sample objects.

        Parameters
        ----------
        path : str
            Root used for existing per-sample and merged output paths.
        samples : list
            Modality sample objects retained by reference.

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
        color_enhancement: bool = True,
        remove_background: bool = True,
        crop_to_tissue: bool = True,
        background_color: str = SegmentationBackgroundColor.WHITE,
        min_object_coverage: float = 0.01,
        force_recomputing: bool = False,
        clip_percentile: int = _settings._CLIP_PERCENTILE,
        crop_margin: int = _settings._CROP_MARGIN_PX,
        gamma: float = _settings._GAMMA,
        contrast_saturation: float = _settings._CONTRAST_SATURATION,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Preprocess all microscopy images in the dataset.
        All parameters are forwarded to each sample's process_image method.

        Returns
        -------
        dict[str, str]
                Maps sample IDs to output OME-TIFF paths.
        """
        reporter = step_reporter or get_reporter()
        processed_samples = {}
        for sample in reporter.samples(self.samples):
            sample._step_reporter = reporter
            try:
                output_file = sample.process_image(
                    color_enhancement=color_enhancement,
                    remove_background=remove_background,
                    crop_to_tissue=crop_to_tissue,
                    background_color=background_color,
                    min_object_coverage=min_object_coverage,
                    force_recomputing=force_recomputing,
                    clip_percentile=clip_percentile,
                    crop_margin=crop_margin,
                    gamma=gamma,
                    contrast_saturation=contrast_saturation,
                )
                processed_samples[sample.sample_id] = output_file
            except Exception as e:
                reporter.warning(f"Sample skipped: {e}")
        return processed_samples
