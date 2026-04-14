import os, logging, anndata
import numpy as np
import pandas as pd
import mudata

from focus.constants import (
	ConfigParameters, ModalityParameters, RegistrationType,
	ModalityType, MODALITY_FILE_EXTENSION, MULTIMODAL_DATASET,
	AlignmentStrategy, AnnotationsParameters, AnnotationFileType,
	MODALITY_ANNOTATION, MODALITY_ANNOTATION_MERGED,
)
from focus.preprocessing import preprocess_modality
from focus.preprocessing._utils import StepReporter
from focus.alignment.alignment import DirectMappingAligner
from focus.registration.registration import FeatureExtractorRegistration, SpotInterpolationRegistration

logger = logging.getLogger("focus.orchestrator")

_IMAGE_MODALITIES = [ModalityType.MICROSCOPY_IMAGE, ModalityType.RAMAN]
_SPOT_MODALITIES = [ModalityType.MSI, ModalityType.ST]


def run(config: dict, progress_callback=None) -> dict:
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
	dict
		Structured output files grouped by pipeline stage. Each present key maps to a dict
		with "merged" (list of merged output paths) and "per_sample" (list of per-sample paths).
		Keys: "preprocessing", "alignment", "annotations", "registration", "multimodal".
	"""

	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	output_files: dict = {}

	ann_enabled = config.get(ConfigParameters.SPATIAL_ANNOTATIONS) is not None
	n_stages = 5 if ann_enabled else 4

	def _report(**kwargs):
		if progress_callback:
			progress_callback(kwargs)

	step_reporter = StepReporter(callback=progress_callback)

	# --- Stage 1: Preprocessing (always runs, caching is internal) ---
	logger.info("=" * 60)
	logger.info("STAGE 1: Preprocessing")
	logger.info("=" * 60)
	_report(state="running", stage="preprocessing", stage_index=1, total_stages=n_stages,
			message="Starting preprocessing...", sub_step=None, sub_step_index=0,
			sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)

	modality_files: dict[str, dict[str, str]] = {}
	total_modalities = len(modalities)
	for mod_idx, modality in enumerate(modalities, 1):
		mod_name = modality[ModalityParameters.NAME]
		mod_type = modality[ModalityParameters.TYPE]
		logger.info(f"Preprocessing modality '{mod_name}' (type: {mod_type})")
		_report(state="running", stage="preprocessing", stage_index=1, total_stages=n_stages,
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
		logger.info(f"Preprocessing complete for '{mod_name}': {len(modality_files[mod_name])} samples")

	# Collect preprocessing outputs — per_modality groups per-sample files by modality name
	pre_merged: list[str] = []
	pre_per_modality: dict[str, list[str]] = {}
	for mod_name, mod_files in modality_files.items():
		for sid, path in mod_files.items():
			if sid == "merged":
				pre_merged.append(path)
			else:
				pre_per_modality.setdefault(mod_name, []).append(path)
	if pre_merged or pre_per_modality:
		output_files["preprocessing"] = {"merged": pre_merged, "per_modality": pre_per_modality}

	# --- Stage 2: Alignment ---
	aligned_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_ALIGNMENT]:
		logger.info("=" * 60)
		logger.info("STAGE 2: Alignment")
		logger.info("=" * 60)
		_report(state="running", stage="alignment", stage_index=2, total_stages=n_stages,
				message="Starting alignment...",
				current_modality=None, current_modality_index=0, total_modalities=0,
				current_sample=None, current_sample_index=0, total_samples=0,
				sub_step=None, sub_step_index=0, sub_step_total=0,
				sub_step_progress=0, sub_step_items_total=0)
		aligned_files = _run_alignment(config, modality_files, _report, n_stages)
		aln_merged: list[str] = []
		aln_per_modality: dict[str, list[str]] = {}
		for mod_name, mod_files in aligned_files.items():
			for sid, path in mod_files.items():
				if sid == "merged":
					aln_merged.append(path)
				else:
					aln_per_modality.setdefault(mod_name, []).append(path)
		if aln_merged or aln_per_modality:
			output_files["alignment"] = {"merged": aln_merged, "per_modality": aln_per_modality}
	else:
		logger.info("Skipping alignment (disabled in config).")

	# --- Stage 2.5: Annotation Transfer ---
	annotation_files: dict[str, str] = {}
	if ann_enabled:
		logger.info("=" * 60)
		logger.info("STAGE 2.5: Annotation Transfer")
		logger.info("=" * 60)
		_report(state="running", stage="annotation_transfer", stage_index=3, total_stages=n_stages,
				message="Transferring spatial annotations...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		annotation_files = _run_annotation_transfer(
			config, modality_files, aligned_files,
			report=_report, stage_index=3, n_stages=n_stages,
		)
		ann_merged: list[str] = []
		ann_per_modality: dict[str, list[str]] = {}
		for sid, path in annotation_files.items():
			if sid == "merged":
				ann_merged.append(path)
			else:
				ann_per_modality.setdefault("reference", []).append(path)
		if ann_merged or ann_per_modality:
			output_files["annotations"] = {"merged": ann_merged, "per_modality": ann_per_modality}
		logger.info("Annotation transfer complete.")

	stage_reg = 4 if ann_enabled else 3
	stage_mudata = 5 if ann_enabled else 4

	# --- Stage 3/4: Registration ---
	registered_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_REGISTRATION]:
		logger.info("=" * 60)
		logger.info(f"STAGE {stage_reg}: Registration")
		logger.info("=" * 60)
		_report(state="running", stage="registration", stage_index=stage_reg, total_stages=n_stages,
				message="Starting registration...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		registered_files = _run_registration(config, modality_files, aligned_files)
		reg_merged: list[str] = []
		reg_per_modality: dict[str, list[str]] = {}
		for mod_name, mod_files in registered_files.items():
			for sid, path in mod_files.items():
				if sid == "merged":
					reg_merged.append(path)
				else:
					reg_per_modality.setdefault(mod_name, []).append(path)
		if reg_merged or reg_per_modality:
			output_files["registration"] = {"merged": reg_merged, "per_modality": reg_per_modality}
	else:
		logger.info("Skipping registration (disabled in config).")

	# --- Stage 4/5: Compile MuData ---
	if config[ConfigParameters.PERFORM_REGISTRATION] and _has_spot_modalities(config):
		logger.info("=" * 60)
		logger.info(f"STAGE {stage_mudata}: Compiling multimodal dataset")
		logger.info("=" * 60)
		_report(state="running", stage="compiling", stage_index=stage_mudata, total_stages=n_stages,
				message="Compiling multimodal dataset...", sub_step=None, sub_step_index=0,
				sub_step_total=0, sub_step_progress=0, sub_step_items_total=0)
		mudata_path = _compile_mudata(config, modality_files, registered_files, annotation_files)
		if mudata_path:
			output_files["multimodal"] = {"merged": [mudata_path], "per_modality": {}}

	logger.info("=" * 60)
	logger.info("FOCUS pipeline completed successfully.")
	logger.info("=" * 60)

	_report(state="completed", stage=None, stage_index=n_stages, total_stages=n_stages,
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


def _run_alignment(config: dict, modality_files: dict, report, n_stages: int) -> dict:
	"""
	Align the reference modality into each non-reference modality's coordinate system.

	The reference modality is the "moving target" and each non-reference modality is the
	"fixed frame". After alignment, the reference modality's AnnData accumulates one
	obsm key per non-reference modality: obsm['{mod_name}_spatial'] contains the reference
	spots expressed in that modality's coordinate system.

	Returns
	-------
	dict
		{non_ref_modality_name: {sample_id: aligned_ref_file_path}}
		Each value points to the shared aligned reference file for that sample, which
		contains obsm['{mod_name}_spatial'] needed for registration.
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_mod = _get_reference_modality(config)
	ref_type = ref_mod[ModalityParameters.TYPE]

	global_force = config.get(ConfigParameters.ALIGNMENT_FORCE_RECOMPUTING, False)
	ref_proc_force = ref_mod[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)

	aligned_files: dict[str, dict[str, str]] = {}

	for modality in modalities:
		mod_name = modality[ModalityParameters.NAME]
		if mod_name == ref_name:
			continue

		mod_type = modality[ModalityParameters.TYPE]

		# Force recomputing if: global switch is on, or either modality's preprocessing was forced
		tgt_proc_force = modality[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)
		pair_force = global_force or ref_proc_force or tgt_proc_force

		logger.info(f"Aligning reference '{ref_name}' into '{mod_name}' coordinate space (force_recomputing={pair_force})")

		# The non-reference modality is the FIXED frame; the reference is the MOVING target.
		# This produces obsm['{mod_name}_spatial'] on the reference AnnData — the reference
		# spots expressed in the non-reference modality's coordinate system.
		aligner = DirectMappingAligner(
			path=dataset_path,
			reference_modality=modality_files[mod_name],
			target_modality=modality_files[ref_name],
			reference_modality_name=mod_name,
			target_modality_name=ref_name,
			reference_modality_type=mod_type,
			target_modality_type=ref_type,
		)

		strategy = modality.get(ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL)

		if strategy == AlignmentStrategy.PRE_ALIGNED:
			logger.info(f"Pre-aligned strategy for '{mod_name}' — using uniform alignment (no GUI)")
			report(state="running", stage="alignment", stage_index=2, total_stages=n_stages,
				   current_modality=mod_name,
				   message=f"Applying pre-aligned coordinates for '{mod_name}'...",
				   current_sample=None, current_sample_index=0, total_samples=0,
				   sub_step=None, sub_step_index=0, sub_step_total=0,
				   sub_step_progress=0, sub_step_items_total=0)
			aligned_files[mod_name] = aligner.uniform_aligned_dataset(force_recomputing=pair_force)
		elif aligner.is_alignment_needed(force_recomputing=pair_force):
			# Signal that alignment is starting — the GUI should show "Open Alignment Tool"
			report(state="alignment_waiting", stage="alignment", stage_index=2, total_stages=n_stages,
				   current_modality=mod_name,
				   message=f"Waiting for alignment of reference '{ref_name}' into '{mod_name}' space...",
				   sub_step=None, sub_step_index=0, sub_step_total=0,
				   sub_step_progress=0, sub_step_items_total=0)

			def _on_gui_done():
				report(state="running", stage="alignment", stage_index=2, total_stages=n_stages,
					   current_modality=mod_name,
					   message=f"Saving alignment results for '{mod_name}'...",
					   sub_step=None, sub_step_index=0, sub_step_total=0,
					   sub_step_progress=0, sub_step_items_total=0)

			aligned_files[mod_name] = aligner.align_dataset(force_recomputing=pair_force, on_gui_done=_on_gui_done)
		else:
			logger.info(f"Reference '{ref_name}' already aligned into '{mod_name}' space — loading cached files")
			report(state="running", stage="alignment", stage_index=2, total_stages=n_stages,
				   current_modality=mod_name,
				   message=f"Loading cached alignment for '{mod_name}'...",
				   current_sample=None, current_sample_index=0, total_samples=0,
				   sub_step=None, sub_step_index=0, sub_step_total=0,
				   sub_step_progress=0, sub_step_items_total=0)
			aligned_files[mod_name] = aligner.collect_aligned_files()

		report(state="running", stage="alignment", stage_index=2, total_stages=n_stages,
			   current_modality=mod_name,
			   message=f"Alignment complete for '{mod_name}'")

		logger.info(f"Alignment complete for '{mod_name}': {len(aligned_files[mod_name])} files")

	return aligned_files


def _run_annotation_transfer(
	config: dict,
	modality_files: dict[str, dict[str, str]],
	aligned_files: dict[str, dict[str, str]],
	report=None,
	stage_index: int = 3,
	n_stages: int = 5,
) -> dict[str, str]:
	"""
	Transfer spatial annotations from the annotation modality to the reference modality spots.

	Annotations are transferred per sample (using per-sample aligned coordinates as the
	reliable source), then the annotated per-sample files are concatenated into a merged
	annotated file.  This avoids any dependency on the merged aligned file, which may be
	absent or incomplete if a previous run was interrupted before its merge step.

	Returns
	-------
	dict
		{sample_id: annotated_file_path, "merged": annotated_merged_path}
	"""
	from focus.annotations import transfer_annotations

	dataset_path = config[ConfigParameters.DATASET_PATH]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ann_cfg = config[ConfigParameters.SPATIAL_ANNOTATIONS]
	ann_mod_name = ann_cfg[AnnotationsParameters.MODALITY_NAME]

	result: dict[str, str] = {}

	per_sample_ids = [sid for sid in modality_files[ref_name] if sid != "merged"]
	total_samples = len(per_sample_ids)

	# Build annotation paths once — one GeoJSON per sample in the annotation modality folder
	annotation_paths: dict[str, str] = {}
	for sid in per_sample_ids:
		mod_dir = os.path.join(dataset_path, sid, ann_mod_name)
		geojson_files = [f for f in os.listdir(mod_dir) if f.endswith('.geojson')]
		annotation_paths[sid] = os.path.join(mod_dir, geojson_files[0])

	# --- Per-sample annotation transfer ---
	per_sample_annotated: list[str] = []
	for i, sample_id in enumerate(per_sample_ids, 1):
		if report:
			report(state="running", stage="annotation_transfer",
				   stage_index=stage_index, total_stages=n_stages,
				   current_sample=sample_id, current_sample_index=i, total_samples=total_samples,
				   message=f"Transferring annotations for sample '{sample_id}'...",
				   sub_step=None, sub_step_index=0, sub_step_total=0,
				   sub_step_progress=0, sub_step_items_total=0)

		ref_path = modality_files[ref_name][sample_id]
		ref_sample = anndata.read_h5ad(ref_path)

		if ann_mod_name == ref_name:
			coords_sample = np.asarray(ref_sample.obsm['spatial'])
		else:
			ann_sample = anndata.read_h5ad(aligned_files[ann_mod_name][sample_id])
			coords_sample = np.asarray(ann_sample.obsm[f'{ann_mod_name}_spatial'])

		sids_sample = np.asarray(ref_sample.obs['sample_id'])
		ann_labels = transfer_annotations(coords_sample, sids_sample, annotation_paths)
		ref_sample.obs['spatial_annotation'] = pd.Categorical(ann_labels)

		sample_out = MODALITY_ANNOTATION(dataset_path, sample_id, ref_name, "h5ad")
		os.makedirs(os.path.dirname(sample_out), exist_ok=True)
		ref_sample.write(sample_out)
		result[sample_id] = sample_out
		per_sample_annotated.append(sample_out)
		logger.debug(f"Annotated sample '{sample_id}' saved to {sample_out}")

	# --- Merge annotated per-sample files ---
	if per_sample_annotated:
		if report:
			report(state="running", stage="annotation_transfer",
				   stage_index=stage_index, total_stages=n_stages,
				   current_sample=None, current_sample_index=total_samples, total_samples=total_samples,
				   message="Merging annotated samples...",
				   sub_step=None, sub_step_index=0, sub_step_total=0,
				   sub_step_progress=0, sub_step_items_total=0)

		merged_out = MODALITY_ANNOTATION_MERGED(dataset_path, ref_name, "h5ad")
		os.makedirs(os.path.dirname(merged_out), exist_ok=True)
		anndata.experimental.concat_on_disk(
			per_sample_annotated, merged_out,
			merge="same", uns_merge="same",
		)
		result["merged"] = merged_out
		logger.info(f"Annotated merged reference saved to {merged_out}")

	return result


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
			# aligned_files[mod_name] contains the aligned reference AnnData, which holds
			# obsm['{mod_name}_spatial'] — the reference spots in the image's coordinate space.
			# FeatureExtractorRegistration reads that key to locate where to extract patches.
			registered_files[mod_name] = engine.register_dataset(
				image_files=modality_files[mod_name],
				anchor_files=aligned_files[mod_name],
				image_name=mod_name,
				anchor_name=ref_name,
				min_max_rescale=reg_settings.get("min_max_rescale", True),
				force_recomputing=reg_settings.get("force_recomputing", False),
				background_color=reg_settings.get("background_color", None),
				patch_size=reg_settings.get("patch_size", 224),
			)

		elif reg_type == RegistrationType.SPOT_INTERPOLATION:
			engine = SpotInterpolationRegistration(path=dataset_path)
			# aligned_files[mod_name] contains the aligned reference AnnData with
			# obsm['{mod_name}_spatial'] (reference coords in the non-ref modality's space).
			# modality_files[mod_name] contains the non-ref modality's preprocessed AnnData
			# with its own obsm['spatial'] and feature matrix X.
			registered_files[mod_name] = engine.register_dataset(
				anchor_files=aligned_files[mod_name],
				target_files=modality_files[mod_name],
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
	annotation_files: dict | None = None,
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

	# Load anchor: prefer annotated file (from Stage 2.5) when available
	if annotation_files and "merged" in annotation_files:
		anchor_merged = annotation_files["merged"]
	else:
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

	# Propagate spatial annotations (if present) to top-level mdata.obs
	if 'spatial_annotation' in anchor_adata.obs.columns:
		mdata.obs['spatial_annotation'] = anchor_adata.obs['spatial_annotation'].values
		logger.info("Spatial annotation labels promoted to mdata.obs['spatial_annotation']")

	output_path = MULTIMODAL_DATASET(dataset_path, "h5mu")
	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	mdata.write(output_path)
	logger.info(f"MuData saved to {output_path} with {len(mod_dict)} modalities, {n_anchor_obs} observations")

	return output_path
