import os, logging, anndata
import numpy as np
import mudata

from focus.constants import (
	ConfigParameters, ModalityParameters, RegistrationType,
	ModalityType, MODALITY_FILE_EXTENSION, MULTIMODAL_DATASET,
	AlignmentStrategy
)
from focus.preprocessing import preprocess_modality
from focus.preprocessing._utils import StepReporter
from focus.alignment.alignment import DirectMappingAligner
from focus.registration.registration import FeatureExtractorRegistration, SpotInterpolationRegistration

logger = logging.getLogger("focus.orchestrator")

_IMAGE_MODALITIES = [ModalityType.MICROSCOPY_IMAGE, ModalityType.RAMAN]
_SPOT_MODALITIES = [ModalityType.MSI, ModalityType.ST]


def run(config: dict, progress_callback=None) -> list[str]:
	"""
	Execute the full FOCUS pipeline: preprocessing, alignment, registration, and MuData compilation.

	Parameters
	----------
	config : dict
		Validated configuration dictionary (output of utils.parse_config).
	progress_callback : callable, optional
		Called at each stage/sample transition with a status dict.
		Signature: progress_callback(status_dict)

	Returns
	-------
	list[str]
		List of absolute paths to all generated output files.
	"""

	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	output_files: list[str] = []

	def _report(**kwargs):
		if progress_callback:
			progress_callback(kwargs)

	step_reporter = StepReporter(callback=progress_callback)

	# --- Stage 1: Preprocessing (always runs, caching is internal) ---
	logger.info("=" * 60)
	logger.info("STAGE 1: Preprocessing")
	logger.info("=" * 60)
	_report(state="running", stage="preprocessing", stage_index=1, total_stages=4,
			message="Starting preprocessing...", sub_step=None, sub_step_index=0,
			sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)

	modality_files: dict[str, dict[str, str]] = {}
	total_modalities = len(modalities)
	for mod_idx, modality in enumerate(modalities, 1):
		mod_name = modality[ModalityParameters.NAME]
		mod_type = modality[ModalityParameters.TYPE]
		logger.info(f"Preprocessing modality '{mod_name}' (type: {mod_type})")
		_report(state="running", stage="preprocessing", stage_index=1, total_stages=4,
				current_modality=mod_name, current_modality_index=mod_idx, total_modalities=total_modalities,
				current_sample=None, current_sample_index=0, total_samples=0,
				message=f"Preprocessing '{mod_name}'",
				sub_step=None, sub_step_index=0, sub_step_total=0,
				sub_step_progress=0, sub_step_items_total=0)

		modality_files[mod_name] = preprocess_modality(
			path=dataset_path,
			modality_name=mod_name,
			modality_type=mod_type,
			preprocessing_settings=modality[ModalityParameters.PROCESSING_SETTINGS],
			step_reporter=step_reporter
		)
		for path in modality_files[mod_name].values():
			output_files.append(path)
		logger.info(f"Preprocessing complete for '{mod_name}': {len(modality_files[mod_name])} samples")

	# --- Stage 2: Alignment ---
	aligned_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_ALIGNMENT]:
		logger.info("=" * 60)
		logger.info("STAGE 2: Alignment")
		logger.info("=" * 60)
		_report(state="running", stage="alignment", stage_index=2, total_stages=4,
				message="Starting alignment...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		aligned_files = _run_alignment(config, modality_files, _report)
		for mod_files in aligned_files.values():
			for path in mod_files.values():
				if path not in output_files:
					output_files.append(path)
	else:
		logger.info("Skipping alignment (disabled in config).")

	# --- Stage 3: Registration ---
	registered_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_REGISTRATION]:
		logger.info("=" * 60)
		logger.info("STAGE 3: Registration")
		logger.info("=" * 60)
		_report(state="running", stage="registration", stage_index=3, total_stages=4,
				message="Starting registration...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		registered_files = _run_registration(config, modality_files, aligned_files)
		for mod_files in registered_files.values():
			for path in mod_files.values():
				output_files.append(path)
	else:
		logger.info("Skipping registration (disabled in config).")

	# --- Stage 4: Compile MuData ---
	if config[ConfigParameters.PERFORM_REGISTRATION] and _has_spot_modalities(config):
		logger.info("=" * 60)
		logger.info("STAGE 4: Compiling multimodal dataset")
		logger.info("=" * 60)
		_report(state="running", stage="compiling", stage_index=4, total_stages=4,
				message="Compiling multimodal dataset...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		mudata_path = _compile_mudata(config, modality_files, registered_files)
		if mudata_path:
			output_files.append(mudata_path)

	logger.info("=" * 60)
	logger.info("FOCUS pipeline completed successfully.")
	logger.info("=" * 60)

	_report(state="completed", stage=None, stage_index=4, total_stages=4,
			message="Pipeline completed successfully.", output_files=output_files)

	return output_files


def _get_reference_modality(config: dict) -> dict:
	"""Find and return the reference modality config entry."""
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	for m in config[ConfigParameters.MODALITIES]:
		if m[ModalityParameters.NAME] == ref_name:
			return m
	raise ValueError(f"Reference modality '{ref_name}' not found.")


def _has_spot_modalities(config: dict) -> bool:
	"""Check if there is at least one spot-based modality in the config."""
	ref_mod = _get_reference_modality(config)
	return ref_mod[ModalityParameters.TYPE] in _SPOT_MODALITIES


def _run_alignment(config: dict, modality_files: dict, report) -> dict:
	"""
	Align each non-reference modality to the reference modality.

	Returns
	-------
	dict
		{modality_name: {sample_id: aligned_file_path}}
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_mod = _get_reference_modality(config)
	ref_type = ref_mod[ModalityParameters.TYPE]

	global_force = config.get(ConfigParameters.ALIGNMENT_FORCE_RECOMPUTING, False)
	ref_proc_force = ref_mod[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)

	aligned_files: dict[str, dict[str, str]] = {}

	# The reference modality is not aligned — its preprocessed files are passed through
	aligned_files[ref_name] = modality_files[ref_name]

	for modality in modalities:
		mod_name = modality[ModalityParameters.NAME]
		if mod_name == ref_name:
			continue

		# Force recomputing if: global switch is on, or either modality's preprocessing was forced
		tgt_proc_force = modality[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)
		pair_force = global_force or ref_proc_force or tgt_proc_force

		logger.info(f"Aligning '{mod_name}' to reference '{ref_name}' (force_recomputing={pair_force})")

		aligner = DirectMappingAligner(
			path=dataset_path,
			reference_modality=modality_files[ref_name],
			target_modality=modality_files[mod_name],
			reference_modality_name=ref_name,
			target_modality_name=mod_name,
			reference_modality_type=ref_type,
			target_modality_type=modality[ModalityParameters.TYPE]
		)

		strategy = modality.get(ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL)

		if strategy == AlignmentStrategy.PRE_ALIGNED:
			logger.info(f"Pre-aligned strategy for '{mod_name}' — using uniform alignment (no GUI)")
			aligned_files[mod_name] = aligner.uniform_aligned_dataset(force_recomputing=pair_force)
		elif aligner.is_alignment_needed(force_recomputing=pair_force):
			# Signal that alignment is starting — the GUI should show "Open Alignment Tool"
			report(state="alignment_waiting", stage="alignment", stage_index=2, total_stages=4,
				   current_modality=mod_name,
				   message=f"Waiting for manual alignment of '{mod_name}' to '{ref_name}'...")

			aligned_files[mod_name] = aligner.align_dataset(force_recomputing=pair_force)
		else:
			logger.info(f"All samples for '{mod_name}' already aligned — skipping GUI")
			aligned_files[mod_name] = aligner.collect_aligned_files()

		report(state="running", stage="alignment", stage_index=2, total_stages=4,
			   current_modality=mod_name,
			   message=f"Alignment complete for '{mod_name}'")

		logger.info(f"Alignment complete for '{mod_name}': {len(aligned_files[mod_name])} files")

	return aligned_files


def _run_registration(config: dict, modality_files: dict, aligned_files: dict) -> dict:
	"""
	Register each non-reference modality that has a registration_type != 'none'.

	For FeatureExtraction (image modalities):
		Extracts patch embeddings from the image at anchor spot locations.
	For SpotInterpolation (spot modalities):
		Gaussian-weighted interpolation of target features onto anchor spot grid.

	Returns
	-------
	dict
		{modality_name: {sample_id: registered_file_path}}
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]

	registered_files: dict[str, dict[str, str]] = {}

	for modality in modalities:
		mod_name = modality[ModalityParameters.NAME]
		reg_type = modality[ModalityParameters.REGISTRATION_TYPE]

		if mod_name == ref_name or reg_type == RegistrationType.NONE:
			continue

		reg_settings = modality[ModalityParameters.REGISTRATION_SETTINGS]
		logger.info(f"Registering '{mod_name}' using '{reg_type}' strategy")

		if reg_type == RegistrationType.FEATURE_EXTRACTION:
			engine = FeatureExtractorRegistration(
				path=dataset_path,
				hf_token=config[ConfigParameters.HUGGINGFACE_TOKEN]
			)
			registered_files[mod_name] = engine.register_dataset(
				image_files=modality_files[mod_name],
				anchor_files=aligned_files.get(ref_name, modality_files[ref_name]),
				image_name=mod_name,
				anchor_name=ref_name,
				min_max_rescale=reg_settings.get("min_max_rescale", True),
				force_recomputing=reg_settings.get("force_recomputing", False),
				background_color=reg_settings.get("background_color", None),
				patch_size=reg_settings.get("patch_size", 224),
			)

		elif reg_type == RegistrationType.SPOT_INTERPOLATION:
			engine = SpotInterpolationRegistration(path=dataset_path)
			registered_files[mod_name] = engine.register_dataset(
				anchor_files=modality_files[ref_name],
				target_files=aligned_files[mod_name],
				anchor_name=ref_name,
				target_name=mod_name,
				min_max_rescale=reg_settings.get("min_max_rescale", True),
				force_recomputing=reg_settings.get("force_recomputing", False),
			)

		else:
			logger.warning(f"Unknown registration type '{reg_type}' for '{mod_name}', skipping.")
			continue

		logger.info(f"Registration complete for '{mod_name}'")

	return registered_files


def _compile_mudata(
	config: dict,
	modality_files: dict,
	registered_files: dict,
) -> str | None:
	"""
	Compile a final MuData object with paired observations across modalities.

	Returns the path to the saved MuData file, or None if compilation was skipped.
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_mod = _get_reference_modality(config)

	# Only compile if anchor is spot-based
	if ref_mod[ModalityParameters.TYPE] not in _SPOT_MODALITIES:
		logger.info("Anchor modality is not spot-based; skipping MuData compilation.")
		return None

	# Load anchor modality's merged preprocessed data
	anchor_merged = modality_files[ref_name].get("merged")
	if anchor_merged is None or not os.path.exists(anchor_merged):
		logger.warning(f"No merged file for anchor '{ref_name}', skipping MuData compilation.")
		return None

	anchor_adata = anndata.read_h5ad(anchor_merged)

	# Shared obs/obsm/uns come from the anchor
	shared_spatial = np.asarray(anchor_adata.obsm['spatial'], dtype=np.float32)
	shared_sample_id = anchor_adata.obs['sample_id'].copy()
	shared_spot_size = anchor_adata.uns.get('spot_size', None)
	n_anchor_obs = anchor_adata.n_obs

	# Build modality dict for MuData
	mod_dict: dict[str, anndata.AnnData] = {}

	# Add anchor modality
	ref_ad = anchor_adata.copy()
	if 'spatial' in ref_ad.obsm:
		del ref_ad.obsm['spatial']
	if 'spot_size' in ref_ad.uns:
		del ref_ad.uns['spot_size']
	mod_dict[ref_name] = ref_ad

	# Add registered non-anchor modalities
	for modality in modalities:
		mod_name = modality[ModalityParameters.NAME]
		if mod_name == ref_name:
			continue

		if mod_name not in registered_files:
			logger.debug(f"No registration output for '{mod_name}', skipping in MuData.")
			continue

		merged_path = registered_files[mod_name].get("merged")
		if merged_path is None or not os.path.exists(merged_path):
			logger.warning(f"No merged registration file for '{mod_name}', skipping in MuData.")
			continue

		reg_adata = anndata.read_h5ad(merged_path)

		if reg_adata.n_obs != n_anchor_obs:
			logger.warning(
				f"Observation count mismatch for '{mod_name}': "
				f"{reg_adata.n_obs} vs anchor {n_anchor_obs}. Skipping in MuData."
			)
			continue

		if 'spatial' in reg_adata.obsm:
			del reg_adata.obsm['spatial']
		if 'spot_size' in reg_adata.uns:
			del reg_adata.uns['spot_size']

		reg_adata.obs_names = anchor_adata.obs_names.tolist()
		mod_dict[mod_name] = reg_adata

	if len(mod_dict) < 2:
		logger.info("Only one modality available for MuData, skipping compilation.")
		return None

	# Build MuData
	mdata = mudata.MuData(mod_dict)

	mdata.obs['sample_id'] = shared_sample_id.values
	mdata.obsm['spatial'] = shared_spatial
	if shared_spot_size is not None:
		mdata.uns['spot_size'] = shared_spot_size

	output_path = MULTIMODAL_DATASET(dataset_path, "h5mu")
	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	mdata.write(output_path)
	logger.info(f"MuData saved to {output_path} with {len(mod_dict)} modalities, {n_anchor_obs} observations")

	return output_path
