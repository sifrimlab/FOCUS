"""Format the existing hierarchical activity labels and timing suffixes."""

from __future__ import annotations
from typing import Literal
from focus.runtime.types import ActivityLine

import logging


STAGE_LABELS = {
    "preprocessing": "Preprocessing",
    "alignment": "Alignment",
    "annotation_transfer": "Annotation transfer",
    "registration": "Registration",
    "compiling": "Compiling",
}


_LEVELS = {
    "step": logging.INFO,
    "done": logging.INFO,
    "detail": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def format_step_label(index: int, total: int, name: str) -> str:
    """'3/8 - Name', the single place where step labels are built."""
    return f"{index}/{total} - {name}" if total else name


def format_activity(entry: ActivityLine) -> str:
    """Console / log rendering of an activity line (ASCII, empty segments omitted)."""
    kind = entry["kind"]
    if kind == "detail":
        return f"    - {entry['text']}"
    if kind == "warning":
        return f"    ! {entry['text']}"
    segments = []
    step = entry.get("step")
    if step:
        segments.append(
            format_step_label(
                entry.get("step_index", 0), entry.get("step_total", 0), step
            )
        )
    keys: tuple[Literal["sample", "modality"], ...] = ("sample", "modality")
    for key in keys:
        value = entry.get(key)
        if value:
            segments.append(value)
    stage = entry.get("stage")
    if stage:
        segments.append(STAGE_LABELS.get(stage, stage))
    line = " ".join(f"[{s}]" for s in segments)
    if kind == "done":
        return f"{line} done in {entry.get('seconds', 0):.1f}s"
    if kind == "error":
        return f"{line} failed: {entry.get('text', '')}".strip()
    return f"{line} (cached)" if entry.get("cached") else line


def log_activity(
    logger: logging.Logger,
    kind: str,
    text: str | None = None,
    entry: ActivityLine | None = None,
) -> None:
    """Render an activity and emit it through the logger or console fallback."""
    line = (
        format_activity(entry)
        if entry
        else format_activity({"kind": kind, "text": text})
    )
    logger.log(_LEVELS[kind], line)
    if not logger.hasHandlers():
        print(line)
