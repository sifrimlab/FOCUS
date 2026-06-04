import os, time, math, threading
from PIL import Image
from io import BytesIO

from flask import Flask, request, jsonify, send_file, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.serving import make_server


def _replace_nonfinite(obj):
    """Recursively replace non-finite floats (NaN, ±Infinity) with ``None``.

    JSON has no NaN/Infinity literals, so the result is valid JSON. Only used on
    the rare payload that actually contains a non-finite value (see
    :class:`_ValidJSONProvider`).
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _replace_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_replace_nonfinite(v) for v in obj]
    return obj


class _ValidJSONProvider(DefaultJSONProvider):
    """Flask JSON provider that never emits ``NaN``/``Infinity`` tokens.

    Flask's default provider serializes non-finite floats as the bare tokens
    ``NaN``/``Infinity``, which are *not* valid JSON. The browser client's
    ``JSON.parse`` rejects them; axios then returns the unparsed response as a
    raw string, so the SPOT ``/payload`` (which the GUI expects to be an array)
    arrives as a string and crashes the client with ``x.map is not a function``.
    Spot coordinates can legitimately be non-finite (spots without a valid
    spatial position), so every response is guarded. The clean path stays fast:
    strict serialization is attempted first and only falls back to sanitizing
    when a non-finite value is actually present.
    """

    def dumps(self, obj, **kwargs):
        try:
            return super().dumps(obj, allow_nan=False, **kwargs)
        except ValueError:
            return super().dumps(_replace_nonfinite(obj), **kwargs)


class DirectMappingAlignmentGUI:
    """
    A GUI interface that allows the user to directly map target coordinates to a reference image.

    Serves a Flask web app on localhost:8000 that displays reference and target modalities
    and collects user-provided alignment transformations sample by sample.

    Parameters
    ----------
        dataset_size: int
            The number of samples to be aligned.
        dataset_completed_event: threading.Event
            Event that signals when the alignment thread has finished processing all samples.
    """

    def __init__(self, dataset_size: int, dataset_completed_event: threading.Event):

        self._dataset_size = dataset_size       # Hold how many samples need to be processed

        # Synchronization events
        self._user_event = threading.Event()    # Event to signal when the user has saved the aligned coordinates
        self._dataset_completed_event = dataset_completed_event  # Event to signal when the dataset processing is completed

        # Sample metadata
        self._sample_id: str | None = None      # Name of the sample (sample ID)
        self._sample_index: int | None = None   # Index of the sample in the dataset

        # Modalities' metadata
        self._reference_metadata: dict | None = None  # Metadata of the reference modality
        self._target_metadata: dict | None = None     # Metadata of the target modality

        # Modalities' data (Image.Image for IMAGE modalities, list[dict] for SPOT modalities)
        self._reference_payload: list[dict] | Image.Image | None = None
        self._target_payload: list[dict] | Image.Image | None = None

        # Aligned coordinates
        self._aligned_target: dict | None = None                     # Aligned target modality data

        # Error state (set by alignment thread on crash)
        self._error_message: str | None = None

        # Reset the events
        self._user_event.clear()
        self._dataset_completed_event.clear()

        self._basedir = os.path.join(os.path.dirname(__file__), 'alignment')

        self.app = Flask(__name__)
        # Guarantee valid JSON responses (non-finite floats -> null) so the
        # browser client never receives an unparseable payload.
        self.app.json = _ValidJSONProvider(self.app)

        # Enable CORS
        @self.app.after_request
        def after_request(response):
            response.headers.add('Access-Control-Allow-Origin', '*')
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
            response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
            return response

        self._server = None
        self._register_routes()

    def set_error(self, message: str) -> None:
        """Signal that the alignment thread encountered an error. Triggers server shutdown."""
        self._error_message = message
        self._dataset_completed_event.set()

    def _register_routes(self):
        @self.app.route('/')
        def index():
            return send_from_directory(self._basedir, 'index.html')

        @self.app.route('/<path:filename>')
        def serve_static(filename):
            return send_from_directory(self._basedir, filename)

        @self.app.route('/assets/<path:path>')
        def static_assets(path):
            return send_from_directory(self._basedir, path)

        @self.app.route('/status', methods=['GET'])
        def get_status():
            if self._error_message is not None:
                return jsonify({"error": self._error_message}), 500

            if self._dataset_completed_event.is_set():
                return jsonify({"message": "No more samples available"}), 404

            if self._sample_id is None:
                return jsonify({"message": "Sample not ready"}), 400

            return jsonify({
                "sample_id": self._sample_id,
                "sample_index": self._sample_index,
                "total_samples_count": self._dataset_size
            })

        @self.app.route('/<type>/metadata', methods=['GET'])
        def get_metadata(type):
            if type == 'reference':
                metadata = self._reference_metadata
            elif type == 'target':
                metadata = self._target_metadata
            else:
                return jsonify({"message": "Invalid type"}), 400
            
            if metadata is None:
                return jsonify({"message": "Metadata not set"}), 404
                
            return jsonify(metadata)

        @self.app.route('/<type>/payload', methods=['GET'])
        def get_payload(type):
            if type == 'reference':
                payload = self._reference_payload
                metadata = self._reference_metadata
            elif type == 'target':
                payload = self._target_payload
                metadata = self._target_metadata
            else:
                return jsonify({"message": "Invalid type"}), 400

            if payload is None or metadata is None:
                return jsonify({"message": "Payload not set"}), 404

            modality_type = metadata.get('modality_type')
            
            if modality_type == 'IMAGE':
                if isinstance(payload, Image.Image):
                    img_io = BytesIO()
                    payload.save(img_io, 'PNG')
                    img_io.seek(0)
                    return send_file(img_io, mimetype='image/png')
                else:
                     return jsonify({"message": "Invalid payload type for IMAGE"}), 500
            elif modality_type == 'SPOT':
                return jsonify(payload)
            else:
                return jsonify({"message": f"Unknown modality type: {modality_type}"}), 500

        @self.app.route('/confirm', methods=['POST'])
        def confirm_alignment():
            data = request.get_json()
            if not data:
                return jsonify({"message": "No data provided"}), 400
            
            self._aligned_target = data
            self._user_event.set()
            
            return jsonify({"message": "Alignment confirmed successfully"})
        
    def align_sample(
        self,
        sample_id: str,
        sample_index: int,
        reference_metadata: dict,
        target_metadata: dict,
        reference_payload: list[dict] | Image.Image,
        target_payload: list[dict] | Image.Image,
        ) -> dict:
        """
        Align the target coordinates to the reference image for a given sample.

        Parameters
        ----------
            sample_id: str
                The sample identifier.
            sample_index: int
                The index of the sample in the dataset.
            reference_metadata: dict
                The metadata of the reference modality.
            target_metadata: dict
                The metadata of the target modality.
            reference_payload: dict | Image.Image
                The payload of the reference modality.
            target_payload: dict | Image.Image
                The payload of the target modality.

        Returns
        -------
            aligned_result: dict
                The alignment result as a dictionary.
        """

        # Set the reference image and target coordinates
        self._sample_id = sample_id
        self._sample_index = sample_index
        self._reference_metadata = reference_metadata
        self._target_metadata = target_metadata
        self._reference_payload = reference_payload
        self._target_payload = target_payload
        
        # Clear the user event
        self._user_event.clear()
        print(f"Please align the coordinates for sample '{sample_id}' using the GUI.")

        # Wait for the user to save the aligned coordinates
        self._user_event.wait()

        print(f"Aligned coordinates for sample '{sample_id}' have been saved.")
        self._sample_id = None
        self._sample_index = None
        self._reference_metadata = None
        self._target_metadata = None
        self._reference_payload = None
        self._target_payload = None

        return self._aligned_target
        
    def enable_gui(self):

        # Start a thread that will pend on the dataset completed event
        threading.Thread(target=self._disable_gui, daemon=True).start()
        
        self._server = make_server('localhost', 8000, self.app)
        self._server.serve_forever()

    def _disable_gui(self):
        
        # Wait for the dataset to be completed
        self._dataset_completed_event.wait()

        # Give the browser time to read any error screen; otherwise just drain in-flight requests
        delay = 60 if self._error_message else 2
        time.sleep(delay)

        if self._server:
            self._server.shutdown()
            self._server = None

