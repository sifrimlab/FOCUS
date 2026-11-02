"""Adapt configured microscopy settings and samples to the modality registry."""

from focus.config.types import ProcessingSettings
from focus.preprocessing.microscopy.parameters import MicroscopyImageSettings
from focus.runtime.reporting import StepReporter
from focus.pipeline.types import SampleFiles
import focus.preprocessing.microscopy.settings as _settings


from focus.config.options import SegmentationBackgroundColor
from focus.config.keys import MicroscopyImageProcessingParams
from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.preprocessing.microscopy.dataset import MicroscopyImageDataset


# --- Modality Registration ---


def _create_microscopy_samples(
    path: str, sample_ids: list[str], modality_name: str, settings: ProcessingSettings
) -> list[MicroscopyImage]:
    """Construct modality samples in the discovered sample order."""
    return [
        MicroscopyImage(source_path=path, sample_id=sid, modality_name=modality_name)
        for sid in sample_ids
    ]


def _create_microscopy_dataset(
    path: str, samples: list[MicroscopyImage], settings: ProcessingSettings
) -> MicroscopyImageDataset:
    """Bind the modality coordinator to its source path and samples."""
    return MicroscopyImageDataset(path=path, samples=samples)


def _extract_microscopy_settings(
    settings: ProcessingSettings,
) -> MicroscopyImageSettings:
    """Read configured settings and apply the established modality defaults."""
    return {
        "color_enhancement": settings.get(
            MicroscopyImageProcessingParams.COLOR_ENHANCEMENT, True
        ),
        "remove_background": settings.get(
            MicroscopyImageProcessingParams.REMOVE_BACKGROUND, True
        ),
        "crop_to_tissue": settings.get(
            MicroscopyImageProcessingParams.CROP_TO_TISSUE, True
        ),
        "background_color": settings.get(
            MicroscopyImageProcessingParams.BACKGROUND_COLOR,
            SegmentationBackgroundColor.WHITE,
        ),
        "min_object_coverage": settings.get(
            MicroscopyImageProcessingParams.MIN_OBJECT_COVERAGE, 0.01
        ),
        "force_recomputing": settings.get(
            MicroscopyImageProcessingParams.FORCE_RECOMPUTING, False
        ),
        "clip_percentile": settings.get(
            MicroscopyImageProcessingParams.CLIP_PERCENTILE,
            _settings._CLIP_PERCENTILE,
        ),
        "crop_margin": settings.get(
            MicroscopyImageProcessingParams.CROP_MARGIN,
            _settings._CROP_MARGIN_PX,
        ),
        "gamma": settings.get(
            MicroscopyImageProcessingParams.GAMMA,
            _settings._GAMMA,
        ),
        "contrast_saturation": settings.get(
            MicroscopyImageProcessingParams.CONTRAST_SATURATION,
            _settings._CONTRAST_SATURATION,
        ),
    }


def _execute(
    dataset: MicroscopyImageDataset,
    settings: MicroscopyImageSettings,
    reporter: StepReporter | None,
) -> SampleFiles:
    """Apply this modality's typed keyword settings to its dataset."""
    return dataset.process_dataset(step_reporter=reporter, **settings)


register_modality(
    "microscopy_image",
    ModalityHandler(
        create_samples=_create_microscopy_samples,
        create_dataset=_create_microscopy_dataset,
        extract_settings=_extract_microscopy_settings,
        execute=_execute,
    ),
)
