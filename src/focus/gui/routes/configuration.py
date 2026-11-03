"""Configuration HTTP routes for the main GUI."""

from __future__ import annotations
from typing import cast
from focus.config.types import PipelineConfig
from flask.typing import ResponseReturnValue
from focus.gui.settings import _CONFIG_FILENAME
from focus.gui.validation import _validate_config_safe
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import os
import json

from flask import request, jsonify

from focus.config.keys import ConfigParameters


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Config CRUD ---

    @gui.app.route("/api/config", methods=["GET"])
    def get_config() -> ResponseReturnValue:
        """Return the current configuration as JSON."""
        return jsonify(gui._config)

    @gui.app.route("/api/config", methods=["PUT"])
    def put_config() -> ResponseReturnValue:
        """Replace the current configuration and persist it through the existing autosave path."""
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON body"}), 400
        gui._config = cast(PipelineConfig, data)
        gui._auto_save()
        return jsonify(gui._config)

    @gui.app.route("/api/config/load", methods=["POST"])
    def load_config() -> ResponseReturnValue:
        """Read a chosen configuration file and return its existing HTTP contract."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400

        # Load from path or from inline content
        if "path" in data:
            try:
                with open(data["path"], "r") as f:
                    loaded = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                return jsonify({"valid": False, "errors": [str(e)]})
        elif "content" in data:
            try:
                loaded = (
                    json.loads(data["content"])
                    if isinstance(data["content"], str)
                    else data["content"]
                )
            except json.JSONDecodeError as e:
                return jsonify({"valid": False, "errors": [str(e)]})
        else:
            return jsonify({"error": "Provide 'path' or 'content'"}), 400

        # Validate
        errors = _validate_config_safe(loaded)
        if errors:
            return jsonify({"valid": False, "errors": errors})

        gui._config = loaded
        gui._auto_save()
        return jsonify({"valid": True, "config": loaded})

    @gui.app.route("/api/config/load-existing", methods=["POST"])
    def load_existing_config() -> ResponseReturnValue:
        """Load the focus_config.json from the dataset_path.

        Accepts an optional 'dataset_path' in the request body; falls back to
        the in-memory config if not provided.  Never writes to disk — callers
        must not call PUT /api/config before this endpoint.

        Always stores the loaded config so the user can edit it.  Validation
        errors are returned alongside the config so the frontend can display
        them immediately without blocking the user from fixing them.
        """
        data = request.get_json() or {}
        dataset_path = data.get("dataset_path") or gui._config.get(
            ConfigParameters.DATASET_PATH, ""
        )
        if not dataset_path:
            return jsonify({"error": "dataset_path not set"}), 400

        config_path = os.path.join(dataset_path, _CONFIG_FILENAME)
        if not os.path.isfile(config_path):
            return jsonify({"error": "No existing config file found"}), 404

        try:
            with open(config_path, "r") as f:
                loaded = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            # File exists but cannot be parsed — report as corrupted so the
            # frontend can ask the user before overwriting it.
            return jsonify({"valid": False, "corrupted": True, "errors": [str(e)]})

        # Store the config regardless of validation outcome so the user can
        # see and fix it in the editor without restarting the backend.
        gui._config = loaded
        errors = _validate_config_safe(loaded)
        return jsonify({"valid": len(errors) == 0, "config": loaded, "errors": errors})

    # --- Validation ---

    @gui.app.route("/api/validate", methods=["POST"])
    def validate_config() -> ResponseReturnValue:
        """Validate the supplied configuration and report errors through the existing HTTP contract."""
        errors = _validate_config_safe(gui._config)
        if errors:
            return jsonify({"valid": False, "errors": errors})
        return jsonify({"valid": True, "config": gui._config})
