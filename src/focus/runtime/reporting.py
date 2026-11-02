"""Serialize stage/sample progress, console messages and GUI activity operations."""

from __future__ import annotations
from focus.runtime.items import sample_id
from focus.runtime.snapshot import progress_snapshot
from focus.runtime.steps import new_step
from focus.runtime.activity import log_activity
from collections.abc import Sized
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar, Unpack, cast
from focus.runtime.types import (
    Activity,
    ActivityLine,
    LineFields,
    ProgressCallback,
    Status,
    Step,
)


import logging
import threading
import time
from contextlib import contextmanager

import tqdm as _tqdm_lib
from focus.runtime.cancellation import CancelToken, PipelineCancelled
from focus.runtime.activity import format_step_label


T = TypeVar("T")


class StepReporter:
    """Single reporting interface: console + focus.log via the ``focus`` logger, GUI via ``callback``.

    The orchestrator opens the context with ``stage()`` and ``modality()``;
    modules report ``step()`` / ``tqdm()`` / ``samples()`` and attach
    ``detail()`` / ``warning()`` lines. Calls are serialized by a re-entrant
    lock (alignment reports from its own thread) and the callback runs under it,
    so lines reach the GUI in order.
    """

    def __init__(
        self,
        callback: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> None:
        """Create a reporter with optional GUI callback and cancellation token.

        Parameters
        ----------
        callback : ProgressCallback, optional
            Receives status snapshots under the reporter's re-entrant lock.
        cancel : CancelToken, optional
            Existing token checked at reporting boundaries; no run is started here."""
        self._callback = callback
        self._cancel = cancel
        self._logger = logging.getLogger("focus")
        self._lock = threading.RLock()
        self._next_id = 1
        self._stage: str | None = None
        self._stage_index = 0
        self._total_stages = 0
        self._modality: str | None = None
        self._modality_index = 0
        self._total_modalities = 0
        self._sample: str | None = None
        self._sample_index = 0
        self._total_samples = 0
        # Open step: name, index, total, unit, items_total, progress, cached, opened_at, announced.
        self._step: Step | None = None
        # The context changed and no line names it yet.
        self._dirty = False
        self._last_line_id: int | None = None
        self._message = ""
        # Activity operations waiting for the next callback.
        self._ops: list[Activity] = []

    # ── context ───────────────────────────────────────────────────────────

    @contextmanager
    def stage(self, stage_id: str, index: int, total: int) -> Iterator[StepReporter]:
        """Run a pipeline stage; the open step closes when the stage ends normally."""
        with self._lock:
            self._close_step()
            self._stage, self._stage_index, self._total_stages = stage_id, index, total
            self._set_modality(None, 0, 0)
            self._set_sample(None, 0, 0)
            self._dirty = True
            self._send(extra={"state": "running"})
        yield self
        with self._lock:
            self._close_step()
            self._send()

    @contextmanager
    def modality(self, name: str, index: int, total: int) -> Iterator[StepReporter]:
        """Process one modality of the current stage."""
        with self._lock:
            self._close_step()
            self._set_modality(name, index, total)
            self._set_sample(None, 0, 0)
            self._dirty = True
            self._send()
        yield self
        with self._lock:
            self._close_step()
            self._set_modality(None, 0, 0)
            self._send()

    def samples(
        self, items: Iterable[T], id_of: Callable[[T], str | None] | None = None
    ) -> Iterator[T]:
        """Iterate samples whose steps are reported inside the loop body.

        Each item becomes the current sample (its open step closes first); the
        sample is cleared when the loop ends, also on error or ``break``.
        """
        items = list(items)
        n = len(items)
        try:
            for i, item in enumerate(items, 1):
                with self._lock:
                    self._close_step()
                    self._set_sample(sample_id(item, id_of), i, n)
                    self._dirty = True
                    self._send()
                yield item
            with self._lock:
                self._close_step()
        finally:
            with self._lock:
                self._set_sample(None, 0, 0)
                self._send()

    def set_state(self, state: str, **extra: object) -> None:
        """Change the run state (e.g. ``alignment_waiting``) without a line."""
        with self._lock:
            self._send(extra=cast(Status, {"state": state, **extra}))

    # ── steps ─────────────────────────────────────────────────────────────

    def step(
        self,
        index: int,
        total: int,
        name: str,
        *,
        unit: str | None = None,
        items_total: int = 0,
        cached: bool = False,
    ) -> None:
        """Start step ``index/total``; repeated calls with the same step only update progress."""
        with self._lock:
            s = self._step
            if (
                s
                and (s["index"], s["total"], s["name"]) == (index, total, name)
                and not self._dirty
            ):
                s.update({"unit": unit, "items_total": items_total})
                if cached:
                    self.cached()
                else:
                    self._send()
                return
            self._open_step(index, total, name, unit, items_total, cached)
            self._add_line("step")
            self._send()

    def tqdm(
        self,
        iterable: Iterable[T],
        step_index: int,
        step_total: int,
        name: str,
        *,
        unit: str | None = None,
        id_of: Callable[[T], str | None] | None = None,
        **tqdm_kwargs: Any,
    ) -> Iterator[T]:
        """Iterate with a console progress bar while reporting step ``step_index/step_total``.

        With ``unit="sample"`` each item becomes the current sample and gets its
        own line under the same step; the sample is cleared after the loop. Other
        units (tile, channel, patch) only report item progress. ``total=`` keeps
        its tqdm meaning: the number of items, for iterables without a length.
        """
        n = tqdm_kwargs.pop("total", None)
        if n is None and hasattr(iterable, "__len__"):
            n = len(cast(Sized, iterable))
        n = n or 0
        per_sample = unit == "sample"
        with self._lock:
            self._open_step(step_index, step_total, name, unit, n, False)
            if not per_sample:
                self._add_line("step")
            self._send()
        label = format_step_label(step_index, step_total, name)
        try:
            for i, item in enumerate(
                _tqdm_lib.tqdm(
                    iterable,
                    desc=label,
                    total=n or None,
                    unit=unit or "it",
                    **tqdm_kwargs,
                )
            ):
                with self._lock:
                    if per_sample:
                        self._set_sample(sample_id(item, id_of), i + 1, n)
                        cast(Step, self._step)["progress"] = i
                        self._add_line("step")
                    self._send()
                yield item
                with self._lock:
                    if self._step:
                        self._step["progress"] = i + 1
                    self._send()
        finally:
            if per_sample:
                with self._lock:
                    # Back to the step's own context, already announced: no new line needed.
                    self._set_sample(None, 0, 0)
                    self._send()

    def update(self, current: int, total: int | None = None) -> None:
        """Item progress of the open step (no line)."""
        with self._lock:
            if self._step:
                self._step["progress"] = current
                if total is not None:
                    self._step["items_total"] = total
            self._send()

    def cached(self) -> None:
        """Mark the current context as served from cache (no 'done' line follows)."""
        with self._lock:
            s = self._step
            # A step spanning samples is not cached as a whole when one sample is.
            if s and s["unit"] != "sample":
                s["cached"] = True
            if self._dirty or self._last_line_id is None:
                self._add_line("step", cached=True)
            else:
                self._send_ops(
                    [{"op": "patch", "id": self._last_line_id, "cached": True}]
                )
                log_activity(self._logger, "detail", "cached result reused")
                return
            self._send()

    # ── free text ─────────────────────────────────────────────────────────

    def detail(self, msg: str) -> None:
        """Attach an informative line to the current context."""
        self._text("detail", msg)

    def warning(self, msg: str) -> None:
        """Attach a warning line to the current context."""
        self._text("warning", msg)

    def finish(self) -> None:
        """Close the open step at the end of the run."""
        with self._lock:
            self._close_step()
            self._send(check=False)

    def fail(self, exc: BaseException) -> None:
        """Report the error in the current context; the failed step gets no 'done' line."""
        with self._lock:
            self._step = None
            self._add_line("error", text=f"{type(exc).__name__}: {exc}")
            self._send(check=False)

    def check_cancelled(self) -> None:
        """Raise ``PipelineCancelled`` if the run was interrupted (every progress call checks this)."""
        if self._cancel is not None and self._cancel.is_set():
            raise PipelineCancelled()

    def on_cancel(self, hook: Callable[[], None]) -> None:
        """Call ``hook`` when the run is interrupted (at once if it already was)."""
        if self._cancel is not None:
            self._cancel.add_hook(hook)

    def off_cancel(self, hook: Callable[[], None]) -> None:
        """Detach a cleanup hook from this reporter's cancellation token."""
        if self._cancel is not None:
            self._cancel.remove_hook(hook)

    def cancelled(self) -> None:
        """Report the interruption in the current context; the open step gets no 'done' line."""
        with self._lock:
            self._step = None
            self._add_line("warning", text="Run interrupted by the user")
            self._send(check=False)

    # ── internals (lock held) ─────────────────────────────────────────────

    def _text(self, kind: str, msg: str) -> None:
        """Emit an activity message in the current reporting context."""
        with self._lock:
            if self._dirty:
                self._add_line("step")
            self._add_line(kind, text=msg)
            self._send()

    def _set_modality(self, name: str | None, index: int, total: int) -> None:
        """Update the modality context and its ordered progress counters."""
        self._modality, self._modality_index, self._total_modalities = (
            name,
            index,
            total,
        )

    def _set_sample(self, sample: str | None, index: int, total: int) -> None:
        """Update the sample context and its ordered progress counters."""
        self._sample, self._sample_index, self._total_samples = sample, index, total

    def _open_step(
        self,
        index: int,
        total: int,
        name: str,
        unit: str | None,
        items_total: int,
        cached: bool,
    ) -> None:
        """Close the previous step and create new counters with an opening timestamp."""
        self._close_step()
        self._step = new_step(index, total, name, unit, items_total, cached)

    def _close_step(self) -> None:
        """Close an announced uncached step and emit its elapsed time."""
        s = self._step
        self._step = None
        if s and s["announced"] and not s["cached"]:
            # A step spanning samples ends at the level of its modality, not of its last sample.
            sample = None if s["unit"] == "sample" else s["sample"]
            self._add_line(
                "done", step=s, sample=sample, seconds=time.time() - s["opened_at"]
            )

    def _add_line(
        self,
        kind: str,
        *,
        step: Step | None = None,
        sample: str | None = None,
        **fields: Unpack[LineFields],
    ) -> None:
        """Create a line in the current context (``step``/``sample`` override it for 'done')."""
        s = step if step is not None else self._step
        entry: ActivityLine = {
            "id": self._next_id,
            "t": time.time(),
            "kind": kind,
            "stage": self._stage,
            "modality": self._modality,
            "sample": sample if step is not None else self._sample,
            "step": s["name"] if s else None,
            "step_index": s["index"] if s else 0,
            "step_total": s["total"] if s else 0,
            "cached": bool(
                fields.pop("cached", False) or (s and s["cached"] and kind == "step")
            ),
            **fields,
        }
        self._next_id += 1
        if kind == "step":
            self._dirty = False
            self._last_line_id = entry["id"]
            if s:
                s["announced"] = True
                s["sample"] = self._sample
        self._message = entry.get("text") or entry["step"] or self._message
        log_activity(self._logger, kind, entry=entry)
        self._ops.append({"op": "add", **entry})

    def _send_ops(self, ops: list[Activity]) -> None:
        """Queue ordered activity operations and send the current progress snapshot."""
        self._ops.extend(ops)
        self._send()

    def _send(self, extra: Status | None = None, *, check: bool = True) -> None:
        """Send the full context plus any pending activity operations to the GUI.

        Every progress call lands here, so it is also where an interrupt is noticed;
        cleanup paths pass ``check=False`` so they never raise while unwinding.
        """
        if check:
            self.check_cancelled()
        ops, self._ops = self._ops, []
        if not self._callback:
            return
        status = progress_snapshot(self, extra)
        if ops:
            status["activity"] = ops
        self._callback(status)
