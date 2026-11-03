"""Adapt configured microscopy settings and samples to the modality registry."""

from focus.config.keys import MicroscopyImageProcessingParams as Keys
from focus.config.options import MicroscopyImageType, MicroscopyZProjection
from focus.config.types import ProcessingSettings
from focus.pipeline.types import SampleFiles
from focus.preprocessing.microscopy.dataset import MicroscopyImageDataset
from focus.preprocessing.microscopy.parameters import MicroscopyImageSettings
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.runtime.reporting import StepReporter


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
    """Read configured settings and apply the modality defaults (lossless, nothing removed)."""
    return {
        "image_type": settings.get(Keys.IMAGE_TYPE, MicroscopyImageType.AUTO),
        "z_projection": settings.get(Keys.Z_PROJECTION, MicroscopyZProjection.AUTO),
        "crop_to_tissue": settings.get(Keys.CROP_TO_TISSUE, False),
        "remove_background": settings.get(Keys.REMOVE_BACKGROUND, False),
        "pixel_size": settings.get(Keys.PIXEL_SIZE),
        "force_recomputing": settings.get(Keys.FORCE_RECOMPUTING, False),
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
