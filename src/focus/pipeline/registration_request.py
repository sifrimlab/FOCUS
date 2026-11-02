"""Typed input state for dispatching one modality's registration strategy."""

from dataclasses import dataclass

from focus.config.types import PipelineConfig, RegistrationSettings
from focus.pipeline.types import ModalityFiles
from focus.runtime.reporting import StepReporter


@dataclass(frozen=True)
class RegistrationRequest:
    """Existing strategy arguments, retaining the original config and path-map references."""

    kind: str
    modality: str
    reference: str
    path: str
    config: PipelineConfig
    settings: RegistrationSettings
    inputs: ModalityFiles
    aligned: ModalityFiles
    outputs: ModalityFiles
    reporter: StepReporter
    overrides: dict[str, bool] | None

    @property
    def force(self) -> bool:
        """Resolve the downstream force override at the original execution point."""
        return (self.overrides or {}).get(
            self.modality, self.settings.get("force_recomputing", False)
        )
