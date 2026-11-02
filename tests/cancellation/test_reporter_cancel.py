"""ReporterCancel behavioral scenarios."""

import pytest
from unittest import mock

from focus.runtime.cancellation import CancelToken, PipelineCancelled
from focus.runtime.reporting import StepReporter


class TestReporterCancel:
    """Behavioral contracts for reporter cancel."""

    def test_progress_calls_raise_once_cancelled(self) -> None:
        """Verify progress calls raise once cancelled."""
        token = CancelToken()
        reporter = StepReporter(callback=lambda status: None, cancel=token)
        reporter.step(1, 2, "Load")
        token.cancel()
        with pytest.raises(PipelineCancelled):
            reporter.update(1, 10)
        with pytest.raises(PipelineCancelled):
            reporter.step(2, 2, "Write")

    def test_loop_stops_at_next_item(self) -> None:
        """Verify loop stops at next item."""
        token = CancelToken()
        reporter = StepReporter(callback=lambda status: None, cancel=token)
        seen = []
        with pytest.raises(PipelineCancelled):
            for item in reporter.tqdm(range(10), 1, 1, "Loop", unit="sample"):
                seen.append(item)
                if item == 2:
                    token.cancel()
        assert seen == [0, 1, 2]

    def test_cleanup_paths_do_not_raise(self) -> None:
        """Verify cleanup paths do not raise."""
        token = CancelToken()
        reporter = StepReporter(callback=lambda status: None, cancel=token)
        token.cancel()
        reporter.cancelled()
        reporter.fail(RuntimeError("x"))
        reporter.finish()

    def test_not_swallowed_by_except_exception(self) -> None:
        """Verify not swallowed by except exception."""
        token = CancelToken()
        reporter = StepReporter(callback=lambda status: None, cancel=token)
        token.cancel()
        with pytest.raises(PipelineCancelled):
            try:
                reporter.detail("x")
            except Exception:
                pytest.fail("PipelineCancelled must not be an Exception")

    def test_no_token_never_cancels(self) -> None:
        """Verify no token never cancels."""
        reporter = StepReporter(callback=lambda status: None)
        reporter.step(1, 1, "Load")
        reporter.check_cancelled()

    def test_hooks_run_on_cancel_and_when_added_late(self) -> None:
        """Verify hooks run on cancel and when added late."""
        token = CancelToken()
        calls = []
        token.add_hook(lambda: calls.append("early"))
        token.cancel()
        token.add_hook(lambda: calls.append("late"))
        assert calls == ["early", "late"]

    def test_removed_hook_is_not_called(self) -> None:
        """Verify removed hook is not called."""
        token = CancelToken()
        hook = mock.Mock()
        token.add_hook(hook)
        token.remove_hook(hook)
        token.cancel()
        hook.assert_not_called()
