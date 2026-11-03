"""HTTP routes for manual alignment: assets, status, modality payloads and confirmation."""

from __future__ import annotations

from io import BytesIO
from typing import TYPE_CHECKING
from PIL import Image
from flask import Response, jsonify, request, send_file, send_from_directory
from flask.typing import ResponseReturnValue

if TYPE_CHECKING:
    from focus.gui.alignment import DirectMappingAlignmentGUI


def _requested_channels() -> list[int] | None:
    """Channel indices of ``?channels=0,2`` (empty string: none shown); None without the query."""
    raw = request.args.get("channels")
    if raw is None:
        return None
    return [int(v) for v in raw.split(",") if v.strip().lstrip("-").isdigit()]


def _image_response(payload: object) -> ResponseReturnValue:
    """PNG of an image payload; an ImageView renders the requested channels first."""
    # Imported here: focus.alignment imports the GUI package at import time.
    from focus.alignment.image_view import ImageView

    if isinstance(payload, ImageView):
        payload = payload.image(_requested_channels())
    if not isinstance(payload, Image.Image):
        return jsonify({"message": "Invalid payload type for IMAGE"}), 500
    img_io = BytesIO()
    payload.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


def register_routes(gui: DirectMappingAlignmentGUI) -> None:
    """Register the GUI endpoints in their established declaration order."""

    @gui.app.route("/")
    def index() -> ResponseReturnValue:
        """Serve the compiled GUI entry document."""
        return send_from_directory(gui._basedir, "index.html")

    @gui.app.route("/<path:filename>")
    def serve_static(filename: str) -> ResponseReturnValue:
        """Serve a bundled GUI asset by its requested path."""
        return send_from_directory(gui._basedir, filename)

    @gui.app.route("/assets/<path:path>")
    def static_assets(path: str) -> ResponseReturnValue:
        """Serve a compiled alignment asset from the existing asset directory."""
        return send_from_directory(gui._basedir, path)

    @gui.app.route("/status", methods=["GET"])
    def get_status() -> ResponseReturnValue:
        """Return the current GUI status with its existing JSON fields."""
        if gui._error_message is not None:
            return jsonify({"error": gui._error_message}), 500
        if gui._dataset_completed_event.is_set():
            return jsonify({"message": "No more samples available"}), 404
        if gui._sample_id is None:
            return jsonify({"message": "Sample not ready"}), 400
        return jsonify(
            {
                "sample_id": gui._sample_id,
                "sample_index": gui._sample_index,
                "total_samples_count": gui._dataset_size,
            }
        )

    @gui.app.route("/<type>/metadata", methods=["GET"])
    def get_metadata(type: str) -> ResponseReturnValue:
        """Return alignment metadata for the requested modality."""
        if type == "reference":
            metadata = gui._reference_metadata
        elif type == "target":
            metadata = gui._target_metadata
        else:
            return jsonify({"message": "Invalid type"}), 400
        if metadata is None:
            return jsonify({"message": "Metadata not set"}), 404
        return jsonify(metadata)

    @gui.app.route("/<type>/payload", methods=["GET"])
    def get_payload(type: str) -> ResponseReturnValue:
        """Return the original encoded image or spot payload for the requested modality."""
        if type == "reference":
            payload = gui._reference_payload
            metadata = gui._reference_metadata
        elif type == "target":
            payload = gui._target_payload
            metadata = gui._target_metadata
        else:
            return jsonify({"message": "Invalid type"}), 400
        if payload is None or metadata is None:
            return jsonify({"message": "Payload not set"}), 404
        modality_type = metadata.get("modality_type")
        if modality_type == "IMAGE":
            return _image_response(payload)
        elif modality_type == "SPOT":
            if isinstance(payload, bytes):
                return Response(payload, mimetype="application/octet-stream")
            return jsonify(payload)
        else:
            return jsonify({"message": f"Unknown modality type: {modality_type}"}), 500

    @gui.app.route("/confirm", methods=["POST"])
    def confirm_alignment() -> ResponseReturnValue:
        """Accept the browser transformation and signal the waiting alignment worker."""
        data = request.get_json()
        if not data:
            return jsonify({"message": "No data provided"}), 400
        gui._aligned_target = data
        gui._user_event.set()
        return jsonify({"message": "Alignment confirmed successfully"})
