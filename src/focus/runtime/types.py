"""Progress dictionaries exchanged by the pipeline and GUI backend."""

from collections.abc import Callable
from typing import TypeAlias, TypedDict

from focus.pipeline.types import PipelineFiles


class Step(TypedDict, total=False):
    """Mutable state of one open step; times are UNIX seconds."""

    index: int
    total: int
    name: str
    unit: str | None
    items_total: int
    progress: int
    cached: bool
    opened_at: float
    announced: bool
    sample: str | None


class LineFields(TypedDict, total=False):
    """Additional fields accepted when an activity line is created."""

    text: str | None
    seconds: float
    cached: bool


class ActivityLine(LineFields, total=False):
    """Content of one activity line, before transport operations are attached."""

    id: int
    t: float
    kind: str
    stage: str | None
    modality: str | None
    sample: str | None
    step: str | None
    step_index: int
    step_total: int


class Activity(ActivityLine, total=False):
    """An activity line or a patch applied to an existing line."""

    op: str


class Status(TypedDict, total=False):
    """Partial status updates; the GUI merges them into its current snapshot."""

    state: str
    alignment_port: int
    error: str | None
    stage: str | None
    stage_index: int
    total_stages: int
    current_modality: str | None
    current_modality_index: int
    total_modalities: int
    current_sample: str | None
    current_sample_index: int
    total_samples: int
    sub_step: str | None
    sub_step_index: int
    sub_step_total: int
    sub_step_progress: int
    sub_step_items_total: int
    sub_step_unit: str | None
    message: str
    activity: list[Activity]
    output_files: PipelineFiles


ProgressCallback: TypeAlias = Callable[[Status], None]


class ModalityTiming(TypedDict):
    """Timing and observed samples for one modality."""

    name: str
    started_at: float
    ended_at: float | None
    samples: list[str]


class StageTiming(TypedDict):
    """Timing, failure state, and modality history for one stage."""

    stage: str
    started_at: float
    ended_at: float | None
    failed: bool
    modalities: list[ModalityTiming]
    samples: list[str]


class ProgressHistory(TypedDict):
    """History fields added to the status response."""

    run_started_at: float | None
    server_now: float
    timeline: list[StageTiming]
    activity: list[Activity]
