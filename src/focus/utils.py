import os
import gc
import sys
import ctypes
import logging
import multiprocessing
from pathlib import Path

import anndata
import numpy as np
import pandas as pd

from focus.constants import (
	ConfigParameters, ModalityParameters, ModalityType,
	RegistrationType, REGISTRATION_COMPATIBILITY,
	AlignmentStrategy, ALIGNMENT_STRATEGY_COMPATIBILITY,
	MsiPreprocessingParams, MsiIonMode,
	AnnotationsParameters, AnnotationFileType,
)
from focus.preprocessing._utils import validate_path_readable, discover_sample_ids, find_imzml_pair


def hw_from_axes(shape, axes: str | None) -> tuple[int, int]:
	"""Return (height, width) = (Y, X) sizes from a shape tuple using its OME 'axes' string.

	Falls back to the last two dimensions (OME canonical order places Y, X last) when the
	axes string is absent or does not label both Y and X against ``shape``. Correct for both
	channel-first ('CYX', e.g. Raman) and channel-last ('YXC', e.g. RGB microscopy) layouts,
	so display->full-resolution scale factors are derived from the real spatial axes rather
	than a positional guess.
	"""
	a = (axes or "").upper()
	if len(a) == len(shape) and "Y" in a and "X" in a:
		return int(shape[a.index("Y")]), int(shape[a.index("X")])
	return int(shape[-2]), int(shape[-1])


def available_cpus():
	try:
		# Linux: respects affinity (Slurm, cpuset cgroups, taskset)
		return len(os.sched_getaffinity(0))
	except AttributeError:
		# Non-Linux fallback
		return multiprocessing.cpu_count()


def release_memory(*, gpu: bool = True) -> None:
	"""
	Reclaim memory left over from a finished pipeline stage.

	The pipeline already isolates stages (only file paths cross stage boundaries),
	so a stage's large objects are unreferenced by the time this is called — but
	nothing forces their collection or returns the freed pages to the OS. This
	helper does both, and is the single place that knowledge lives.

	Steps (all best-effort):
	1. ``gc.collect()`` — AnnData / MuData / pandas / torch routinely build
	   reference cycles that frame-exit refcounting cannot free; only the cyclic
	   collector reclaims them, and an idle process may not run it for a long time.
	2. ``torch.cuda.empty_cache()`` — only when torch is *already* imported (probed
	   via ``sys.modules`` so a torch-free run never pays the import / CUDA-init
	   cost) and a CUDA device is present. Returns the caching allocator's blocks.
	3. ``malloc_trim(0)`` — Linux/glibc only. This is the step that actually makes
	   RSS fall in tools like ``htop``; without it the C allocator keeps freed
	   pages. ``libc.so.6`` / ``malloc_trim`` do not exist on macOS or musl.

	Parameters
	----------
	gpu : bool, keyword-only
		When False, skip the torch/CUDA probe (use at non-GPU stage boundaries).
	"""
	gc.collect()

	if gpu:
		torch = sys.modules.get("torch")
		if torch is not None:
			try:
				if torch.cuda.is_available():
					torch.cuda.empty_cache()
			except Exception:
				pass

	if sys.platform.startswith("linux"):
		try:
			ctypes.CDLL("libc.so.6").malloc_trim(0)
		except Exception:
			pass


def setup_logging(dataset_path: str | None = None, debug: bool = False) -> logging.Logger:
	"""
	Configure the 'focus' logger with a console handler and optionally a file handler.

	Can be called in two phases: first with dataset_path=None to install a
	console-only handler (useful before the config is validated and dataset_path
	is known), then again with a real dataset_path to add the file handler.

	Parameters
	----------
	dataset_path : str or None
		Directory where focus.log is written.  If None, only the console
		handler is installed (no file handler).
	debug : bool
		If True, the console handler emits DEBUG-level messages and werkzeug
		HTTP request logs are shown.  If False (default), the console shows
		INFO and above only, and werkzeug is silenced.

	Returns the configured logger.
	"""
	logger = logging.getLogger("focus")
	logger.setLevel(logging.DEBUG)

	# Add console handler only if one is not already present
	has_console = any(
		isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
		for h in logger.handlers
	)
	if not has_console:
		console = logging.StreamHandler()
		console.setLevel(logging.DEBUG if debug else logging.INFO)
		console.setFormatter(logging.Formatter(
			"%(asctime)s [%(levelname)s] %(name)s: %(message)s",
			datefmt="%H:%M:%S"
		))
		logger.addHandler(console)

	# Add file handler only when dataset_path is known and no file handler exists yet
	if dataset_path is not None:
		has_file = any(isinstance(h, logging.FileHandler) for h in logger.handlers)
		if not has_file:
			log_file = os.path.join(dataset_path, "focus.log")
			file_handler = logging.FileHandler(log_file, mode='a')
			file_handler.setLevel(logging.DEBUG)
			file_handler.setFormatter(logging.Formatter(
				"%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
			))
			logger.addHandler(file_handler)

	# Silence werkzeug HTTP request logs unless in debug mode
	logging.getLogger("werkzeug").setLevel(logging.DEBUG if debug else logging.WARNING)

	return logger


def parse_config(config: dict, require_raw_inputs: bool = True) -> dict:
	"""
	Validate the FOCUS configuration dictionary and apply defaults.

	Performs all structural, type, path, and compatibility checks upfront
	so that errors are caught before any computation begins.

	Parameters
	----------
	config : dict
		Raw configuration loaded from JSON.
	require_raw_inputs : bool, default True
		Whether to verify that the on-disk resources consumed only by stages
		*other than* alignment are present. The full pipeline (``main.py``) leaves
		this ``True``. Set it to ``False`` for an alignment-only run that starts
		from already-preprocessed files, where none of these are needed: it skips
		the per-(sample, modality) raw input directory check and the MSI
		lipid-annotation database check (preprocessing inputs), the HuggingFace
		token requirement (a registration credential), and the GeoJSON
		annotation-file existence check (an annotation input). All structural,
		type, naming, reference, and alignment-strategy compatibility checks run
		regardless of this flag.

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
	config.setdefault(ConfigParameters.IGNORE_SAMPLES, [])
	config.setdefault(ConfigParameters.SAMPLES, [])
	config.setdefault(ConfigParameters.LAST_EDIT, None)

	_check_type(config, ConfigParameters.PERFORM_ALIGNMENT, bool)
	_check_type(config, ConfigParameters.PERFORM_REGISTRATION, bool)
	if config[ConfigParameters.HUGGINGFACE_TOKEN] is not None:
		_check_type(config, ConfigParameters.HUGGINGFACE_TOKEN, str)

	ignore_samples = config[ConfigParameters.IGNORE_SAMPLES]
	if not isinstance(ignore_samples, list) or not all(isinstance(s, str) for s in ignore_samples):
		raise TypeError("'ignore_samples' must be a list of strings.")

	samples = config[ConfigParameters.SAMPLES]
	if not isinstance(samples, list) or not all(isinstance(s, str) for s in samples):
		raise TypeError("'samples' must be a list of strings.")

	if config[ConfigParameters.LAST_EDIT] is not None and not isinstance(config[ConfigParameters.LAST_EDIT], str):
		raise TypeError("'last_edit' must be a string or null.")

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
		modality.setdefault(ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL)
		modality.setdefault(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False)

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
	sample_ids = discover_sample_ids(dataset_path, ignore_samples=ignore_samples)
	if len(sample_ids) == 0:
		raise ValueError(f"No sample directories found in '{dataset_path}'.")

	# Raw per-(sample, modality) input directories and the MSI lipid database are
	# consumed only by preprocessing; an alignment-only run from preprocessed files
	# needs neither, so skip these existence checks when require_raw_inputs is False.
	if require_raw_inputs:
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

				# MSI input lives in pos/ and/or neg/ ion mode subdirectories. The GUI creates both
				# for every sample, so validate the imzML/IBD pairs rather than the directories:
				# a sample needs a complete pair in at least one ion mode.
				for sample_id in sample_ids:
					mod_dir = os.path.join(dataset_path, sample_id, mod_name)
					# Materialise the list rather than using a generator with any(): short-circuiting
					# on the first hit would skip the incomplete-pair check on the other ion mode.
					pairs = [
						find_imzml_pair(os.path.join(mod_dir, mode)) for mode in MsiIonMode.list()
					]
					if not any(pairs):
						raise FileNotFoundError(
							f"No complete .imzML + .ibd file pair found for sample '{sample_id}' in "
							f"'{mod_dir}'. Expected one in at least one of the ion mode "
							f"subdirectories: {MsiIonMode.list()}."
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

	# --- Step 9b: Alignment strategy compatibility ---
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_modality = next(
		m for m in config[ConfigParameters.MODALITIES] if m[ModalityParameters.NAME] == ref_name
	)
	ref_type = ref_modality[ModalityParameters.TYPE]
	pre_aligned_count = 0
	for modality in config[ConfigParameters.MODALITIES]:
		strategy = modality[ModalityParameters.ALIGNMENT_STRATEGY]
		if strategy not in AlignmentStrategy.list():
			raise ValueError(
				f"Unsupported alignment strategy '{strategy}' for modality '{modality[ModalityParameters.NAME]}'. "
				f"Supported strategies: {AlignmentStrategy.list()}"
			)
		if strategy == AlignmentStrategy.PRE_ALIGNED:
			if modality[ModalityParameters.NAME] == ref_name:
				raise ValueError(
					f"Alignment strategy 'pre_aligned' cannot be set on the reference modality "
					f"'{modality[ModalityParameters.NAME]}'."
				)
			# Pre-aligned requires the reference to be spot-based (its coordinates must be
			# expressible in another modality's coordinate system)
			compatible_ref_types = ALIGNMENT_STRATEGY_COMPATIBILITY[AlignmentStrategy.PRE_ALIGNED]
			if ref_type not in compatible_ref_types:
				raise ValueError(
					f"Alignment strategy 'pre_aligned' requires a spot-based reference modality, "
					f"but reference '{ref_name}' has type '{ref_type}'. "
					f"Supported reference types: {compatible_ref_types}"
				)
			pre_aligned_count += 1
	if pre_aligned_count > 1:
		raise ValueError(
			f"At most one non-reference modality may use the 'pre_aligned' alignment strategy. "
			f"The reference modality can only be expressed in one coordinate system at a time, "
			f"but {pre_aligned_count} modalities have 'pre_aligned' set."
		)

	# --- Step 10: HuggingFace token requirement ---
	# The token is only used to download the registration feature extractor; an
	# alignment-only run never registers, so skip the requirement when
	# require_raw_inputs is False.
	if require_raw_inputs:
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

	# --- Step 11: spatial_annotations ---
	config.setdefault(ConfigParameters.SPATIAL_ANNOTATIONS, None)
	ann_cfg = config[ConfigParameters.SPATIAL_ANNOTATIONS]
	if ann_cfg is not None:
		if not isinstance(ann_cfg, dict):
			raise TypeError("'spatial_annotations' must be a dict or null.")
		_require_key(ann_cfg, AnnotationsParameters.MODALITY_NAME, str, "spatial_annotations")
		_require_key(ann_cfg, AnnotationsParameters.FILE_TYPE, str, "spatial_annotations")

		ann_mod_name = ann_cfg[AnnotationsParameters.MODALITY_NAME]
		if ann_mod_name not in names:
			raise ValueError(
				f"Annotation modality '{ann_mod_name}' not found in declared modalities: {names}"
			)

		ann_file_type = ann_cfg[AnnotationsParameters.FILE_TYPE]
		if ann_file_type not in AnnotationFileType.list():
			raise ValueError(
				f"Unsupported annotation file type '{ann_file_type}'. "
				f"Supported types: {AnnotationFileType.list()}"
			)

		# The per-sample GeoJSON files are an annotation-transfer input; skip the
		# existence check for an alignment-only run (require_raw_inputs False).
		if require_raw_inputs and ann_file_type == AnnotationFileType.GEOJSON:
			for sample_id in sample_ids:
				mod_dir = os.path.join(dataset_path, sample_id, ann_mod_name)
				geojson_files = [f for f in os.listdir(mod_dir) if f.endswith('.geojson')]
				if len(geojson_files) == 0:
					raise FileNotFoundError(
						f"No .geojson annotation file found for sample '{sample_id}' "
						f"in '{mod_dir}'."
					)
				if len(geojson_files) > 1:
					raise ValueError(
						f"Multiple .geojson files found for sample '{sample_id}' "
						f"in '{mod_dir}': {geojson_files}. Expected exactly one."
					)

		ref_name = config[ConfigParameters.REFERENCE_MODALITY]
		if ann_mod_name != ref_name and not config[ConfigParameters.PERFORM_ALIGNMENT]:
			raise ValueError(
				f"'spatial_annotations.modality_name' is '{ann_mod_name}' (a non-reference modality). "
				"Transferring annotations from a non-reference modality requires "
				"'perform_alignment' to be true."
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

def enhance_contrast(channel: np.ndarray, saturated_pixels: float = 0.35, max_stat_pixels: int | None = None) -> np.ndarray:
	'''
	Enhance the contrast of a single channel image by stretching the histogram.
	Add a small amount of saturated pixels to improve the contrast.

	Parameters
	----------
	channel : np.ndarray[np.uint8]
		The channel to enhance.
	saturated_pixels : float
		The amount of saturated pixels to add. Default is 0.35%.
	max_stat_pixels : int, optional
		If set and the number of nonzero pixels exceeds this, the saturation percentiles are
		estimated from a strided subsample instead of the full array (percentiles are stable
		under subsampling, so this is a fast, negligible-error approximation for huge images).
		The stretch itself is still applied exactly to every pixel of `channel`. Default (None)
		always uses the exact full-population percentile, unchanged from prior behavior.
	'''

	if channel.dtype != np.float32:
		channel = channel.astype(np.float32)

	mask = channel > 0

	if np.any(mask):
		values = channel[mask]
		if max_stat_pixels is not None and values.size > max_stat_pixels:
			stride = -(-values.size // max_stat_pixels)  # ceil division
			values = values[::stride]

		# Compute the pixels to saturate
		p_low, p_high = np.percentile(values, (saturated_pixels, 100 - saturated_pixels))

		if p_high > p_low:
			# Stretch the histogram in-place
			np.clip(channel, p_low, p_high, out=channel)
			channel -= p_low
			channel /= (p_high - p_low)

	return channel

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

	if channel.dtype != np.float32:
		channel = channel.astype(np.float32)
	np.power(channel, gamma, out=channel)
	return channel


def concat_on_disk_compat(*args, **kwargs) -> None:
	anndata.settings.allow_write_nullable_strings = True
	# Newer anndata versions call .is_dir() on input paths and require Path objects, not strings.
	args = list(args)
	if args:
		inp = args[0]
		if isinstance(inp, dict):
			args[0] = {k: Path(v) for k, v in inp.items()}
		elif isinstance(inp, (list, tuple)):
			args[0] = [Path(p) for p in inp]
	if len(args) >= 2 and isinstance(args[1], str):
		args[1] = Path(args[1])
	anndata.experimental.concat_on_disk(*args, **kwargs)


def _compat_strings(obj):
	"""Recursively convert pd.StringDtype fields to object dtype in-place."""
	if isinstance(obj, pd.DataFrame):
		for col in obj.columns:
			if isinstance(obj[col].dtype, pd.StringDtype):
				obj[col] = obj[col].astype(object)
		if isinstance(obj.index.dtype, pd.StringDtype):
			obj.index = obj.index.astype(object)
	elif isinstance(obj, pd.Series):
		if isinstance(obj.dtype, pd.StringDtype):
			return obj.astype(object)
	elif isinstance(obj, dict):
		for key in list(obj.keys()):
			obj[key] = _compat_strings(obj[key])
	return obj


def _compat_adata(adata) -> None:
	"""Convert all nullable string fields in an AnnData (or MuData) to object dtype."""
	for df in (adata.obs, adata.var):
		_compat_strings(df)
	for key in list(adata.obsm.keys()):
		adata.obsm[key] = _compat_strings(adata.obsm[key])
	_compat_strings(adata.uns)


def write_h5ad_compat(adata, path, **kwargs) -> None:
	"""Write AnnData to h5ad, converting nullable string arrays to object dtype.

	Converts pd.StringDtype in obs, var, obsm, and uns (recursively) before writing
	so that the file is readable by anndata < 0.11 and downstream tools that do not
	support the newer nullable-string encoding.
	"""
	_compat_adata(adata)
	adata.write_h5ad(path, **kwargs)


def read_merged_sample_ids(path: str) -> set[str] | None:
	"""Return the set of unique obs['sample_id'] values from a merged h5ad file.

	Reads only the obs DataFrame (backed mode) so the full data matrix is never loaded.
	Returns None if the file cannot be opened or lacks the obs['sample_id'] column.
	"""
	try:
		import anndata as _ad
		adata = _ad.read_h5ad(path, backed='r')
		ids = set(adata.obs['sample_id'].unique())
		adata.file.close()
		return ids
	except Exception:
		return None


def registration_cache_valid(cached_adata, expected_n_obs: int, registration_type: str) -> bool:
	"""True when a cached registration file can be safely reused.

	A cache is valid only when its observation count matches ``expected_n_obs`` AND it was
	produced by the same registration mode, recorded in ``uns['registration_type']``. A missing
	or mismatched stamp counts as invalid, so a file written by a different mode — or before
	stamping was introduced — is recomputed rather than silently reused. Every registration
	engine stamps this key on write, so switching a modality's ``registration_type`` always
	invalidates the previous mode's cache.
	"""
	return (cached_adata.n_obs == expected_n_obs
			and cached_adata.uns.get('registration_type') == registration_type)


def write_h5mu_compat(mdata, path, **kwargs) -> None:
	"""Write MuData to h5mu, converting nullable string arrays to object dtype.

	Converts pd.StringDtype in obs, var, obsm, and uns (recursively) for the
	top-level MuData and every modality AnnData so the file is readable by
	anndata/mudata < 0.11 and downstream tools that do not support the newer
	nullable-string encoding.
	"""
	for adata in [mdata] + list(mdata.mod.values()):
		_compat_adata(adata)
	mdata.write(path, **kwargs)
