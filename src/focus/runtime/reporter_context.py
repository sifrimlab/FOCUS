"""Bind a reporter to the active thread-local pipeline execution context."""

from __future__ import annotations

import threading

from focus.runtime.reporting import StepReporter


# ── active reporter (for code without a reporter handle) ──────────────────

_active_lock = threading.Lock()

_active: StepReporter | None = None


def use_reporter(reporter: StepReporter | None) -> None:
    """Make ``reporter`` the one returned by ``get_reporter()`` (None resets it)."""
    global _active
    with _active_lock:
        _active = reporter


def get_reporter() -> StepReporter:
    """The reporter of the running pipeline, or a console-only one outside a run."""
    with _active_lock:
        return _active if _active is not None else StepReporter()
