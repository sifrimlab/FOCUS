#!/usr/bin/env python3
"""Run ONLY the FOCUS alignment stage, starting from already-preprocessed files.

This is a thin, standalone entry point for the case where preprocessing has already
been done (one preprocessed AnnData/OME-TIFF per sample per modality) and you only
want to (re)produce the alignment outputs without re-running preprocessing and
without touching registration or MuData compilation.

Unlike the full pipeline (``focus -c config.json``), this script does NOT require the
raw input data to be present: it consumes only the per-sample preprocessed files that
live under ``{dataset_path}/{sample_id}/preprocessing/{modality}/``. The raw modality
input directories, the MSI lipid-annotation database, the HuggingFace token, and any
GeoJSON annotation files are not needed and their presence is not checked.

It reuses the exact same config format and the same alignment machinery as the full
pipeline (``focus.orchestrator._run_alignment`` → ``focus.alignment.DirectMappingAligner``),
so the outputs are identical to what a normal run would produce:

  - per-sample : {dataset_path}/{sample_id}/alignment/{ref}_{sample_id}_processed_aligned.h5ad
                 (the reference modality, with obsm['{target}_spatial'] added per target)
  - merged     : {dataset_path}/merged/alignment/{ref}_merged_processed_aligned.h5ad

For modalities whose ``alignment_strategy`` is ``manual`` (the default), the interactive
alignment GUI is launched at http://localhost:8000 and the script blocks until each
sample is confirmed, exactly as in a normal CLI run. Modalities using ``pre_aligned``
run headlessly with no GUI.

Usage
-----
    python scripts/align_only.py -c /path/to/config.json
    python scripts/align_only.py -c /path/to/config.json --debug

The config is the standard FOCUS JSON config (same as the full pipeline). Only the
fields relevant to alignment are used: ``dataset_path``, ``modalities`` (``name``,
``type``, ``alignment_strategy``, ``alignment_force_recomputing``),
``reference_modality``, and ``ignore_samples``.
"""

import os
import sys
import json
import argparse
import logging

# Allow running straight from a source checkout (scripts/ is a sibling of src/) without
# requiring an editable install. An installed ``focus`` still takes precedence if present.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(os.path.join(_SRC, "focus")) and _SRC not in sys.path:
	sys.path.insert(0, _SRC)

from focus import utils, orchestrator
from focus.constants import (
	ConfigParameters,
	ModalityParameters,
	MODALITY_PREPROCESSING,
	MODALITY_FILE_EXTENSION,
)
from focus.preprocessing._utils import discover_sample_ids


def _collect_preprocessed_modality_files(config: dict, logger: logging.Logger) -> tuple[dict, list]:
	"""Build {modality_name: {sample_id: preprocessed_path}} from existing files on disk.

	Mirrors the dict shape that ``preprocess_modality`` returns to the orchestrator, but
	without running preprocessing: it points at the per-sample preprocessed files and
	includes only those that actually exist. Samples missing a modality's file are simply
	omitted from that modality's dict (and reported), so a pair is aligned over the samples
	present in both the reference and the target. No ``"merged"`` key is added: alignment
	does not use it, and ``DirectMappingAligner`` ignores it anyway.
	"""
	dataset_path = config[ConfigParameters.DATASET_PATH]
	modalities = config[ConfigParameters.MODALITIES]
	ignore_samples = config.get(ConfigParameters.IGNORE_SAMPLES, [])

	sample_ids = discover_sample_ids(dataset_path, ignore_samples=ignore_samples)
	logger.info(f"Discovered {len(sample_ids)} sample(s): {sample_ids}")

	modality_files: dict[str, dict[str, str]] = {}
	for modality in modalities:
		name = modality[ModalityParameters.NAME]
		mod_type = modality[ModalityParameters.TYPE]
		ext = MODALITY_FILE_EXTENSION[mod_type]

		present: dict[str, str] = {}
		missing: list[str] = []
		for sid in sample_ids:
			path = MODALITY_PREPROCESSING(dataset_path, sid, name, ext)
			if os.path.isfile(path):
				present[sid] = path
			else:
				missing.append(sid)

		modality_files[name] = present
		logger.info(
			f"Modality '{name}' ({mod_type}): "
			f"{len(present)}/{len(sample_ids)} preprocessed '.{ext}' file(s) present."
		)
		if missing:
			logger.warning(f"  No preprocessed '.{ext}' for sample(s): {missing}")

	return modality_files, sample_ids


def _validate_alignable(config: dict, modality_files: dict, logger: logging.Logger) -> bool:
	"""Check there is something to align (lenient policy).

	Returns True if alignment can proceed. Hard failures (returns False):
	- the reference modality has zero preprocessed files;
	- no non-reference modality is declared;
	- a non-reference modality shares zero samples with the reference (nothing to align
	  for that pair, which is usually a mistake or a path mismatch).

	Per-sample gaps within an otherwise-overlapping pair are only warned about (already
	logged by the collector): the aligner aligns the reference∩target intersection.
	"""
	ref_name = config[ConfigParameters.REFERENCE_MODALITY]
	modalities = config[ConfigParameters.MODALITIES]

	ref_files = modality_files.get(ref_name, {})
	if not ref_files:
		logger.error(
			f"Reference modality '{ref_name}' has no preprocessed files, so there is nothing to align. "
			f"Expected files at {{dataset_path}}/{{sample}}/preprocessing/{ref_name}/."
		)
		return False
	ref_samples = set(ref_files)

	ok = True
	targets = 0
	for modality in modalities:
		name = modality[ModalityParameters.NAME]
		if name == ref_name:
			continue
		targets += 1
		overlap = ref_samples & set(modality_files.get(name, {}))
		if not overlap:
			logger.error(
				f"Target modality '{name}' shares no sample with reference '{ref_name}'. "
				f"This pair has nothing to align."
			)
			ok = False
		else:
			logger.info(
				f"Target '{name}': {len(overlap)} sample(s) will be aligned against "
				f"reference '{ref_name}'."
			)

	if targets == 0:
		logger.error(
			"No non-reference modality declared. Alignment needs at least one target modality."
		)
		return False

	return ok


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Run ONLY the FOCUS alignment stage from already-preprocessed files."
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

	# Validate WITHOUT requiring the raw inputs / registration credential / annotation
	# files. None are needed to align from preprocessed files. All structural, type, and
	# alignment-strategy-compatibility checks still run.
	try:
		config = utils.parse_config(config, require_raw_inputs=False)
	except (TypeError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
		logger.error(f"Config validation failed: {e}")
		sys.exit(1)

	if not config[ConfigParameters.PERFORM_ALIGNMENT]:
		logger.error("'perform_alignment' is false in the config, so there is nothing to do.")
		sys.exit(1)

	# Phase 2: full logging. Adds the focus.log file handler now that dataset_path is known.
	dataset_path = config[ConfigParameters.DATASET_PATH]
	utils.setup_logging(dataset_path, debug=args.debug)
	logger.info(f"Config loaded and validated (alignment-only): {args.config}")

	# Build the preprocessed-file map and confirm there is something to align.
	modality_files, sample_ids = _collect_preprocessed_modality_files(config, logger)
	if not sample_ids:
		logger.error(f"No sample directories found in '{dataset_path}'.")
		sys.exit(1)
	if not _validate_alignable(config, modality_files, logger):
		logger.error("Input validation failed. Aborting alignment.")
		sys.exit(1)

	# No GUI progress sink in standalone mode; _run_alignment also logs via the focus logger.
	def _report(**kwargs):
		return None

	logger.info("=" * 60)
	logger.info("Running alignment stage only")
	logger.info("=" * 60)

	# force_overrides=None: re-alignment is governed solely by each modality's
	# 'alignment_force_recomputing' and by whether cached aligned files already exist.
	# The preprocessing-force cascade is not applied, since this script does not preprocess.
	aligned_files = orchestrator._run_alignment(
		config, modality_files, _report, n_stages=1, force_overrides=None,
	)

	# Summary. For multiple targets the per-sample/merged paths repeat across targets:
	# they are the SAME reference file, which accumulates one obsm['{target}_spatial'] per
	# target it was aligned against.
	logger.info("=" * 60)
	logger.info("Alignment complete. Aligned reference files (per target modality):")
	for mod_name, files in aligned_files.items():
		merged = files.get("merged")
		per_sample = {k: v for k, v in files.items() if k != "merged"}
		logger.info(
			f"  target '{mod_name}': {len(per_sample)} per-sample file(s)"
			+ (f"; merged: {merged}" if merged else "; (no merged file produced)")
		)
		for sid, path in per_sample.items():
			logger.info(f"      {sid}: {path}")
	logger.info("=" * 60)

	sys.exit(0)


if __name__ == "__main__":
	main()
