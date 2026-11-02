"""Status HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from focus.gui.settings import _CONFIG_FILENAME
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import os

from flask import jsonify

from focus.config.keys import ConfigParameters
from focus.io.discovery import discover_sample_ids


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    @gui.app.route("/api/status", methods=["GET"])
    def get_status() -> ResponseReturnValue:
        """Return the current GUI status with its existing JSON fields."""
        return jsonify(gui._status_payload())

    # --- Full state (for restoring the frontend on reload) ---

    @gui.app.route("/api/state", methods=["GET"])
    def get_state() -> ResponseReturnValue:
        """Return the full backend state so the frontend can restore its view."""
        dataset_path = gui._config.get(ConfigParameters.DATASET_PATH, "")
        samples = []
        has_existing_config = False
        if dataset_path and os.path.isdir(dataset_path):
            try:
                samples = discover_sample_ids(dataset_path)
                has_existing_config = os.path.isfile(
                    os.path.join(dataset_path, _CONFIG_FILENAME)
                )
            except Exception:
                pass
        return jsonify(
            {
                "config": gui._config,
                "status": gui._status_payload(),
                "samples": samples,
                "has_existing_config": has_existing_config,
            }
        )
