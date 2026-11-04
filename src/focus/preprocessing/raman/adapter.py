"""Adapt configured Raman settings and samples to the modality registry."""

from focus.config.keys import RamanPreprocessingParams as Keys
from focus.config.options import RamanNormalization
from focus.config.types import ProcessingSettings
from focus.pipeline.types import SampleFiles
from focus.preprocessing.raman.dataset import RamanDataset
from focus.preprocessing.raman.parameters import RamanSettings
from focus.preprocessing.raman.sample import RamanImage
from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.runtime.reporting import StepReporter


def _create_raman_samples(
    path: str, sample_ids: list[str], modality_name: str, settings: ProcessingSettings
) -> list[RamanImage]:
    """Construct modality samples in the discovered sample order."""
    return [
        RamanImage(source_path=path, sample_id=sid, modality_name=modality_name)
        for sid in sample_ids
    ]


def _create_raman_dataset(
    path: str, samples: list[RamanImage], settings: ProcessingSettings
) -> RamanDataset:
    """Bind the modality coordinator to its source path and samples."""
    return RamanDataset(path=path, samples=samples)


def _extract_raman_settings(settings: ProcessingSettings) -> RamanSettings:
    """Read configured settings and apply the modality defaults."""
    return {
        "denoise": bool(settings.get(Keys.DENOISE, True)),
        "remove_offset": bool(settings.get(Keys.REMOVE_OFFSET, False)),
        "normalization": settings.get(Keys.NORMALIZATION, RamanNormalization.NONE),
        "force_recomputing": settings.get(Keys.FORCE_RECOMPUTING, False),
    }


def _execute(
    dataset: RamanDataset, settings: RamanSettings, reporter: StepReporter | None
) -> SampleFiles:
    """Apply this modality's typed keyword settings to its dataset."""
    return dataset.process_dataset(step_reporter=reporter, **settings)


register_modality(
    "raman",
    ModalityHandler(
        create_samples=_create_raman_samples,
        create_dataset=_create_raman_dataset,
        extract_settings=_extract_raman_settings,
        execute=_execute,
    ),
)
