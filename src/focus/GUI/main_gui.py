import os, json, threading, traceback

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.serving import make_server

from focus.constants import (
	ConfigParameters, ModalityParameters, ModalityType, RegistrationType,
	REGISTRATION_COMPATIBILITY,
	AlignmentStrategy, ALIGNMENT_STRATEGY_COMPATIBILITY,
	SegmentationBackgroundColor, MsiIntensityNormalization,
	MicroscopyImageProcessingParams, MsiPreprocessingParams,
	RamanPreprocessingParams, STPreprocessingParams,
	AnnotationFileType,
)
from focus.preprocessing._utils import discover_sample_ids, validate_path_readable


_CONFIG_FILENAME = "focus_config.json"


class MainGUI:
	"""
	Main FOCUS GUI backend.

	Serves a Vue.js frontend for interactive config building and pipeline monitoring.
	Runs on localhost:5000.
	"""

	def __init__(self):
		self._config: dict = {}
		self._pipeline_thread: threading.Thread | None = None
		self._pipeline_status: dict = _default_status()
		self._basedir = os.path.join(os.path.dirname(__file__), 'main')

		self.app = Flask(__name__)

		@self.app.after_request
		def after_request(response):
			response.headers.add('Access-Control-Allow-Origin', '*')
			response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
			response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
			return response

		self._server = None
		self._register_routes()

	# ── Routes ────────────────────────────────────────────────────────────

	def _register_routes(self):

		# --- Static file serving ---

		@self.app.route('/')
		def index():
			return send_from_directory(self._basedir, 'index.html')

		@self.app.route('/<path:filename>')
		def serve_static(filename):
			return send_from_directory(self._basedir, filename)

		# --- Schema ---

		@self.app.route('/api/schema', methods=['GET'])
		def get_schema():
			return jsonify(_build_schema())

		# --- Filesystem browser ---

		@self.app.route('/api/browse', methods=['GET'])
		def browse_filesystem():
			path = request.args.get('path', '').strip()
			if not path:
				path = os.path.expanduser('~')
			path = os.path.realpath(path)
			if not os.path.isdir(path):
				return jsonify({"error": f"Not a directory: {path}"}), 400
			parent = os.path.dirname(path)
			parent = None if parent == path else parent
			entries = []
			try:
				for name in sorted(os.listdir(path), key=str.lower):
					full = os.path.join(path, name)
					try:
						if os.path.isdir(full):
							entries.append({"name": name})
					except OSError:
						pass
			except PermissionError as e:
				return jsonify({"error": str(e)}), 403
			return jsonify({"path": path, "parent": parent, "entries": entries})

		@self.app.route('/api/browse_files', methods=['GET'])
		def browse_filesystem_files():
			"""Like /api/browse but returns both directories and files."""
			path = request.args.get('path', '').strip()
			if not path:
				path = os.path.expanduser('~')
			path = os.path.realpath(path)
			if not os.path.isdir(path):
				return jsonify({"error": f"Not a directory: {path}"}), 400
			parent = os.path.dirname(path)
			parent = None if parent == path else parent
			entries = []
			try:
				for name in sorted(os.listdir(path), key=str.lower):
					full = os.path.join(path, name)
					try:
						is_dir = os.path.isdir(full)
						entries.append({"name": name, "is_dir": is_dir})
					except OSError:
						pass
			except PermissionError as e:
				return jsonify({"error": str(e)}), 403
			return jsonify({"path": path, "parent": parent, "entries": entries})

		# --- Sample discovery ---

		@self.app.route('/api/samples', methods=['GET'])
		def get_samples():
			path = request.args.get('path', '')
			if not path:
				return jsonify({"error": "Missing 'path' query parameter"}), 400
			try:
				validate_path_readable(path)
				samples = discover_sample_ids(path)
				# Check for existing config file
				config_path = os.path.join(path, _CONFIG_FILENAME)
				has_existing_config = os.path.isfile(config_path)
				return jsonify({
					"samples": samples,
					"has_existing_config": has_existing_config,
				})
			except (FileNotFoundError, PermissionError) as e:
				return jsonify({"error": str(e)}), 400

		# --- Config CRUD ---

		@self.app.route('/api/config', methods=['GET'])
		def get_config():
			return jsonify(self._config)

		@self.app.route('/api/config', methods=['PUT'])
		def put_config():
			data = request.get_json()
			if not data or not isinstance(data, dict):
				return jsonify({"error": "Invalid JSON body"}), 400
			self._config = data
			self._auto_save()
			return jsonify(self._config)

		@self.app.route('/api/config/load', methods=['POST'])
		def load_config():
			data = request.get_json()
			if not data:
				return jsonify({"error": "No JSON body"}), 400

			# Load from path or from inline content
			if "path" in data:
				try:
					with open(data["path"], 'r') as f:
						loaded = json.load(f)
				except (FileNotFoundError, json.JSONDecodeError) as e:
					return jsonify({"valid": False, "errors": [str(e)]})
			elif "content" in data:
				try:
					loaded = json.loads(data["content"]) if isinstance(data["content"], str) else data["content"]
				except json.JSONDecodeError as e:
					return jsonify({"valid": False, "errors": [str(e)]})
			else:
				return jsonify({"error": "Provide 'path' or 'content'"}), 400

			# Validate
			errors = _validate_config_safe(loaded)
			if errors:
				return jsonify({"valid": False, "errors": errors})

			self._config = loaded
			self._auto_save()
			return jsonify({"valid": True, "config": loaded})

		@self.app.route('/api/config/load-existing', methods=['POST'])
		def load_existing_config():
			"""Load the focus_config.json from the dataset_path.

			Accepts an optional 'dataset_path' in the request body; falls back to
			the in-memory config if not provided.  Never writes to disk — callers
			must not call PUT /api/config before this endpoint.
			"""
			data = request.get_json() or {}
			dataset_path = data.get('dataset_path') or self._config.get(ConfigParameters.DATASET_PATH, '')
			if not dataset_path:
				return jsonify({"error": "dataset_path not set"}), 400

			config_path = os.path.join(dataset_path, _CONFIG_FILENAME)
			if not os.path.isfile(config_path):
				return jsonify({"error": "No existing config file found"}), 404

			try:
				with open(config_path, 'r') as f:
					loaded = json.load(f)
			except (json.JSONDecodeError, OSError) as e:
				# File exists but cannot be parsed — report as corrupted so the
				# frontend can ask the user before overwriting it.
				return jsonify({"valid": False, "corrupted": True, "errors": [str(e)]})

			self._config = loaded
			return jsonify({"valid": True, "config": loaded})

		# --- Validation ---

		@self.app.route('/api/validate', methods=['POST'])
		def validate_config():
			errors = _validate_config_safe(self._config)
			if errors:
				return jsonify({"valid": False, "errors": errors})
			return jsonify({"valid": True, "config": self._config})

		# --- Pipeline execution ---

		@self.app.route('/api/run', methods=['POST'])
		def run_pipeline():
			if self._pipeline_thread and self._pipeline_thread.is_alive():
				return jsonify({"error": "Pipeline already running"}), 409

			# Validate before running
			errors = _validate_config_safe(self._config)
			if errors:
				return jsonify({"valid": False, "errors": errors}), 400

			# Set up logging
			from focus import utils
			dataset_path = self._config[ConfigParameters.DATASET_PATH]
			utils.setup_logging(dataset_path)

			# Reset status
			self._pipeline_status = _default_status()
			self._pipeline_status["state"] = "running"

			# Run in background thread
			self._pipeline_thread = threading.Thread(
				target=self._run_pipeline_thread,
				daemon=True,
			)
			self._pipeline_thread.start()
			return jsonify({"started": True})

		@self.app.route('/api/status', methods=['GET'])
		def get_status():
			return jsonify(self._pipeline_status)

		# --- Full state (for restoring the frontend on reload) ---

		@self.app.route('/api/state', methods=['GET'])
		def get_state():
			"""Return the full backend state so the frontend can restore its view."""
			dataset_path = self._config.get(ConfigParameters.DATASET_PATH, '')
			samples = []
			has_existing_config = False
			if dataset_path and os.path.isdir(dataset_path):
				try:
					samples = discover_sample_ids(dataset_path)
					has_existing_config = os.path.isfile(os.path.join(dataset_path, _CONFIG_FILENAME))
				except Exception:
					pass
			return jsonify({
				"config": self._config,
				"status": self._pipeline_status,
				"samples": samples,
				"has_existing_config": has_existing_config,
			})

		# --- Reset ---

		@self.app.route('/api/reset', methods=['POST'])
		def reset():
			self._config = {}
			self._pipeline_status = _default_status()
			return jsonify({"message": "Reset complete"})

	# ── Pipeline thread ───────────────────────────────────────────────────

	def _run_pipeline_thread(self):
		try:
			from focus import utils, orchestrator

			# Apply defaults via parse_config
			validated = utils.parse_config(self._config)
			self._config = validated

			output_files = orchestrator.run(
				validated,
				progress_callback=self._on_progress,
			)

			self._pipeline_status["state"] = "completed"
			self._pipeline_status["output_files"] = output_files
			self._pipeline_status["message"] = "Pipeline completed successfully."

		except Exception as e:
			self._pipeline_status["state"] = "error"
			self._pipeline_status["error"] = str(e)
			self._pipeline_status["message"] = f"Error: {e}"
			traceback.print_exc()

	def _on_progress(self, status: dict):
		"""Callback from orchestrator to update pipeline status."""
		self._pipeline_status.update(status)

	# ── Auto-save ─────────────────────────────────────────────────────────

	def _auto_save(self):
		"""Save current config to focus_config.json in dataset_path."""
		dataset_path = self._config.get(ConfigParameters.DATASET_PATH, '')
		if not dataset_path or not os.path.isdir(dataset_path):
			return
		try:
			config_path = os.path.join(dataset_path, _CONFIG_FILENAME)
			with open(config_path, 'w') as f:
				json.dump(self._config, f, indent=2)
		except Exception:
			pass  # Best-effort auto-save

	# ── Server lifecycle ──────────────────────────────────────────────────

	def start(self, port: int = 5050):
		try:
			self._server = make_server('localhost', port, self.app)
		except OSError as e:
			print(f"ERROR: Could not start server on port {port}: {e}")
			print("Try a different port or free the current one.")
			return
		print(f"FOCUS GUI started. Open http://localhost:{port} in your browser.")
		self._server.serve_forever()


# ── Helpers ───────────────────────────────────────────────────────────────

def _default_status() -> dict:
	return {
		"state": "idle",
		"stage": None,
		"stage_index": 0,
		"total_stages": 4,
		"current_modality": None,
		"current_modality_index": 0,
		"total_modalities": 0,
		"current_sample": None,
		"current_sample_index": 0,
		"total_samples": 0,
		"message": "",
		"error": None,
		"output_files": [],
		"alignment_port": 8000,
		"sub_step": None,
		"sub_step_index": 0,
		"sub_step_total": 0,
		"sub_step_progress": 0,
		"sub_step_items_total": 0,
	}


def _validate_config_safe(config: dict) -> list[str]:
	"""Run parse_config and return a list of error strings (empty if valid)."""
	from focus import utils
	try:
		utils.parse_config(config)
		return []
	except (TypeError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
		return [str(e)]


def _build_schema() -> dict:
	"""Build the schema dict that drives all frontend dropdowns and forms."""
	return {
		"modality_types": ModalityType.list(),
		"registration_types": RegistrationType.list(),
		"registration_compatibility": {
			rt: (compat if compat is not None else None)
			for rt, compat in REGISTRATION_COMPATIBILITY.items()
		},
		"alignment_strategies": AlignmentStrategy.list(),
		"alignment_strategy_compatibility": {
			s: (compat if compat is not None else None)
			for s, compat in ALIGNMENT_STRATEGY_COMPATIBILITY.items()
		},
		"intensity_normalization": MsiIntensityNormalization.list(),
		"background_color": SegmentationBackgroundColor.list(),
		"processing_params": {
			ModalityType.MICROSCOPY_IMAGE: {
				MicroscopyImageProcessingParams.COLOR_ENHANCEMENT: {"type": "bool", "default": True},
				MicroscopyImageProcessingParams.REMOVE_BACKGROUND: {"type": "bool", "default": True},
				MicroscopyImageProcessingParams.CROP_TO_TISSUE: {"type": "bool", "default": True},
				MicroscopyImageProcessingParams.BACKGROUND_COLOR: {
					"type": "enum",
					"options": SegmentationBackgroundColor.list(),
					"default": SegmentationBackgroundColor.WHITE,
				},
				MicroscopyImageProcessingParams.PYRAMID_LEVELS: {"type": "int", "default": 4},
				MicroscopyImageProcessingParams.MIN_OBJECT_COVERAGE: {"type": "float", "default": 0.01},
				MicroscopyImageProcessingParams.FORCE_RECOMPUTING: {"type": "bool", "default": False},
				MicroscopyImageProcessingParams.GAUSSIAN_BLUR_KERNEL_SIZE: {"type": "int", "default": 251},
				MicroscopyImageProcessingParams.MIN_OBJECT_SIZE: {"type": "int", "default": 500},
				MicroscopyImageProcessingParams.CLIP_PERCENTILE: {"type": "int", "default": 99},
				MicroscopyImageProcessingParams.CROP_MARGIN: {"type": "int", "default": 250},
				MicroscopyImageProcessingParams.GAMMA: {"type": "float", "default": 0.45},
				MicroscopyImageProcessingParams.CONTRAST_SATURATION: {"type": "float", "default": 0.35},
			},
			ModalityType.MSI: {
				MsiPreprocessingParams.LIPID_ANNOTATION_DB: {"type": "path", "default": None, "nullable": True},
				MsiPreprocessingParams.MASS_TOLERANCE: {"type": "float", "default": 10},
				MsiPreprocessingParams.FREQUENCY_THRESHOLD: {"type": "float", "default": 0.01},
				MsiPreprocessingParams.INTENSITY_NORMALIZATION: {
					"type": "enum",
					"options": MsiIntensityNormalization.list(),
					"default": MsiIntensityNormalization.NONE,
				},
				MsiPreprocessingParams.RECALIBRATION_REFERENCE: {"type": "string", "default": None, "nullable": True},
				MsiPreprocessingParams.MIN_INTENSITY_THRESHOLD: {"type": "float", "default": 1e4},
				MsiPreprocessingParams.DETECT_BACKGROUND: {"type": "bool", "default": False},
				MsiPreprocessingParams.SAMPLE_TYPE: {"type": "enum", "options": ["tissue", "microgrid"], "default": "tissue"},
				MsiPreprocessingParams.FORCE_RECOMPUTING: {"type": "bool", "default": False},
			},
			ModalityType.RAMAN: {
				RamanPreprocessingParams.FORCE_RECOMPUTING: {"type": "bool", "default": False},
				RamanPreprocessingParams.MAX_WORKERS: {"type": "int", "default": 8},
				RamanPreprocessingParams.SAVGOL_WINDOW: {"type": "int", "default": 7},
				RamanPreprocessingParams.SAVGOL_POLYORDER: {"type": "int", "default": 3},
				RamanPreprocessingParams.BG_MIN_AREA_FRACTION: {"type": "float", "default": 0.05},
				RamanPreprocessingParams.OTSU_THRESHOLD_FACTOR: {"type": "float", "default": 0.7},
				RamanPreprocessingParams.MIN_OBJECT_SIZE: {"type": "int", "default": 500},
			},
			ModalityType.ST: {
				STPreprocessingParams.MIN_COUNT_PER_SPOT: {"type": "int", "default": None, "nullable": True},
				STPreprocessingParams.MAX_COUNT_PER_SPOT: {"type": "int", "default": None, "nullable": True},
				STPreprocessingParams.MIN_GENES_PER_SPOT: {"type": "int", "default": None, "nullable": True},
				STPreprocessingParams.MAX_GENES_PER_SPOT: {"type": "int", "default": None, "nullable": True},
				STPreprocessingParams.MIN_SPOTS_PER_GENE: {"type": "float", "default": None, "nullable": True},
				STPreprocessingParams.MIN_COUNT_SPOTS_RATIO_PER_GENE: {"type": "float", "default": None, "nullable": True},
				STPreprocessingParams.TOTAL_COUNTS_NORMALIZE: {"type": "bool", "default": False},
				STPreprocessingParams.LOG1P_TRANSFORM: {"type": "bool", "default": False},
				STPreprocessingParams.FORCE_RECOMPUTING: {"type": "bool", "default": False},
			},
		},
		"annotation_file_types": AnnotationFileType.list(),
		"registration_params": {
			RegistrationType.FEATURE_EXTRACTION: {
				"min_max_rescale": {"type": "bool", "default": True},
				"force_recomputing": {"type": "bool", "default": False},
				"patch_size": {"type": "int", "default": 224},
				"background_color": {
					"type": "enum",
					"options": SegmentationBackgroundColor.list(),
					"default": SegmentationBackgroundColor.WHITE,
				},
			},
			RegistrationType.SPOT_INTERPOLATION: {
				"min_max_rescale": {"type": "bool", "default": True},
				"force_recomputing": {"type": "bool", "default": False},
			},
			RegistrationType.NONE: {},
		},
	}
