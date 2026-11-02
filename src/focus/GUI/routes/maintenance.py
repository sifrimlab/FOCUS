"""Maintenance HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from focus.gui.status import _default_status
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import os

from flask import jsonify


from focus.gui.progress_tracker import ProgressTracker


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Cleanup temporary files ---

    @gui.app.route("/api/cleanup", methods=["POST"])
    def cleanup_temp_files() -> ResponseReturnValue:
        """Remove pipeline temporary files using the established cleanup rules."""
        files_struct = gui._pipeline_status.get("output_files", {})
        deleted = 0
        for section_key, section in files_struct.items():
            if section_key == "multimodal":
                continue  # Never touch the final merged output
            per_modality = section.get("per_modality", {})
            for paths in per_modality.values():
                for path in paths:
                    try:
                        if os.path.isfile(path):
                            os.remove(path)
                            deleted += 1
                    except OSError:
                        pass
            section["per_modality"] = {}
        return jsonify({"deleted": deleted})

    # --- Reset ---

    @gui.app.route("/api/reset", methods=["POST"])
    def reset() -> ResponseReturnValue:
        """Reset GUI run state through the existing maintenance endpoint."""
        gui._config = {}
        with gui._status_lock:
            gui._pipeline_status = _default_status()
            gui._progress = ProgressTracker()
        # Drop the finished worker-thread reference and reclaim memory so starting a new
        # project begins from a clean slate. Only clear the thread when it isn't running,
        # so a reset during an active run can't orphan the running pipeline.
        if gui._pipeline_thread is not None and not gui._pipeline_thread.is_alive():
            gui._pipeline_thread = None
        try:
            from focus.runtime.resources import release_memory

            release_memory()
        except Exception:
            pass
        return jsonify({"message": "Reset complete"})
