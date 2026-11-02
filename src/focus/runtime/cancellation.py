"""Share cancellation state and cleanup hooks between workers and GUI threads."""

from __future__ import annotations
from collections.abc import Callable

import threading


class PipelineCancelled(BaseException):
    """The user interrupted the run.

    A ``BaseException`` so the ``except Exception`` blocks that turn a failing
    sample into a warning cannot swallow it. It is raised by the reporter at
    the next progress call after the cancel event is set.
    """


class CancelToken:
    """Interrupt request shared by the GUI thread and the pipeline.

    ``cancel()`` sets the flag the reporter checks and runs the registered hooks,
    which unblock waits the reporter cannot reach (the alignment tool). A hook
    added after the cancel runs at once, so a late registration is never missed.
    """

    def __init__(self) -> None:
        """Create an unset cancellation event, lock and empty ordered cleanup-hook list."""
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._hooks: list[Callable[[], None]] = []

    def is_set(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Set cancellation once and notify the registered cleanup hooks."""
        with self._lock:
            self._event.set()
            hooks = list(self._hooks)
        for hook in hooks:
            hook()

    def add_hook(self, hook: Callable[[], None]) -> None:
        """Register a cleanup hook, invoking it immediately if already cancelled."""
        with self._lock:
            if not self._event.is_set():
                self._hooks.append(hook)
                return
        hook()

    def remove_hook(self, hook: Callable[[], None]) -> None:
        """Remove a registered cleanup hook without invoking it."""
        with self._lock:
            if hook in self._hooks:
                self._hooks.remove(hook)
