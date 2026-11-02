"""Validated source paths and sample collections shared by modality coordinators."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.runtime.reporting import StepReporter
import os
from typing import Generic, TypeVar

from focus.io.paths import FocusOutputDirectories
from focus.io.discovery import validate_path_readable


SampleT = TypeVar("SampleT")


class SampleSource:
    """Shared path initialization for modality-specific sample processors.

    Provides common initialization: path validation, attribute assignment,
    and output directory creation.
    """

    _step_reporter: StepReporter

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Validate the source root and create this sample's preprocessing directory.

        Parameters
        ----------
        source_path : str
            Existing readable dataset root; validation errors propagate unchanged.
        sample_id : str
            Identifier used as the sample directory and observation prefix.
        modality_name : str
            Modality subdirectory and output-name component."""
        validate_path_readable(source_path)
        self.source_path = source_path
        self.sample_id = sample_id
        self.modality_name = modality_name
        self.output_path = os.path.join(
            source_path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name
        )
        os.makedirs(self.output_path, exist_ok=True)


class DatasetState(Generic[SampleT]):
    """Typed path and sample collection for modality-specific dataset processors.

    Provides paths and samples without imposing incompatible processing signatures.
    """

    def __init__(self, path: str, samples: list[SampleT]) -> None:
        """Retain the dataset root and ordered sample list without copying or validating them.

        Parameters
        ----------
        path : str
            Dataset source/output root.
        samples : list[SampleT]
            Modality-specific sample objects, retained by reference."""
        self.dataset_source_path = path
        self.samples = samples

    @staticmethod
    def _check_cache(output_path: str, force_recomputing: bool) -> bool:
        """Returns True if a cached result exists and force_recomputing is False."""
        return not force_recomputing and os.path.exists(output_path)
