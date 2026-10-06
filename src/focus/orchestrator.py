import os, logging, anndata
import numpy as np
import scipy.sparse
import pandas as pd
import mudata

from focus.constants import (
	ConfigParameters, ModalityParameters, RegistrationType,
	ModalityType, MODALITY_FILE_EXTENSION, MULTIMODAL_DATASET,
	AlignmentStrategy, AnnotationsParameters, AnnotationFileType,
	MODALITY_ANNOTATION, MODALITY_ANNOTATION_MERGED, DISPLAY_NAMES,
	IMAGE_MODALITY_TYPES, SPOT_MODALITY_TYPES,
	MsiPreprocessingParams, MsiSampleType,
)
from focus.utils import write_h5ad_compat, concat_on_disk_compat, write_h5mu_compat, release_memory
from focus.preprocessing import preprocess_modality
from focus.reporting import StepReporter, use_reporter, get_reporter
from focus.alignment.alignment import DirectMappingAligner
# NOTE: the registration engines are imported lazily inside _run_registration() rather
# than here, because focus.registration.microscopy_image imports torch / timm /
# huggingface_hub at module load. Keeping these out of the module top lets callers that
# only need the alignment stage (e.g. scripts/align_only.py via _run_alignment) import
# this module without pulling in the PyTorch / GigaPath stack.

logger = logging.getLogger("focus.orchestrator")

_IMAGE_MODALITIES = IMAGE_MODALITY_TYPES
_SPOT_MODALITIES = SPOT_MODALITY_TYPES


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

	reporter = StepReporter(callback=progress_callback)
	# Code without a reporter handle (deep helpers) reports through get_reporter().
	use_reporter(reporter)
	try:
		output_files = _run_stages(config, reporter)
		reporter.finish()
		return output_files
	except Exception as e:
		reporter.fail(e)
		raise
	finally:
		use_reporter(None)


def _run_stages(config: dict, reporter: StepReporter) -> dict:
	"""The pipeline stages, each reported as a stage context (see focus.reporting)."""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	output_files: dict = {}

	ann_enabled = config.get(ConfigParameters.SPATIAL_ANNOTATIONS) is not None
	n_stages = 5 if ann_enabled else 4

	# --- Stage 1: Preprocessing (always runs, caching is internal) ---
	modality_files: dict[str, dict[str, str]] = {}
	with reporter.stage("preprocessing", 1, n_stages):
		total_modalities = len(modalities)
		for mod_idx, modality in enumerate(modalities, 1):
			mod_name = modality[ModalityParameters.NAME]
			mod_type = modality[ModalityParameters.TYPE]
			with reporter.modality(mod_name, mod_idx, total_modalities):
				modality_files[mod_name] = preprocess_modality(
					path=dataset_path,
					modality_name=mod_name,
					modality_type=mod_type,
					preprocessing_settings=modality[ModalityParameters.PROCESSING_SETTINGS],
					step_reporter=reporter,
					ignore_samples=config.get(ConfigParameters.IGNORE_SAMPLES, []),
				)

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

	# Preprocessing is done — every per-sample AnnData was read from and written to disk
	# inside preprocess_modality and is already out of scope. Reclaim before the next stage.
	release_memory(gpu=False)

	# Compute effective force_recomputing flags, cascading upstream changes downstream:
	# preprocessing force → alignment force → registration force
	alignment_force, registration_force = _compute_effective_force_flags(config)

	# --- Stage 2: Alignment ---
	aligned_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_ALIGNMENT]:
		with reporter.stage("alignment", 2, n_stages):
			aligned_files = _run_alignment(config, modality_files, reporter, force_overrides=alignment_force)
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
		with reporter.stage("annotation_transfer", 3, n_stages):
			annotation_files = _run_annotation_transfer(config, modality_files, aligned_files, reporter)
		ann_merged: list[str] = []
		ann_per_modality: dict[str, list[str]] = {}
		for sid, path in annotation_files.items():
			if sid == "merged":
				ann_merged.append(path)
			else:
				ann_per_modality.setdefault("reference", []).append(path)
		if ann_merged or ann_per_modality:
			output_files["annotations"] = {"merged": ann_merged, "per_modality": ann_per_modality}

	stage_reg = 4 if ann_enabled else 3
	stage_mudata = 5 if ann_enabled else 4

	# --- Stage 3/4: Registration ---
	registered_files: dict[str, dict[str, str]] = {}
	if config[ConfigParameters.PERFORM_REGISTRATION]:
		with reporter.stage("registration", stage_reg, n_stages):
			registered_files = _run_registration(config, modality_files, aligned_files, reporter,
											   force_overrides=registration_force)
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
		with reporter.stage("compiling", stage_mudata, n_stages):
			mudata_path = _compile_mudata(config, modality_files, registered_files, annotation_files, reporter)
		if mudata_path:
			output_files["multimodal"] = {"merged": [mudata_path], "per_modality": {}}
		# Covers _compile_mudata's early-return paths (all spots filtered / <2 modalities),
		# which skip its internal release.
		release_memory(gpu=False)

	logger.info("FOCUS pipeline completed successfully.")
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


def _is_microgrid_experiment(config: dict) -> bool:
	"""Return True if any MSI modality in the config is a microgrid acquisition.

	Microgrid alignment matches the pattern of foreground spots between grids, so in that case
	every spot modality is shown in the alignment GUI at its exact spot positions (see
	DirectMappingAligner exact_spots).
	"""
	for m in config[ConfigParameters.MODALITIES]:
		if m[ModalityParameters.TYPE] != ModalityType.MSI:
			continue
		settings = m.get(ModalityParameters.PROCESSING_SETTINGS, {}) or {}
		if settings.get(MsiPreprocessingParams.SAMPLE_TYPE, MsiSampleType.TISSUE) == MsiSampleType.MICROGRID:
			return True
	return False


def _compute_effective_force_flags(config: dict) -> tuple[dict[str, bool], dict[str, bool]]:
	"""
	Derive effective force_recomputing flags for alignment and registration by cascading
	upstream flags downstream through the pipeline dependency chain.

	Cascade rules (OR-combined per modality):
	- reference modality preprocessing forced → force ALL alignment pairs + ALL registrations
	- target modality preprocessing forced    → force THAT pair's alignment + THAT registration
	- alignment forced (any cause above)      → force THAT registration

	Returns
	-------
	alignment_force : dict[str, bool]
		Effective alignment force per non-reference modality name.
	registration_force : dict[str, bool]
		Effective registration force per non-reference modality name.
	"""
	ref_mod = _get_reference_modality(config)
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_preproc_force: bool = ref_mod[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)

	alignment_force: dict[str, bool] = {}
	registration_force: dict[str, bool] = {}

	for modality in config[ConfigParameters.MODALITIES]:
		mod_name = modality[ModalityParameters.NAME]
		if mod_name == ref_name:
			continue

		tgt_preproc_force: bool = modality[ModalityParameters.PROCESSING_SETTINGS].get("force_recomputing", False)

		effective_align = (
			modality.get(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False)
			or ref_preproc_force
			or tgt_preproc_force
		)
		alignment_force[mod_name] = effective_align

		explicit_reg_force: bool = modality.get(ModalityParameters.REGISTRATION_SETTINGS, {}).get("force_recomputing", False)
		registration_force[mod_name] = explicit_reg_force or effective_align

	return alignment_force, registration_force


def _run_alignment(config: dict, modality_files: dict, reporter: StepReporter,
				   force_overrides: dict[str, bool] | None = None) -> dict:
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

	aligned_files: dict[str, dict[str, str]] = {}

	exact_spots = _is_microgrid_experiment(config)
	if exact_spots:
		reporter.detail("Microgrid MSI modality found: alignment shows exact foreground spots with no coarsening or clustering")

	targets = [m for m in modalities if m[ModalityParameters.NAME] != ref_name]
	for pair_idx, modality in enumerate(targets, 1):
		mod_name = modality[ModalityParameters.NAME]
		mod_type = modality[ModalityParameters.TYPE]

		pair_force = (force_overrides or {}).get(
			mod_name, modality.get(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False)
		)

		with reporter.modality(mod_name, pair_idx, len(targets)):
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
				exact_spots=exact_spots,
				reporter=reporter,
			)

			strategy = modality.get(ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL)

			if strategy == AlignmentStrategy.PRE_ALIGNED:
				reporter.detail(f"Pre-aligned: '{ref_name}' coordinates are used as they are, without the alignment tool")
				aligned_files[mod_name] = aligner.uniform_aligned_dataset(force_recomputing=pair_force)
			elif aligner.is_alignment_needed(force_recomputing=pair_force):
				# The GUI shows "Open alignment tool" while the state is alignment_waiting.
				reporter.set_state("alignment_waiting")
				reporter.detail(f"Waiting for the alignment tool: align '{ref_name}' into '{mod_name}' space")
				aligned_files[mod_name] = aligner.align_dataset(
					force_recomputing=pair_force, on_gui_done=lambda: reporter.set_state("running"),
				)
			else:
				aligned_files[mod_name] = aligner.collect_aligned_files()

			# Drop this pair's aligner (and its accumulated _aligned_coordinates / GUI
			# payloads) before the next pair. Its interactive server has already shut down.
			del aligner
			release_memory(gpu=False)

	return aligned_files


def _run_annotation_transfer(
	config: dict,
	modality_files: dict[str, dict[str, str]],
	aligned_files: dict[str, dict[str, str]],
	reporter: StepReporter | None = None,
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

	reporter = reporter or get_reporter()
	result: dict[str, str] = {}

	per_sample_ids = [sid for sid in modality_files[ref_name] if sid != "merged"]

	# Build annotation paths once — one GeoJSON per sample in the annotation modality folder
	annotation_paths: dict[str, str] = {}
	for sid in per_sample_ids:
		mod_dir = os.path.join(dataset_path, sid, ann_mod_name)
		geojson_files = [f for f in os.listdir(mod_dir) if f.endswith('.geojson')]
		annotation_paths[sid] = os.path.join(mod_dir, geojson_files[0])

	# --- Per-sample annotation transfer ---
	per_sample_annotated: list[str] = []
	for sample_id in reporter.tqdm(per_sample_ids, 1, 2, "Transferring annotations", unit="sample"):
		ref_path = modality_files[ref_name][sample_id]
		ref_sample = None
		ann_sample = None
		try:
			ref_sample = anndata.read_h5ad(ref_path, backed='r')

			if ann_mod_name == ref_name:
				coords_sample = np.asarray(ref_sample.obsm['spatial'])
			else:
				ann_sample = anndata.read_h5ad(aligned_files[ann_mod_name][sample_id], backed='r')
				coords_sample = np.asarray(ann_sample.obsm[f'{ann_mod_name}_spatial'])
				ann_sample.file.close()
				ann_sample = None

			sids_sample = np.asarray(ref_sample.obs['sample_id'])
			ann_labels = transfer_annotations(coords_sample, sids_sample, annotation_paths)
			n_annotated = int(np.sum(ann_labels != None))  # noqa: E711
			reporter.detail(f"{n_annotated}/{len(coords_sample)} spots annotated")
			ref_sample.obs['spatial_annotation'] = pd.Categorical(ann_labels)

			sample_out = MODALITY_ANNOTATION(dataset_path, sample_id, ref_name, "h5ad")
			os.makedirs(os.path.dirname(sample_out), exist_ok=True)
			write_h5ad_compat(ref_sample, sample_out)
			result[sample_id] = sample_out
			per_sample_annotated.append(sample_out)
		finally:
			# Guarantee the backed HDF5 handles are released even if the spatial
			# query or the write raises — otherwise an mmap/file handle leaks per sample.
			for _h in (ann_sample, ref_sample):
				if _h is not None:
					try:
						_h.file.close()
					except Exception:
						pass

	# --- Merge annotated per-sample files ---
	if per_sample_annotated:
		reporter.step(2, 2, "Merging annotated samples")
		merged_out = MODALITY_ANNOTATION_MERGED(dataset_path, ref_name, "h5ad")
		os.makedirs(os.path.dirname(merged_out), exist_ok=True)
		concat_on_disk_compat(
			per_sample_annotated, merged_out,
			merge="same", uns_merge="same",
		)
		result["merged"] = merged_out
		logger.debug(f"Annotated merged reference saved to {merged_out}")

	release_memory(gpu=False)
	return result


def _run_registration(config: dict, modality_files: dict, aligned_files: dict, step_reporter: StepReporter,
					  force_overrides: dict[str, bool] | None = None) -> dict:
	"""
	Register each non-reference modality that has a registration_type != 'none'.

	For FeatureExtraction (image modalities):
		Extracts patch embeddings from the image at anchor spot locations.
	For SpotInterpolation (spot modalities):
		Gaussian-weighted interpolation of target features onto anchor spot grid.
	For RamanPixelInterpolation (raman modality):
		Gaussian-weighted interpolation of Raman OME-TIFF pixels onto anchor spot grid.

	Returns
	-------
	dict
		{modality_name: {sample_id: registered_file_path}}
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]

	registered_files: dict[str, dict[str, str]] = {}

	targets = [
		m for m in modalities
		if m[ModalityParameters.NAME] != ref_name and m[ModalityParameters.REGISTRATION_TYPE] != RegistrationType.NONE
	]
	for reg_idx, modality in enumerate(targets, 1):
		mod_name = modality[ModalityParameters.NAME]
		reg_type = modality[ModalityParameters.REGISTRATION_TYPE]

		reg_settings = modality[ModalityParameters.REGISTRATION_SETTINGS]
		with step_reporter.modality(mod_name, reg_idx, len(targets)):
			step_reporter.detail(f"Method: {DISPLAY_NAMES[reg_type]}")
			if not _register_modality(
				reg_type, mod_name, ref_name, dataset_path, config, reg_settings,
				modality_files, aligned_files, registered_files, step_reporter, force_overrides,
			):
				continue

		# The engine (and, for FeatureExtraction, the pretrained GigaPath model it loaded)
		# went out of scope with _register_modality: free it and any cached GPU blocks
		# before the next modality / stage.
		release_memory(gpu=True)

	return registered_files


def _register_modality(reg_type, mod_name, ref_name, dataset_path, config, reg_settings,
					   modality_files, aligned_files, registered_files, step_reporter, force_overrides) -> bool:
	"""Run one modality's registration engine; False when the type is unknown."""
	# Imported here (not at module top) so that importing this module to run only the
	# alignment stage does not load torch / timm / huggingface_hub. See the import note
	# at the top of this file.
	from focus.registration.registration import FeatureExtractorRegistration, SpotInterpolationRegistration
	from focus.registration.spot_aggregation import SpotAggregationRegistration
	from focus.registration.raman_pixel import RamanPixelInterpolationRegistration

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
			force_recomputing=(force_overrides or {}).get(mod_name, reg_settings.get("force_recomputing", False)),
			background_color=reg_settings.get("background_color", None),
			patch_size=reg_settings.get("patch_size", 224),
			step_reporter=step_reporter,
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
			force_recomputing=(force_overrides or {}).get(mod_name, reg_settings.get("force_recomputing", False)),
			step_reporter=step_reporter,
		)

	elif reg_type == RegistrationType.SPOT_AGGREGATION:
		engine = SpotAggregationRegistration(path=dataset_path)
		# Same inputs as SPOT_INTERPOLATION; the only difference is the per-footprint
		# reduction (sum of target spots instead of a Gaussian-weighted average).
		registered_files[mod_name] = engine.register_dataset(
			anchor_files=aligned_files[mod_name],
			target_files=modality_files[mod_name],
			anchor_name=ref_name,
			target_name=mod_name,
			force_recomputing=(force_overrides or {}).get(mod_name, reg_settings.get("force_recomputing", False)),
			step_reporter=step_reporter,
		)

	elif reg_type == RegistrationType.RAMAN_PIXEL_INTERPOLATION:
		engine = RamanPixelInterpolationRegistration(path=dataset_path)
		# anchor_files[mod_name]: aligned anchor h5ad with obsm['{mod_name}_spatial']
		#   (anchor spots in the Raman image's pixel coordinate system).
		# modality_files[mod_name]: ASHLAR-stitched Raman OME-TIFF files.
		registered_files[mod_name] = engine.register_dataset(
			anchor_files=aligned_files[mod_name],
			target_files=modality_files[mod_name],
			anchor_name=ref_name,
			target_name=mod_name,
			force_recomputing=(force_overrides or {}).get(mod_name, reg_settings.get("force_recomputing", False)),
			step_reporter=step_reporter,
		)

	else:
		step_reporter.warning(f"Unknown registration type '{reg_type}', skipping")
		return False

	return True


def _compute_valid_spot_mask(
	mod_dict: dict,
	ref_name: str,
	n_obs: int,
) -> np.ndarray | None:
	"""
	Returns a bool mask (n_obs,) — True where a spot has non-zero coverage in
	every non-anchor modality. Returns None when no filtering is needed.

	Zero-vector rows arise from two sources:
	- SpotInterpolationRegistration: no target spots fall within the anchor
	  spot's spatial footprint (spot lies outside the target tissue section).
	- FeatureExtractorRegistration: the patch extracted at the anchor spot
	  location is pure background (>= 99% background pixels).
	"""
	mask = np.ones(n_obs, dtype=bool)
	any_empty = False

	for mod_name, adata in mod_dict.items():
		if mod_name == ref_name:
			continue
		X = adata.X
		if scipy.sparse.issparse(X):
			X_csr = X.tocsr().copy()
			X_csr.eliminate_zeros()
			mod_mask = np.asarray(X_csr.getnnz(axis=1) > 0)
		else:
			mod_mask = ~np.all(X == 0.0, axis=1)
		n_empty = int(np.sum(~mod_mask))
		if n_empty > 0:
			logger.debug(f"Coverage check '{mod_name}': {n_empty}/{n_obs} uncovered spots (all-zero features)")
			any_empty = True
		mask &= mod_mask

	return mask if any_empty else None


def _compile_mudata(
	config: dict,
	modality_files: dict,
	registered_files: dict,
	annotation_files: dict | None = None,
	reporter: StepReporter | None = None,
) -> str | None:
	"""
	Compile a final MuData object with paired observations across modalities.

	Returns the path to the saved MuData file, or None if compilation was skipped.
	"""
	reporter = reporter or get_reporter()
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	ref_mod = _get_reference_modality(config)

	# Only compile if anchor is spot-based
	if ref_mod[ModalityParameters.TYPE] not in _SPOT_MODALITIES:
		reporter.warning("The reference modality is not spot-based: the multimodal dataset is not compiled")
		return None

	# Load anchor: prefer annotated file (from Stage 2.5) when available
	if annotation_files and "merged" in annotation_files:
		anchor_merged = annotation_files["merged"]
	else:
		anchor_merged = modality_files[ref_name].get("merged")
	if anchor_merged is None or not os.path.exists(anchor_merged):
		reporter.warning(f"No merged file for the reference '{ref_name}': the multimodal dataset is not compiled")
		return None

	reporter.step(1, 3, "Loading registered modalities")
	anchor_adata = anndata.read_h5ad(anchor_merged)

	# Shared obs/obsm/uns come from the anchor
	shared_spatial = np.asarray(anchor_adata.obsm['spatial'], dtype=np.float32)
	shared_sample_id = anchor_adata.obs['sample_id'].copy()
	shared_spot_size = anchor_adata.uns.get('spot_size', None)
	n_anchor_obs = anchor_adata.n_obs

	anchor_sample_ids = anchor_adata.obs['sample_id'].astype(str).to_numpy()

	# Build modality dict for MuData
	mod_dict: dict[str, anndata.AnnData] = {}

	# Add anchor modality. Reuse anchor_adata directly rather than copying it: the shared
	# spatial / sample_id / spot_size values were already captured above as independent
	# objects (np.asarray with a dtype change, .copy(), .to_numpy()), so dropping these keys
	# in place leaves them intact, and we avoid holding a second full copy of the anchor
	# matrix. Nothing reads the original anchor_adata after this point.
	if 'spatial' in anchor_adata.obsm:
		del anchor_adata.obsm['spatial']
	if 'spot_size' in anchor_adata.uns:
		del anchor_adata.uns['spot_size']
	mod_dict[ref_name] = anchor_adata

	# Add registered non-anchor modalities. We do NOT overwrite obs_names yet —
	# obs_name synchronisation happens after the zero-vector filter so the
	# anchor's filtered slice is what gets propagated.
	for modality in modalities:
		mod_name = modality[ModalityParameters.NAME]
		if mod_name == ref_name:
			continue

		if mod_name not in registered_files:
			logger.debug(f"No registration output for '{mod_name}', skipping in MuData.")
			continue

		merged_path = registered_files[mod_name].get("merged")
		if merged_path is None or not os.path.exists(merged_path):
			reporter.warning(f"'{mod_name}' has no merged registration file and is left out")
			continue

		reg_adata = anndata.read_h5ad(merged_path)

		if reg_adata.n_obs != n_anchor_obs:
			reporter.warning(
				f"'{mod_name}' is left out: {reg_adata.n_obs} observations vs {n_anchor_obs} in the reference"
			)
			del reg_adata
			continue

		# Verify row alignment against the anchor by comparing per-row sample_id.
		# Matching counts alone is not enough: the per-sample concat order in
		# registration._merge_samples can diverge from preprocessing's order, and
		# silently mis-pairing spots produces a MuData that mudata cannot read back.
		if 'sample_id' not in reg_adata.obs.columns:
			reporter.warning(
				f"'{mod_name}' is left out: it has no obs['sample_id'], so its rows cannot be "
				"matched to the reference"
			)
			del reg_adata
			continue
		reg_sample_ids = reg_adata.obs['sample_id'].astype(str).to_numpy()
		if not np.array_equal(reg_sample_ids, anchor_sample_ids):
			reporter.warning(
				f"'{mod_name}' is left out: its sample order differs from the reference '{ref_name}'"
			)
			del reg_adata
			continue

		if 'spatial' in reg_adata.obsm:
			del reg_adata.obsm['spatial']
		if 'spot_size' in reg_adata.uns:
			del reg_adata.uns['spot_size']

		mod_dict[mod_name] = reg_adata

	# Filter anchor spots with no coverage in any target modality
	reporter.step(2, 3, "Filtering uncovered spots")
	valid_mask = _compute_valid_spot_mask(mod_dict, ref_name, n_anchor_obs)
	if valid_mask is not None:
		n_removed = int(np.sum(~valid_mask))
		reporter.detail(
			f"{n_removed}/{n_anchor_obs} reference spots removed: no coverage in at least one modality"
		)
		mod_dict = {k: v[valid_mask].copy() for k, v in mod_dict.items()}
		shared_spatial = shared_spatial[valid_mask]
		shared_sample_id = shared_sample_id[valid_mask]
		n_anchor_obs = int(np.sum(valid_mask))
		if n_anchor_obs == 0:
			reporter.warning(
				"No reference spot has coverage in all modalities: the multimodal dataset is not compiled"
			)
			return None

	if len(mod_dict) < 2:
		reporter.warning("Only one modality is available: the multimodal dataset is not compiled")
		return None

	# Synchronise obs_names across modalities using the (possibly filtered) anchor.
	anchor_obs_names = mod_dict[ref_name].obs_names.tolist()
	for mod_name, adata in mod_dict.items():
		if mod_name == ref_name:
			continue
		adata.obs_names = anchor_obs_names

	# Namespace var_names per modality. MSI uses bare integer strings ("0", "1", ...)
	# and Raman/microscopy may carry similarly generic names, so cross-modality
	# var_name collisions are possible. Mudata's `_update_attr` takes a fragile
	# duplicates/intersection branch in that case and raises a KeyError on read.
	# Prefixing var_names with the modality name eliminates that risk and keeps
	# mudata on its simple, lossless code path on both write and read.
	for mod_name, adata in mod_dict.items():
		adata.var_names = pd.Index(
			[f"{mod_name}:{v}" for v in adata.var_names], dtype=object
		)
		adata.var_names_make_unique()

	logger.debug(
		"Compiling MuData: "
		+ ", ".join(
			f"{k}: n_obs={v.n_obs} n_vars={v.n_vars} "
			f"obs_unique={v.obs_names.is_unique} var_unique={v.var_names.is_unique}"
			for k, v in mod_dict.items()
		)
	)

	# Build MuData
	reporter.step(3, 3, "Writing MuData")
	mdata = mudata.MuData(mod_dict)

	mdata.obs['sample_id'] = shared_sample_id.values
	mdata.obsm['spatial'] = shared_spatial
	if shared_spot_size is not None:
		mdata.uns['spot_size'] = shared_spot_size

	# Propagate spatial annotations (if present) to top-level mdata.obs
	if 'spatial_annotation' in mod_dict[ref_name].obs.columns:
		mdata.obs['spatial_annotation'] = mod_dict[ref_name].obs['spatial_annotation'].values
		logger.debug("Spatial annotation labels promoted to mdata.obs['spatial_annotation']")

	output_path = MULTIMODAL_DATASET(dataset_path, "h5mu")
	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	write_h5mu_compat(mdata, output_path)
	reporter.detail(f"Saved {output_path}: {len(mod_dict)} modalities, {n_anchor_obs} observations")

	# Drop the compiled MuData and every modality AnnData it holds (anchor + all registered
	# modalities are resident at once here) before returning to the orchestrator.
	del mdata, mod_dict
	release_memory(gpu=False)
	return output_path
