"""MainGUICancel behavioral scenarios."""

from typing import cast
from threading import Thread

from focus.runtime.types import ProgressCallback
from focus.pipeline.types import PipelineFiles
import pytest
from focus.config.types import PipelineConfig
import threading
import time
from unittest import mock

from focus.gui.server import MainGUI
from focus.runtime.cancellation import CancelToken, PipelineCancelled
from focus.runtime.reporting import StepReporter


class TestMainGUICancel:
    """Behavioral contracts for main guicancel."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.gui = MainGUI()
        self.client = self.gui.app.test_client()
        self.started = threading.Event()

    def _fake_run(
        self,
        config: PipelineConfig,
        progress_callback: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> PipelineFiles:
        """Report a bounded synthetic run and propagate cancellation after publishing its cancelled state."""
        reporter = StepReporter(callback=progress_callback, cancel=cancel)
        try:
            with reporter.stage("preprocessing", 1, 1):
                self.started.set()
                for _ in reporter.tqdm(range(1000), 1, 1, "Work", unit="sample"):
                    time.sleep(0.01)
            return {}
        except PipelineCancelled:
            reporter.cancelled()
            raise

    def _start_run(self) -> None:
        """Start the patched GUI pipeline worker and verify it reaches the synthetic work loop."""
        with (
            mock.patch("focus.pipeline.runner.run", self._fake_run),
            mock.patch("focus.config.validation.parse_config", lambda config: config),
        ):
            with self.gui._status_lock:
                self.gui._pipeline_status["state"] = "running"
                self.gui._progress.reset()
                self.gui._cancel = CancelToken()
            self.gui._pipeline_thread = threading.Thread(
                target=self.gui._run_pipeline_thread, daemon=True
            )
            self.gui._pipeline_thread.start()
            assert self.started.wait(timeout=2)

    def _wait_for_state(self, state: str, timeout: int = 3) -> None:
        """Poll the GUI status until the requested state appears, failing after the supplied timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = self.client.get("/api/status").get_json()["state"]
            if current == state:
                return
            time.sleep(0.02)
        pytest.fail(f"state never became {state!r}, last {current!r}")

    def test_cancel_when_idle_is_refused(self) -> None:
        """Verify cancel when idle is refused."""
        assert self.client.post("/api/cancel").status_code == 409

    def test_cancel_stops_run(self) -> None:
        """Verify cancel stops run."""
        self._start_run()
        response = self.client.post("/api/cancel")
        assert response.status_code == 200
        self._wait_for_state("cancelled")
        status = self.client.get("/api/status").get_json()
        assert status["error"] is None
        assert any(
            "interrupted" in (line.get("text") or "") for line in status["activity"]
        )
        cast(Thread, self.gui._pipeline_thread).join(timeout=2)
        assert not cast(Thread, self.gui._pipeline_thread).is_alive()

    def test_second_cancel_after_stop_is_refused(self) -> None:
        """Verify second cancel after stop is refused."""
        self._start_run()
        self.client.post("/api/cancel")
        self._wait_for_state("cancelled")
        cast(Thread, self.gui._pipeline_thread).join(timeout=2)
        assert self.client.post("/api/cancel").status_code == 409
