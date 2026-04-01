import os
import logging
import multiprocessing

import numpy as np

from focus.constants import (
	ConfigParameters, ModalityParameters, ModalityType,
	RegistrationType, REGISTRATION_COMPATIBILITY, MsiPreprocessingParams
)
from focus.preprocessing._utils import validate_path_readable, discover_sample_ids


def available_cpus():
	try:
		# Linux: respects affinity (Slurm, cpuset cgroups, taskset)
		return len(os.sched_getaffinity(0))
	except AttributeError:
		# Non-Linux fallback
		return multiprocessing.cpu_count()


def setup_logging(dataset_path: str) -> logging.Logger:
	"""
	Configure the 'focus' logger with console (INFO) and file (DEBUG) handlers.
	The log file is written to {dataset_path}/focus.log.

	Returns the configured logger.
	"""
	logger = logging.getLogger("focus")

	# Avoid adding duplicate handlers if called multiple times
	if logger.handlers:
		return logger

	logger.setLevel(logging.DEBUG)

	# Console handler: INFO level
	console = logging.StreamHandler()
	console.setLevel(logging.INFO)
	console.setFormatter(logging.Formatter(
		"%(asctime)s [%(levelname)s] %(name)s: %(message)s",
		datefmt="%H:%M:%S"
	))
	logger.addHandler(console)

	# File handler: DEBUG level
	log_file = os.path.join(dataset_path, "focus.log")
	file_handler = logging.FileHandler(log_file, mode='a')
	file_handler.setLevel(logging.DEBUG)
	file_handler.setFormatter(logging.Formatter(
		"%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
	))
	logger.addHandler(file_handler)

	return logger


def parse_config(config: dict) -> dict:
	"""
	Validate the FOCUS configuration dictionary and apply defaults.

	Performs all structural, type, path, and compatibility checks upfront
	so that errors are caught before any computation begins.

	Parameters
	----------
	config : dict
		Raw configuration loaded from JSON.

	Returns
	-------
	dict
		The validated configuration with defaults applied.

	Raises
	------
	TypeError
		If config or any field has the wrong type.
	KeyError
		If a required key is missing.
	ValueError
		If a value is invalid or constraints are violated.
	"""

	# --- Step 1: Type check ---
	if not isinstance(config, dict):
		raise TypeError("Config must be a dictionary.")

	# --- Step 2: Required top-level keys and types ---
	_require_key(config, ConfigParameters.DATASET_PATH, str)
	_require_key(config, ConfigParameters.MODALITIES, list)
	_require_key(config, ConfigParameters.REFERENCE_MODALITY, str)

	if len(config[ConfigParameters.MODALITIES]) == 0:
		raise ValueError("'modalities' must contain at least one entry.")

	# Apply defaults for optional top-level keys
	config.setdefault(ConfigParameters.PERFORM_ALIGNMENT, True)
	config.setdefault(ConfigParameters.PERFORM_REGISTRATION, True)
	config.setdefault(ConfigParameters.HUGGINGFACE_TOKEN, None)

	_check_type(config, ConfigParameters.PERFORM_ALIGNMENT, bool)
	_check_type(config, ConfigParameters.PERFORM_REGISTRATION, bool)
	if config[ConfigParameters.HUGGINGFACE_TOKEN] is not None:
		_check_type(config, ConfigParameters.HUGGINGFACE_TOKEN, str)

	# --- Step 3: dataset_path exists and is readable ---
	dataset_path = config[ConfigParameters.DATASET_PATH]
	validate_path_readable(dataset_path)

	# --- Step 4: Per-modality validation ---
	for i, modality in enumerate(config[ConfigParameters.MODALITIES]):
		if not isinstance(modality, dict):
			raise TypeError(f"Modality at index {i} must be a dictionary.")

		_require_key(modality, ModalityParameters.NAME, str, context=f"modality[{i}]")
		_require_key(modality, ModalityParameters.TYPE, str, context=f"modality[{i}]")
		_require_key(modality, ModalityParameters.PROCESSING_SETTINGS, dict, context=f"modality[{i}]")

		mod_type = modality[ModalityParameters.TYPE]
		if mod_type not in ModalityType.list():
			raise ValueError(
				f"Unsupported modality type '{mod_type}' for modality '{modality[ModalityParameters.NAME]}'. "
				f"Supported types: {ModalityType.list()}"
			)

		# Apply defaults for optional modality keys
		modality.setdefault(ModalityParameters.REGISTRATION_TYPE, RegistrationType.NONE)
		modality.setdefault(ModalityParameters.REGISTRATION_SETTINGS, {})

	# --- Step 5: Unique modality names ---
	names = [m[ModalityParameters.NAME] for m in config[ConfigParameters.MODALITIES]]
	if len(names) != len(set(names)):
		duplicates = [n for n in names if names.count(n) > 1]
		raise ValueError(f"Duplicate modality names: {set(duplicates)}")

	# --- Step 6: reference_modality must match one modality's name ---
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	if ref_name not in names:
		raise ValueError(
			f"Reference modality '{ref_name}' not found in declared modalities: {names}"
		)

	# --- Step 7: Logical constraint ---
	if config[ConfigParameters.PERFORM_REGISTRATION] and not config[ConfigParameters.PERFORM_ALIGNMENT]:
		raise ValueError(
			"'perform_registration' requires 'perform_alignment' to be true. "
			"Registration depends on aligned coordinates."
		)

	# --- Step 8: Sample directory structure ---
	sample_ids = discover_sample_ids(dataset_path)
	if len(sample_ids) == 0:
		raise ValueError(f"No sample directories found in '{dataset_path}'.")

	for modality in config[ConfigParameters.MODALITIES]:
		mod_name = modality[ModalityParameters.NAME]
		for sample_id in sample_ids:
			sample_modality_dir = os.path.join(dataset_path, sample_id, mod_name)
			if not os.path.isdir(sample_modality_dir):
				raise FileNotFoundError(
					f"Missing modality directory for sample '{sample_id}': {sample_modality_dir}"
				)

		# Check modality-specific support files
		mod_type = modality[ModalityParameters.TYPE]
		settings = modality[ModalityParameters.PROCESSING_SETTINGS]

		if mod_type == ModalityType.MSI:
			lipid_db = settings.get(MsiPreprocessingParams.LIPID_ANNOTATION_DB)
			if lipid_db is not None and not os.path.isfile(lipid_db):
				raise FileNotFoundError(
					f"Lipid annotation database not found for modality '{mod_name}': {lipid_db}"
				)

	# --- Step 9: Registration type compatibility ---
	for modality in config[ConfigParameters.MODALITIES]:
		reg_type = modality[ModalityParameters.REGISTRATION_TYPE]
		if reg_type not in RegistrationType.list():
			raise ValueError(
				f"Unsupported registration type '{reg_type}' for modality '{modality[ModalityParameters.NAME]}'. "
				f"Supported types: {RegistrationType.list()}"
			)

		compatible = REGISTRATION_COMPATIBILITY.get(reg_type)
		if compatible is not None:  # None means all types are compatible
			mod_type = modality[ModalityParameters.TYPE]
			if mod_type not in compatible:
				raise ValueError(
					f"Registration type '{reg_type}' is not compatible with modality type '{mod_type}' "
					f"(modality '{modality[ModalityParameters.NAME]}'). Compatible types: {compatible}"
				)

	# --- Step 10: HuggingFace token requirement ---
	needs_hf_token = any(
		m[ModalityParameters.REGISTRATION_TYPE] == RegistrationType.FEATURE_EXTRACTION
		for m in config[ConfigParameters.MODALITIES]
	)
	if needs_hf_token:
		hf_token = config[ConfigParameters.HUGGINGFACE_TOKEN]
		if not hf_token or not isinstance(hf_token, str):
			raise ValueError(
				"'huggingface_token' is required when any modality uses 'feature_extraction' registration."
			)

	return config


# --- Validation helpers ---

def _require_key(d: dict, key: str, expected_type: type, context: str = "config"):
	"""Check that key exists in dict and has the expected type."""
	if key not in d:
		raise KeyError(f"Missing required key '{key}' in {context}.")
	if not isinstance(d[key], expected_type):
		raise TypeError(
			f"'{key}' in {context} must be {expected_type.__name__}, got {type(d[key]).__name__}."
		)


def _check_type(d: dict, key: str, expected_type: type, context: str = "config"):
	"""Check type for a key that is known to exist."""
	if not isinstance(d[key], expected_type):
		raise TypeError(
			f"'{key}' in {context} must be {expected_type.__name__}, got {type(d[key]).__name__}."
		)


# --- Image utilities ---

def enhance_contrast(channel: np.ndarray, saturated_pixels: float = 0.35) -> np.ndarray:
	'''
	Enhance the contrast of a single channel image by stretching the histogram.
	Add a small amount of saturated pixels to improve the contrast.

	Parameters
	----------
	channel : np.ndarray[np.uint8]
		The channel to enhance.
	saturated_pixels : float
		The amount of saturated pixels to add. Default is 0.35%.
	'''

	# Convert to float32
	channel = channel.astype(np.float32)

	mask = channel > 0
	result = np.zeros_like(channel, dtype=np.float32)

	if np.any(mask):
		# Compute the pixels to saturate
		p_low, p_high = np.percentile(channel[mask], (saturated_pixels, 100 - saturated_pixels))

		# Stretch the histogram
		rescaled_channel = np.clip(channel[mask], p_low, p_high)

		result[mask] = (rescaled_channel - p_low) / (p_high - p_low)

	return result

def gamma_correction(channel: np.ndarray, gamma: float = 0.45) -> np.ndarray:
	'''
	Apply gamma correction to a single channel image.

	Parameters
	----------
	image : np.ndarray[np.uint8]
		The image to correct.
	gamma : float
		The gamma value to use. Default is 0.45.
	'''

	channel = channel.astype(np.float32)
	channel = np.power(channel, gamma)
	return channel
