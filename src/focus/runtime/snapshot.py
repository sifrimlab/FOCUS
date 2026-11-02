"""Construct progress snapshots from the reporter's current context."""

from typing import Protocol
from focus.runtime.types import Step, Status
from focus.runtime.activity import format_step_label


class ReporterContext(Protocol):
    """Only the context fields needed to render the existing status dictionary."""

    _stage: str | None
    _stage_index: int
    _total_stages: int
    _modality: str | None
    _modality_index: int
    _total_modalities: int
    _sample: str | None
    _sample_index: int
    _total_samples: int
    _step: Step | None
    _message: str


def progress_snapshot(context: ReporterContext, extra: Status | None) -> Status:
    """Copy the current progress fields, then apply the supplied status overrides."""
    s = context._step
    return {
        "stage": context._stage,
        "stage_index": context._stage_index,
        "total_stages": context._total_stages,
        "current_modality": context._modality,
        "current_modality_index": context._modality_index,
        "total_modalities": context._total_modalities,
        "current_sample": context._sample,
        "current_sample_index": context._sample_index,
        "total_samples": context._total_samples,
        "sub_step": format_step_label(s["index"], s["total"], s["name"]) if s else None,
        "sub_step_index": s["index"] if s else 0,
        "sub_step_total": s["total"] if s else 0,
        "sub_step_progress": s["progress"] if s else 0,
        "sub_step_items_total": s["items_total"] if s else 0,
        "sub_step_unit": s["unit"] if s else None,
        "message": context._message,
        **(extra or {}),
    }
