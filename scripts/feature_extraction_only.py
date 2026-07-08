#!/usr/bin/env python3
"""Run standalone, ANCHORLESS microscopy feature extraction from preprocessed OME-TIFFs.

This is a thin, standalone entry point for single-`microscopy_image`-modality FOCUS
projects. FOCUS requires ``reference_modality`` to name a declared modality, so with only
one modality it is unavoidably its own reference — and the real pipeline's registration
stage always skips the reference modality. That means ``registration_type:
"feature_extraction"`` (which normally extracts a Prov-GigaPath embedding for the image
patch centered on each *anchor* spot produced by the alignment stage) never actually runs
for such a project: there is no alignment step and no anchor coordinates.

This script uses the *free-form* mode of
``MicroscopyImageFeatureExtractor.extract_features`` (``patch_centers=None``): the image is
tiled into a non-overlapping grid of ``patch_size``x``patch_size`` patches, patches that are
>=99% background are dropped using the same background-detection code the anchor-based path
uses, and the rest are encoded with Prov-GigaPath — no alignment or anchor modality required.

Like ``align_only.py``, it consumes only the per-sample preprocessed OME-TIFF that lives
under ``{dataset_path}/{sample_id}/preprocessing/{modality}/`` and does not require raw
input data. Unlike ``align_only.py``, sample coverage is checked STRICTLY: every discovered
sample must already have a preprocessed file, or the script aborts before loading any model.

Output is written to the exact same paths the real anchor-based ``feature_extraction``
registration would use, so the result is a drop-in for any downstream FOCUS step that reads
registration output:

  - per-sample : {dataset_path}/{sample_id}/registration/{modality}_{sample_id}_processed_aligned_registered.h5ad
  - merged     : {dataset_path}/merged/registration/{modality}_merged_processed_aligned_registered.h5ad

(The filenames retain "processed_aligned_registered" even though no alignment ran — kept
for drop-in compatibility with any tooling that expects registration output at these paths.
A stamped ``uns['patch_mode'] = 'free_form'`` marker keeps this anchorless output from ever
being silently confused with a genuine future anchor-based cache at the same path, in either
direction: this script rejects a cache missing the marker, and the real pipeline's own
obs-count check rejects a free-form file since its patch count won't match an anchor's spot
count.)

Requires ``huggingface_token`` in the config (to download Prov-GigaPath) and, for reasonable
runtime, a CUDA GPU (falls back to CPU otherwise).

Usage
-----
    python scripts/feature_extraction_only.py -c /path/to/config.json
    python scripts/feature_extraction_only.py -c /path/to/config.json --debug

The config is the standard FOCUS JSON config. The declared ``modalities`` list must contain
exactly one entry, of type ``microscopy_image``. Its ``registration_settings`` (``patch_size``,
``background_color``, ``force_recomputing``) are read with the same defaults as the real
pipeline; ``registration_type`` itself is not checked, since this script always performs
feature extraction regardless of that field's value.
"""

import os
import sys
import json
import argparse
import logging

import anndata

# Allow running straight from a source checkout (scripts/ is a sibling of src/) without
# requiring an editable install. An installed ``focus`` still takes precedence if present.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(os.path.join(_SRC, "focus")) and _SRC not in sys.path:
	sys.path.insert(0, _SRC)

from focus import utils
from focus.constants import (
	ConfigParameters,
	ModalityParameters,
	ModalityType,
	RegistrationType,
	SegmentationBackgroundColor,
	MODALITY_PREPROCESSING,
	MODALITY_REGISTRATION,
	MODALITY_FILE_EXTENSION,
)
from focus.utils import write_h5ad_compat, release_memory
from focus.preprocessing._utils import discover_sample_ids
# Importing FeatureExtractorRegistration already pulls in timm/torch/huggingface_hub
# transitively (via microscopy_image.py) — unlike align_only.py, there is no lazy-import
# benefit to preserve here, since this script always needs the encoder.
from focus.registration.registration import FeatureExtractorRegistration
from focus.registration.microscopy_image import MicroscopyImageFeatureExtractor

_H5AD_COMPRESSION = "gzip"
_PATCH_MODE_FREE_FORM = "free_form"


def _select_microscopy_modality(config: dict, logger: logging.Logger) -> dict | None:
	"""Return the single declared modality, requiring it to be of type microscopy_image."""
	modalities = config[ConfigParameters.MODALITIES]
	if len(modalities) != 1:
		names = [m[ModalityParameters.NAME] for m in modalities]
		logger.error(
			f"This script requires exactly one declared modality, found {len(modalities)}: {names}. "
			f"It is designed for single-microscopy_image-modality projects."
		)
		return None

	modality = modalities[0]
	mod_type = modality[ModalityParameters.TYPE]
	if mod_type != ModalityType.MICROSCOPY_IMAGE:
		logger.error(
			f"Declared modality '{modality[ModalityParameters.NAME]}' has type '{mod_type}', "
			f"but this script only supports '{ModalityType.MICROSCOPY_IMAGE}'."
		)
		return None

	return modality


def _collect_preprocessed_files(
	dataset_path: str, modality_name: str, sample_ids: list, logger: logging.Logger,
) -> tuple[dict, list]:
	"""Build {sample_id: preprocessed_ome_tiff_path}, reporting any samples missing it."""
	ext = MODALITY_FILE_EXTENSION[ModalityType.MICROSCOPY_IMAGE]

	present: dict[str, str] = {}
	missing: list[str] = []
	for sid in sample_ids:
		path = MODALITY_PREPROCESSING(dataset_path, sid, modality_name, ext)
		if os.path.isfile(path):
			present[sid] = path
		else:
			missing.append(sid)

	logger.info(
		f"Modality '{modality_name}': {len(present)}/{len(sample_ids)} preprocessed '.{ext}' file(s) present."
	)
	return present, missing


def _cached_registration_valid(cached_adata, patch_size: int, background_color: str) -> bool:
	"""True when a cached free-form registration file can be safely reused.

	registration_type alone is not enough to validate the cache: a genuine future
	anchor-based feature_extraction run (e.g. after a second modality is added) would stamp
	the same registration_type at this same path. The patch_mode marker disambiguates in
	both directions — this script recomputes a cache missing the marker (rejecting a real
	anchor-based cache), and the real pipeline's own registration_cache_valid recomputes a
	free-form file at this path anyway, since its obs count (patch count) won't generally
	match the anchor's spot count.
	"""
	return (
		cached_adata.uns.get("registration_type") == RegistrationType.FEATURE_EXTRACTION
		and cached_adata.uns.get("patch_mode") == _PATCH_MODE_FREE_FORM
		and cached_adata.uns.get("patch_size") == patch_size
		and cached_adata.uns.get("background_color") == background_color
	)


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Run standalone, anchorless microscopy feature extraction from preprocessed OME-TIFFs."
	)
	parser.add_argument(
		"-c", "--config", type=str, required=True,
		help="Absolute path of the JSON config file (same format as the full pipeline).",
	)
	parser.add_argument(
		"--debug", action="store_true", default=False,
		help="Enable debug logging (shows all log levels including HTTP request logs).",
	)
	args = parser.parse_args()

	# Phase 1: console-only logging so validation errors are formatted.
	utils.setup_logging(debug=args.debug)
	logger = logging.getLogger("focus")

	if not os.path.exists(args.config):
		logger.error(f"Config file not found: {args.config}")
		sys.exit(1)

	try:
		with open(args.config, "r") as f:
			config = json.load(f)
	except json.JSONDecodeError as e:
		logger.error(f"Invalid JSON in config file '{args.config}': {e}")
		sys.exit(1)

	# Validate WITHOUT requiring raw modality input dirs — this script only needs each
	# modality's preprocessed output, exactly like align_only.py's alignment-only run.
	try:
		config = utils.parse_config(config, require_raw_inputs=False)
	except (TypeError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
		logger.error(f"Config validation failed: {e}")
		sys.exit(1)

	if not config[ConfigParameters.PERFORM_REGISTRATION]:
		logger.error("'perform_registration' is false in the config — nothing to do.")
		sys.exit(1)

	# require_raw_inputs=False also skips parse_config's own huggingface_token requirement
	# (gated behind that flag) — check it ourselves, since this script unconditionally needs
	# it to download Prov-GigaPath.
	hf_token = config[ConfigParameters.HUGGINGFACE_TOKEN]
	if not hf_token or not isinstance(hf_token, str):
		logger.error(
			"'huggingface_token' is required to run feature extraction (used to download Prov-GigaPath)."
		)
		sys.exit(1)

	# Phase 2: full logging — adds the focus.log file handler now that dataset_path is known.
	dataset_path = config[ConfigParameters.DATASET_PATH]
	utils.setup_logging(dataset_path, debug=args.debug)
	logger.info(f"Config loaded and validated (feature-extraction-only): {args.config}")

	modality = _select_microscopy_modality(config, logger)
	if modality is None:
		sys.exit(1)
	modality_name = modality[ModalityParameters.NAME]

	reg_settings = modality[ModalityParameters.REGISTRATION_SETTINGS]
	patch_size = reg_settings.get("patch_size", 224)
	background_color = reg_settings.get("background_color") or SegmentationBackgroundColor.WHITE
	force_recomputing = reg_settings.get("force_recomputing", False)
	logger.info(
		f"Modality '{modality_name}': patch_size={patch_size}, "
		f"background_color='{background_color}', force_recomputing={force_recomputing}"
	)

	sample_ids = discover_sample_ids(dataset_path, ignore_samples=config[ConfigParameters.IGNORE_SAMPLES])
	if not sample_ids:
		logger.error(f"No sample directories found in '{dataset_path}'.")
		sys.exit(1)
	logger.info(f"Discovered {len(sample_ids)} sample(s): {sample_ids}")

	# Strict check: every discovered sample must already be preprocessed, checked BEFORE any
	# model loading (unlike align_only.py's lenient per-pair warning policy).
	preprocessed_files, missing = _collect_preprocessed_files(dataset_path, modality_name, sample_ids, logger)
	if missing:
		ext = MODALITY_FILE_EXTENSION[ModalityType.MICROSCOPY_IMAGE]
		logger.error(
			f"Modality '{modality_name}' is missing preprocessed '.{ext}' file(s) for "
			f"{len(missing)}/{len(sample_ids)} sample(s): {missing}. Expected at "
			f"{{dataset_path}}/{{sample}}/preprocessing/{modality_name}/. Aborting — this script "
			f"requires every discovered sample to already be preprocessed."
		)
		sys.exit(1)

	logger.info("=" * 60)
	logger.info("Running standalone feature extraction (anchorless, free-form patch grid)")
	logger.info("=" * 60)

	# engine is reused only for _load_ome_tiff and _merge_samples — cheap __init__, no model
	# loading. The pretrained MicroscopyImageFeatureExtractor is loaded lazily below, only if
	# some sample actually needs (re)computation.
	engine = FeatureExtractorRegistration(path=dataset_path, hf_token=hf_token)
	feature_extractor: MicroscopyImageFeatureExtractor | None = None
	registered_files: dict[str, str] = {}
	all_cached = True

	for sample_idx, sample_id in enumerate(sample_ids, 1):
		logger.info(f"[{sample_idx}/{len(sample_ids)}] Extracting features for sample '{sample_id}'")

		registered_file = MODALITY_REGISTRATION(dataset_path, sample_id, modality_name, "h5ad")
		os.makedirs(os.path.dirname(registered_file), exist_ok=True)

		if os.path.exists(registered_file) and not force_recomputing:
			cached = anndata.read_h5ad(registered_file)
			if _cached_registration_valid(cached, patch_size, background_color):
				logger.info(f"Using cached free-form registration for sample '{sample_id}'")
				registered_files[sample_id] = registered_file
				del cached
				continue
			logger.warning(
				f"Cached registration for '{sample_id}' is stale or from a different mode; recomputing."
			)
			del cached

		all_cached = False

		if feature_extractor is None:
			logger.info("Loading feature extractor model (Prov-GigaPath)...")
			feature_extractor = MicroscopyImageFeatureExtractor(path=dataset_path, hf_token=hf_token)

		image_data = engine._load_ome_tiff(preprocessed_files[sample_id])
		embeddings, center_coordinates = feature_extractor.extract_features(
			image=image_data,
			patch_centers=None,
			background_color=background_color,
			patch_size=patch_size,
			step_reporter=None,
		)
		if embeddings.shape[0] == 0:
			logger.warning(f"Sample '{sample_id}': no foreground patches found (fully background image?).")

		adata = anndata.AnnData(
			X=embeddings,
			obsm={"spatial": center_coordinates},
			obs={"sample_id": [sample_id] * embeddings.shape[0]},
		)
		adata.uns["registration_type"] = RegistrationType.FEATURE_EXTRACTION
		adata.uns["patch_mode"] = _PATCH_MODE_FREE_FORM
		adata.uns["patch_size"] = patch_size
		adata.uns["background_color"] = background_color

		write_h5ad_compat(adata, registered_file, compression=_H5AD_COMPRESSION)
		registered_files[sample_id] = registered_file
		logger.debug(f"Saved {embeddings.shape[0]} patch embeddings for sample '{sample_id}'")

		del image_data, embeddings, center_coordinates, adata

	if feature_extractor is not None:
		del feature_extractor

	# Merge across samples — _merge_samples only reuses a cached merged file when
	# all_per_sample_cached is True, which is only set here once every per-sample file has
	# already passed the free-form-aware _cached_registration_valid check above, so merge-level
	# reuse is fully gated by the per-sample gate even though _merge_samples itself does not
	# check patch_mode/patch_size/background_color.
	registered_files = engine._merge_samples(
		registered_files, modality_name,
		force_recomputing=force_recomputing, all_per_sample_cached=all_cached,
	)

	del engine
	release_memory(gpu=True)

	logger.info("=" * 60)
	logger.info("Feature extraction complete.")
	merged = registered_files.get("merged")
	per_sample = {k: v for k, v in registered_files.items() if k != "merged"}
	logger.info(
		f"  '{modality_name}': {len(per_sample)} per-sample file(s)"
		+ (f"; merged: {merged}" if merged else "; (no merged file produced)")
	)
	for sid, path in per_sample.items():
		logger.info(f"      {sid}: {path}")
	logger.info("=" * 60)

	sys.exit(0)


if __name__ == "__main__":
	main()
