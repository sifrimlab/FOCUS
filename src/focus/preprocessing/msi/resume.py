"""Discover compatible partial outputs in original sample order."""

from __future__ import annotations
from focus.preprocessing.msi.run_state import CachedPrefix
from typing import TYPE_CHECKING, cast
from focus.runtime.reporting import StepReporter
from focus.preprocessing.msi.run_state import ResumeState
from focus.preprocessing.msi.cache_readout import read_cached_sample

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np

import pandas as pd

from focus.config.options import MsiIonMode
from focus.io.paths import preprocessed_file
from focus.io.provenance import cached_fingerprint


def resume_samples(
    dataset: MsiDataset,
    force_recomputing: bool,
    dataset_key: str,
    reporter: StepReporter,
) -> ResumeState:
    """Discover compatible partial outputs in original sample order."""

    # STEP 0b: Partial-resume cache discovery (an interrupted run with unchanged inputs and settings)
    # Walk self.samples in order looking for per-sample h5ad files carrying the current dataset
    # fingerprint. Take the first valid one as ground truth: its .var (mz/mz_sigma_ppm/mz_mode/lipid_annotation)
    # IS the global reference. Samples whose file is missing, stale OR whose var disagrees with the
    # ground truth (and all subsequent samples) are queued for reprocessing. We then skip steps 4-6
    # entirely (we have the cached global reference) and run steps 1-3, 7 only on the queued subset
    # (step 7 recalibrates them and detects their foreground);
    # the cached samples' recalibration references (.uns['recalibration_reference'], already on the
    # common mass scale) anchor the mass scales of the queued ones. Step 8 always runs to rebuild the
    # merged dataset.
    resume = ResumeState(list(dataset.samples), {}, {}, {}, False)
    if not force_recomputing:
        prefix = _discover_prefix(dataset, dataset_key, resume)
        resume.partial = prefix.var is not None
        if resume.partial:
            ground_truth_var = cast(pd.DataFrame, prefix.var)
            _queue_remaining(dataset, prefix, resume)
            _restore_reference(dataset, ground_truth_var)
            reporter.detail(
                f"Partial resume: {len(resume.files)} cached sample file(s) reused, "
                f"{len(resume.samples)} sample(s) to process"
            )
    return resume


def _discover_prefix(
    dataset: MsiDataset, dataset_key: str, resume: ResumeState
) -> CachedPrefix:
    """Read a compatible ordered prefix, stopping at the first missing or inconsistent sample."""
    prefix = CachedPrefix()
    for i, sample in enumerate(dataset.samples):
        sample_path = preprocessed_file(
            dataset.dataset_source_path,
            sample.sample_id,
            sample.modality_name,
            "h5ad",
        )
        result = (
            read_cached_sample(sample_path)
            if cached_fingerprint(sample_path) == dataset_key
            else None
        )
        if result is None:
            prefix.invalid_index = i
            break

        var_subset, spot_size, cached_reference, cached_pool = result
        this_mz = var_subset["mz"].to_numpy(dtype=np.float32)
        this_mode = var_subset["mz_mode"].astype(str).to_numpy()
        this_ann = var_subset["lipid_annotation"].astype(str).to_numpy()

        if prefix.var is None:
            prefix.var = var_subset
            prefix.mz, prefix.mode, prefix.annotation = this_mz, this_mode, this_ann
        else:
            if (
                not np.array_equal(this_mz, cast(np.ndarray, prefix.mz))
                or not np.array_equal(this_mode, cast(np.ndarray, prefix.mode))
                or not np.array_equal(this_ann, cast(np.ndarray, prefix.annotation))
            ):
                prefix.invalid_index = i
                break

        resume.files[sample.sample_id] = sample_path
        resume.spot_sizes[sample.sample_id] = spot_size
        resume.stored_references[sample.sample_id] = cached_reference
        resume.stored_pools[sample.sample_id] = cached_pool

    return prefix


def _queue_remaining(
    dataset: MsiDataset, prefix: CachedPrefix, resume: ResumeState
) -> None:
    """Keep the invalid suffix in original order and discard any suffix cache bookkeeping."""
    if prefix.invalid_index is None:
        resume.samples = []
    else:
        resume.samples = list(dataset.samples[prefix.invalid_index :])
        for s in resume.samples:
            resume.files.pop(s.sample_id, None)
            resume.spot_sizes.pop(s.sample_id, None)
            resume.stored_references.pop(s.sample_id, None)
            resume.stored_pools.pop(s.sample_id, None)


def _restore_reference(dataset: MsiDataset, ground_truth_var: pd.DataFrame) -> None:
    """Restore each polarity's float32 masses and object annotations from the first valid cache."""
    # Reconstruct the global reference m/z and lipid annotations from the cached var
    mode_str_array = ground_truth_var["mz_mode"].astype(str).to_numpy()
    dataset.reference_mz = {}
    dataset.reference_sigma = {}
    dataset.lipid_annotations = {}
    for mode_enum in [MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE]:
        mask = mode_str_array == str(mode_enum)
        if mask.any():
            dataset.reference_mz[mode_enum] = ground_truth_var.loc[mask, "mz"].to_numpy(
                dtype=np.float32
            )
            dataset.reference_sigma[mode_enum] = ground_truth_var.loc[
                mask, "mz_sigma_ppm"
            ].to_numpy(dtype=np.float32)
            dataset.lipid_annotations[mode_enum] = (
                ground_truth_var.loc[mask, "lipid_annotation"].astype(str).to_numpy()
            )
        else:
            dataset.reference_mz[mode_enum] = np.array([], dtype=np.float32)
            dataset.reference_sigma[mode_enum] = np.array([], dtype=np.float32)
            dataset.lipid_annotations[mode_enum] = np.array([], dtype=object)
