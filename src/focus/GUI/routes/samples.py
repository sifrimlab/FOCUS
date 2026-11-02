"""Samples HTTP routes for the main GUI."""

from __future__ import annotations
from flask.typing import ResponseReturnValue
from focus.gui.settings import _CONFIG_FILENAME
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.gui.server import MainGUI
import os

from flask import request, jsonify

from focus.config.keys import ConfigParameters
from focus.io.discovery import discover_sample_ids, validate_path_readable
from focus.io.samples import SampleManager, _validate_sample_id


def register_routes(gui: MainGUI) -> None:
    """Register this route group against the existing GUI state."""

    # --- Sample discovery ---

    @gui.app.route("/api/samples", methods=["GET"])
    def get_samples() -> ResponseReturnValue:
        """Discover samples for the requested dataset path."""
        path = request.args.get("path", "")
        if not path:
            return jsonify({"error": "Missing 'path' query parameter"}), 400
        try:
            validate_path_readable(path)
            samples = discover_sample_ids(path)
            # Check for existing config file
            config_path = os.path.join(path, _CONFIG_FILENAME)
            has_existing_config = os.path.isfile(config_path)
            return jsonify(
                {
                    "samples": samples,
                    "has_existing_config": has_existing_config,
                }
            )
        except (FileNotFoundError, PermissionError) as e:
            return jsonify({"error": str(e)}), 400

    # --- Sample creation ---

    @gui.app.route("/api/samples/create", methods=["POST"])
    def create_sample() -> ResponseReturnValue:
        """Create the requested sample and its declared modality directories."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400
        sample_id = data.get("sample_id", "")
        dataset_path = gui._config.get(ConfigParameters.DATASET_PATH, "")
        if not dataset_path:
            return jsonify({"error": "dataset_path not set in current config"}), 400
        try:
            _validate_sample_id(sample_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        existing = discover_sample_ids(dataset_path)
        if sample_id in existing:
            return jsonify(
                {"error": f"A sample named '{sample_id}' already exists."}
            ), 400
        try:
            modalities = gui._config.get(ConfigParameters.MODALITIES, [])
            SampleManager(dataset_path).create_sample(sample_id, modalities)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"sample_id": sample_id})

    # --- Modality folder scaffolding ---

    @gui.app.route("/api/modalities/create-folders", methods=["POST"])
    def create_modality_folders() -> ResponseReturnValue:
        """Create missing modality directories for the selected samples."""
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON body"}), 400
        modality_name = data.get("modality_name", "")
        modality_type = data.get("modality_type", "")
        if not modality_name or not modality_type:
            return jsonify(
                {"error": "modality_name and modality_type are required"}
            ), 400
        dataset_path = gui._config.get(ConfigParameters.DATASET_PATH, "")
        if not dataset_path or not os.path.isdir(dataset_path):
            return jsonify({"updated_samples": []})
        try:
            sample_ids = discover_sample_ids(dataset_path)
            SampleManager(dataset_path).ensure_modality_folders(
                sample_ids, modality_name, modality_type
            )
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"updated_samples": sample_ids})
