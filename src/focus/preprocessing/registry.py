"""Typed modality construction and execution, independent of concrete datasets."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from focus.config.types import ProcessingSettings
from focus.pipeline.types import SampleFiles
from focus.runtime.reporting import StepReporter

SampleT = TypeVar("SampleT")
DatasetT = TypeVar("DatasetT")
SettingsT = TypeVar("SettingsT")


class Preprocessor(Protocol):
    """The single operation required by the modality dispatcher."""

    def preprocess(
        self,
        path: str,
        sample_ids: list[str],
        modality_name: str,
        settings: ProcessingSettings,
        reporter: StepReporter | None,
    ) -> SampleFiles:
        """Construct and process a modality, returning its existing file map."""
        ...


@dataclass
class ModalityHandler(Generic[SampleT, DatasetT, SettingsT]):
    """Compose typed factories while retaining their original execution order."""

    create_samples: Callable[[str, list[str], str, ProcessingSettings], list[SampleT]]
    create_dataset: Callable[[str, list[SampleT], ProcessingSettings], DatasetT]
    extract_settings: Callable[[ProcessingSettings], SettingsT]
    execute: Callable[[DatasetT, SettingsT, StepReporter | None], SampleFiles]

    def preprocess(
        self,
        path: str,
        sample_ids: list[str],
        modality_name: str,
        settings: ProcessingSettings,
        reporter: StepReporter | None,
    ) -> SampleFiles:
        """Create samples and dataset, then extract settings and execute processing."""
        samples = self.create_samples(path, sample_ids, modality_name, settings)
        dataset = self.create_dataset(path, samples, settings)
        parameters = self.extract_settings(settings)
        return self.execute(dataset, parameters, reporter)


_MODALITY_REGISTRY: dict[str, Preprocessor] = {}


def register_modality(modality_type: str, handler: Preprocessor) -> None:
    """Associate a modality key with its preprocessing operation."""
    _MODALITY_REGISTRY[modality_type] = handler
