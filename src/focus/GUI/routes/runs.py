"""Runs HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from focus.gui.status import _default_status
from focus.gui.validation import _validate_config_safe
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import threading

from flask import jsonify

from focus.config.keys import ConfigParameters
from focus.io.discovery import discover_sample_ids
from focus.runtime.cancellation import CancelToken


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Pipeline execution ---

    @gui.app.route("/api/run", methods=["POST"])
    def run_pipeline() -> ResponseReturnValue:
        """Validate run state and start the pipeline worker thread."""
        if gui._pipeline_thread and gui._pipeline_thread.is_alive():
            return jsonify({"error": "Pipeline already running"}), 409

        # Validate before running
        errors = _validate_config_safe(gui._config)
        if errors:
            return jsonify({"valid": False, "errors": errors}), 400

        # Set up logging
        import focus.runtime.logging as runtime_logging

        dataset_path = gui._config[ConfigParameters.DATASET_PATH]
        runtime_logging.setup_logging(dataset_path, debug=gui._debug)

        # Record which samples will be processed in this run
        ignore_samples = gui._config.get(ConfigParameters.IGNORE_SAMPLES, [])
        gui._config[ConfigParameters.SAMPLES] = discover_sample_ids(
            dataset_path, ignore_samples
        )
        gui._auto_save()

        # Reset status
        with gui._status_lock:
            gui._pipeline_status = _default_status()
            gui._pipeline_status["state"] = "running"
            gui._progress.reset()
            gui._cancel = CancelToken()

        # Run in background thread
        gui._pipeline_thread = threading.Thread(
            target=gui._run_pipeline_thread,
            daemon=True,
        )
        gui._pipeline_thread.start()
        return jsonify({"started": True})

    @gui.app.route("/api/cancel", methods=["POST"])
    def cancel_pipeline() -> ResponseReturnValue:
        """Ask the running pipeline to stop at its next progress call."""
        with gui._status_lock:
            alive = gui._pipeline_thread is not None and gui._pipeline_thread.is_alive()
            if not alive or gui._pipeline_status["state"] not in (
                "running",
                "alignment_waiting",
            ):
                return jsonify({"error": "No pipeline is running"}), 409
            gui._pipeline_status["state"] = "cancelling"
            token = gui._cancel
        # Outside the lock: the hooks close the alignment tool, which can take a moment.
        token.cancel()
        return jsonify({"cancelling": True})
