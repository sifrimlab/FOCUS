"""Narrow execution contracts shared by the registration strategies."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from anndata import AnnData

from focus.registration.anchor import AnchorArrays
from focus.spatial.footprint import AnchorGeometry
from focus.runtime.reporting import StepReporter


class TargetRegistration(Protocol):
    """Shared execution interface for spot and Raman target strategies."""

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """Register ordered samples while retaining this strategy's scientific reduction."""
        ...


class TargetRegistrationFactory(Protocol):
    """Construct a target strategy without broadening its runtime dependencies."""

    def __call__(self, path: str) -> TargetRegistration:
        """Bind a strategy to the supplied dataset root."""
        ...


GeometryMetadata: TypeAlias = dict[str, object]
MethodSettings: TypeAlias = Mapping[str, object]
SettingsProvider: TypeAlias = MethodSettings | Callable[[str], MethodSettings]
RegistrationComputer: TypeAlias = Callable[
    [str, AnchorArrays, AnchorGeometry], tuple[AnnData | None, GeometryMetadata]
]


@dataclass(frozen=True)
class RegistrationJob:
    """Strategy inputs and callbacks, retaining their original path-map and iterator references."""

    path: str
    samples: Iterable[str]
    anchor_files: dict[str, str]
    target_files: dict[str, str]
    anchor_name: str
    target_name: str
    registration_type: str
    settings: SettingsProvider
    compute: RegistrationComputer
    force_recomputing: bool = False
    reporter: StepReporter | None = None
