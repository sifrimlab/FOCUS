"""Discover acquired MSI polarities and adapt configured settings to the modality registry."""

from focus.config.types import ProcessingSettings
from focus.preprocessing.msi.parameters import MsiSettings
from focus.runtime.reporting import StepReporter
from focus.pipeline.types import SampleFiles

import os
from focus.io.discovery import find_imzml_pair

from focus.config.keys import MsiPreprocessingParams
from focus.config.options import (
    MsiIntensityNormalization,
    MsiSampleType,
    MsiIonMode,
    SpotResolution,
)
from focus.preprocessing.registry import ModalityHandler, register_modality
from focus.preprocessing.msi.dataset import MsiDataset
from focus.preprocessing.msi.sample import MsiSample
from focus.config.mass_lists import parse_mz_by_mode


# --- Modality Registration ---


def _create_msi_samples(
    path: str, sample_ids: list[str], modality_name: str, settings: ProcessingSettings
) -> list[MsiSample]:
    """Construct modality samples in the discovered sample order."""
    samples = []
    for sample_id in sample_ids:
        modality_path = os.path.join(path, sample_id, modality_name)

        # The GUI scaffolds both pos/ and neg/ for every MSI modality, so directory presence says
        # nothing about which ion modes were acquired. Decide from the imzML/IBD pairs on disk and
        # treat an ion mode directory holding neither file kind as "not acquired".
        available_modes = [
            mode
            for mode in MsiIonMode.list()
            if find_imzml_pair(os.path.join(modality_path, mode)) is not None
        ]

        if not available_modes:
            raise FileNotFoundError(
                f"No complete .imzML + .ibd file pair found for sample '{sample_id}' in "
                f"'{modality_path}'. Expected one in at least one of the ion mode "
                f"subdirectories: {MsiIonMode.list()}."
            )

        samples.append(
            MsiSample(
                source_path=path,
                sample_id=sample_id,
                modality_name=modality_name,
                double_ion_mode=len(available_modes) > 1,
                ion_mode=None if len(available_modes) > 1 else available_modes[0],
            )
        )
    return samples


def _create_msi_dataset(
    path: str, samples: list[MsiSample], settings: ProcessingSettings
) -> MsiDataset:
    """Bind the modality coordinator to its source path and samples."""
    lipid_annotation_db = settings.get(MsiPreprocessingParams.LIPID_ANNOTATION_DB, None)
    return MsiDataset(
        path=path, samples=samples, lipid_annotation_db=lipid_annotation_db
    )


def _extract_msi_settings(settings: ProcessingSettings) -> MsiSettings:
    """Read configured settings and apply the established modality defaults."""
    return {
        "mass_tolerance": settings.get(MsiPreprocessingParams.MASS_TOLERANCE, 10),
        "frequency_threshold": settings.get(
            MsiPreprocessingParams.FREQUENCY_THRESHOLD, 0.01
        ),
        "intensity_normalization": settings.get(
            MsiPreprocessingParams.INTENSITY_NORMALIZATION,
            MsiIntensityNormalization.NONE,
        ),
        "recalibration_reference": parse_mz_by_mode(
            settings.get(MsiPreprocessingParams.RECALIBRATION_REFERENCE, None),
            MsiPreprocessingParams.RECALIBRATION_REFERENCE,
        ),
        "internal_standard_mz": parse_mz_by_mode(
            settings.get(MsiPreprocessingParams.INTERNAL_STANDARD_MZ, None),
            MsiPreprocessingParams.INTERNAL_STANDARD_MZ,
        ),
        "detect_background": settings.get(
            MsiPreprocessingParams.DETECT_BACKGROUND, False
        ),
        "sample_type": settings.get(
            MsiPreprocessingParams.SAMPLE_TYPE, MsiSampleType.TISSUE
        ),
        "resolution": settings.get(
            MsiPreprocessingParams.RESOLUTION, SpotResolution.STANDARD
        ),
        "force_recomputing": settings.get(
            MsiPreprocessingParams.FORCE_RECOMPUTING, False
        ),
    }


def _execute(
    dataset: MsiDataset, settings: MsiSettings, reporter: StepReporter | None
) -> SampleFiles:
    """Apply this modality's typed keyword settings to its dataset."""
    return dataset.process_dataset(step_reporter=reporter, **settings)


register_modality(
    "msi",
    ModalityHandler(
        create_samples=_create_msi_samples,
        create_dataset=_create_msi_dataset,
        extract_settings=_extract_msi_settings,
        execute=_execute,
    ),
)
