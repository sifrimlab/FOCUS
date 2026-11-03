"""Adapt transcriptomics samples and configured settings to the modality registry."""

from focus.config.types import ProcessingSettings
from focus.preprocessing.transcriptomics.parameters import SpatialTranscriptomicSettings
from focus.runtime.reporting import StepReporter
from focus.pipeline.types import SampleFiles


from focus.config.keys import STPreprocessingParams
from focus.config.options import StCapture, StNormalization, StUnit

from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic
from focus.preprocessing.transcriptomics.dataset import SpatialTranscriptomicDataset


# --- Modality Registration ---


def _create_st_samples(
    path: str, sample_ids: list[str], modality_name: str, settings: ProcessingSettings
) -> list[SpatialTranscriptomic]:
    """Construct modality samples in the discovered sample order."""
    return [
        SpatialTranscriptomic(
            source_path=path, sample_id=sid, modality_name=modality_name
        )
        for sid in sample_ids
    ]


def _create_st_dataset(
    path: str, samples: list[SpatialTranscriptomic], settings: ProcessingSettings
) -> SpatialTranscriptomicDataset:
    """Bind the modality coordinator to its source path and samples."""
    return SpatialTranscriptomicDataset(path=path, samples=samples)


def _extract_st_settings(settings: ProcessingSettings) -> SpatialTranscriptomicSettings:
    """Read configured settings and apply the established modality defaults."""
    return {
        "capture": settings.get(STPreprocessingParams.CAPTURE, StCapture.SEQUENCING),
        "unit": settings.get(STPreprocessingParams.UNIT, StUnit.SPOT),
        "normalization": settings.get(
            STPreprocessingParams.NORMALIZATION, StNormalization.AUTO
        ),
        "quality_control": settings.get(STPreprocessingParams.QUALITY_CONTROL, True),
        "remove_mitochondrial_genes": settings.get(
            STPreprocessingParams.REMOVE_MITOCHONDRIAL_GENES, False
        ),
        "force_recomputing": settings.get(
            STPreprocessingParams.FORCE_RECOMPUTING, False
        ),
    }


def _execute(
    dataset: SpatialTranscriptomicDataset,
    settings: SpatialTranscriptomicSettings,
    reporter: StepReporter | None,
) -> SampleFiles:
    """Apply this modality's typed keyword settings to its dataset."""
    return dataset.process_dataset(step_reporter=reporter, **settings)


register_modality(
    "st",
    ModalityHandler(
        create_samples=_create_st_samples,
        create_dataset=_create_st_dataset,
        extract_settings=_extract_st_settings,
        execute=_execute,
    ),
)
