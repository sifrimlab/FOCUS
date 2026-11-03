"""Main-GUI application state, pipeline worker coordination and server lifetime."""

from focus.gui.headers import cors_response

from focus.config.types import PipelineConfig
from focus.runtime.types import Status
from werkzeug.serving import BaseWSGIServer

from focus.gui.settings import _CONFIG_FILENAME

from focus.gui.routes import register_routes
import os
import json
import logging
import threading
import traceback
from datetime import datetime, timezone

from flask import Flask
from werkzeug.serving import make_server

from focus.config.keys import ConfigParameters
from focus.gui.progress_tracker import ProgressTracker
from focus.runtime.cancellation import CancelToken, PipelineCancelled
from focus.gui.status import _default_status


class MainGUI:
    """
    Main FOCUS GUI backend.

    Serves a Vue.js frontend for interactive config building and pipeline monitoring.
    Runs on localhost:5050 by default (see start()).
    """

    def __init__(self, debug: bool = False) -> None:
        """Create run state and HTTP routes, retaining the existing werkzeug logging policy.

        Parameters
        ----------
        debug : bool
            Enable werkzeug request logging when true. Server binding starts in start()."""
        self._debug = debug
        self._config: PipelineConfig = {}
        self._pipeline_thread: threading.Thread | None = None
        self._pipeline_status: Status = _default_status()
        self._progress = ProgressTracker()
        # Interrupt request of the current run (a new token per run).
        self._cancel = CancelToken()
        # The pipeline thread writes the status while request threads read it.
        self._status_lock = threading.Lock()
        self._basedir = os.path.join(os.path.dirname(__file__), "main")

        # Silence werkzeug HTTP request logs unless debug mode is requested.
        # Must be set here, before the server starts receiving requests.
        logging.getLogger("werkzeug").setLevel(
            logging.DEBUG if debug else logging.WARNING
        )

        self.app = Flask(__name__)

        self.app.after_request(cors_response)

        self._server: BaseWSGIServer | None = None
        register_routes(self)

    # ── Pipeline thread ───────────────────────────────────────────────────

    def _run_pipeline_thread(self) -> None:
        """Run the configured pipeline and update GUI completion or error state."""
        try:
            import focus.config.validation as validation
            import focus.pipeline.runner as pipeline

            # Apply defaults via parse_config
            validated = validation.parse_config(self._config)
            self._config = validated

            output_files = pipeline.run(
                validated,
                progress_callback=self._on_progress,
                cancel=self._cancel,
            )

            with self._status_lock:
                self._pipeline_status["state"] = "completed"
                self._pipeline_status["output_files"] = output_files
                self._progress.observe(self._pipeline_status)

        except PipelineCancelled:
            with self._status_lock:
                self._pipeline_status["state"] = "cancelled"
                self._progress.observe(self._pipeline_status)

        except Exception as e:
            with self._status_lock:
                self._pipeline_status["state"] = "error"
                self._pipeline_status["error"] = str(e)
                self._progress.observe(self._pipeline_status)
            traceback.print_exc()

        finally:
            # Reclaim everything the run allocated the instant the worker thread ends.
            # The orchestrator's stage locals are now unreferenced; force collection of the
            # cyclic garbage AnnData/MuData/torch leave behind and return the freed pages to
            # the OS, so "start a new project" begins from a clean slate. Best-effort: cleanup
            # must never mask a pipeline error or crash the thread.
            try:
                from focus.runtime.resources import release_memory

                release_memory()
            except Exception:
                pass

    def _on_progress(self, status: Status) -> None:
        """Callback from the reporter: full current context plus new activity operations."""
        ops = status.pop("activity", None)
        if self._cancel.is_set():
            status.pop("state", None)  # never overwrite "cancelling"
        with self._status_lock:
            self._pipeline_status.update(status)
            self._progress.observe(self._pipeline_status)
            self._progress.record(ops)

    def _status_payload(self) -> dict[str, object]:
        """Current status plus the run history (timing, samples seen, activity)."""
        with self._status_lock:
            return {**self._pipeline_status, **self._progress.snapshot()}

    # ── Auto-save ─────────────────────────────────────────────────────────

    def _auto_save(self) -> None:
        """Save current config to focus_config.json in dataset_path."""
        dataset_path = self._config.get(ConfigParameters.DATASET_PATH, "")
        if not dataset_path or not os.path.isdir(dataset_path):
            return
        try:
            self._config[ConfigParameters.LAST_EDIT] = datetime.now(
                timezone.utc
            ).isoformat()
            config_path = os.path.join(dataset_path, _CONFIG_FILENAME)
            with open(config_path, "w") as f:
                json.dump(self._config, f, indent=2)
        except Exception:
            pass  # Best-effort auto-save

    # ── Server lifecycle ──────────────────────────────────────────────────

    def start(self, port: int = 5050) -> None:
        """Serve the GUI until shutdown, then release the server resources."""
        try:
            self._server = make_server("localhost", port, self.app)
        except OSError as e:
            print(f"ERROR: Could not start server on port {port}: {e}")
            print("Try a different port or free the current one.")
            return
        print(f"FOCUS GUI started. Open http://localhost:{port} in your browser.")
        self._server.serve_forever()
