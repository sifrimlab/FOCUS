"""Adapt configured Raman settings and samples to the modality registry."""

from focus.config.types import ProcessingSettings
from focus.preprocessing.raman.parameters import RamanSettings
from focus.runtime.reporting import StepReporter
from focus.pipeline.types import SampleFiles
import focus.preprocessing.raman.settings as _settings


from focus.config.keys import RamanPreprocessingParams
from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.preprocessing.raman.dataset import RamanDataset
from focus.preprocessing.raman.sample import RamanImage


# --- Modality Registration ---


def _create_raman_samples(
    path: str, sample_ids: list[str], modality_name: str, settings: ProcessingSettings
) -> list[RamanImage]:
    """Construct modality samples in the discovered sample order."""
    max_workers = settings.get(
        RamanPreprocessingParams.MAX_WORKERS,
        _settings._MAX_WORKERS,
    )
    return [
        RamanImage(
            source_path=path,
            sample_id=sid,
            modality_name=modality_name,
            max_workers=max_workers,
        )
        for sid in sample_ids
    ]


def _create_raman_dataset(
    path: str, samples: list[RamanImage], settings: ProcessingSettings
) -> RamanDataset:
    """Bind the modality coordinator to its source path and samples."""
    return RamanDataset(path=path, samples=samples)


def _extract_raman_settings(settings: ProcessingSettings) -> RamanSettings:
    """Read configured settings and apply the established modality defaults."""
    return {
        "force_recomputing": settings.get(
            RamanPreprocessingParams.FORCE_RECOMPUTING, False
        ),
        "max_workers": settings.get(
            RamanPreprocessingParams.MAX_WORKERS,
            _settings._MAX_WORKERS,
        ),
        "savgol_window": settings.get(
            RamanPreprocessingParams.SAVGOL_WINDOW,
            _settings._SAVGOL_WINDOW,
        ),
        "savgol_polyorder": settings.get(
            RamanPreprocessingParams.SAVGOL_POLYORDER,
            _settings._SAVGOL_POLYORDER,
        ),
        "bg_min_area_fraction": settings.get(
            RamanPreprocessingParams.BG_MIN_AREA_FRACTION,
            _settings._BG_MIN_AREA_FRACTION,
        ),
        "otsu_threshold_factor": settings.get(
            RamanPreprocessingParams.OTSU_THRESHOLD_FACTOR,
            _settings._OTSU_THRESHOLD_FACTOR,
        ),
        "min_object_size": settings.get(
            RamanPreprocessingParams.MIN_OBJECT_SIZE,
            _settings._MIN_OBJECT_SIZE,
        ),
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
