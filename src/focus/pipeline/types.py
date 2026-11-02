"""File maps shared by pipeline stages without changing their wire format."""

from typing import TypeAlias, TypedDict

SampleFiles: TypeAlias = dict[str, str]
ModalityFiles: TypeAlias = dict[str, SampleFiles]


class StageFiles(TypedDict, total=False):
    """Merged and per-modality output paths for a completed stage."""

    merged: list[str]
    per_modality: dict[str, list[str]]


PipelineFiles: TypeAlias = dict[str, StageFiles]
