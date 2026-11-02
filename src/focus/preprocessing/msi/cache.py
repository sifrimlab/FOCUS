"""Reuse a fully cached dataset with the same progress events."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset


import focus.io.anndata as anndata_io
from focus.io.paths import preprocessed_file, merged_preprocessed_file
from focus.io.provenance import cached_fingerprint
from focus.preprocessing.msi.settings import _DATASET_STEPS


def cached_files(
    dataset: MsiDataset,
    force_recomputing: bool,
    dataset_key: str,
    processed_samples: dict[str, str],
    reporter: StepReporter,
) -> dict[str, str] | None:
    """Reuse a fully cached dataset with the same progress events."""

    # STEP 0: Check if the computation is complete and can be skipped
    if not force_recomputing:
        all_sample_computed = True

        # Check if all the samples have already been processed with the same inputs and settings
        for sample in dataset.samples:
            sample_path = preprocessed_file(
                dataset.dataset_source_path,
                sample.sample_id,
                sample.modality_name,
                "h5ad",
            )
            if cached_fingerprint(sample_path) != dataset_key:
                all_sample_computed = False
                break
            processed_samples[sample.sample_id] = sample_path

        # Check if the merged dataset already exists and has the correct sample composition
        merged_path = merged_preprocessed_file(
            dataset.dataset_source_path, dataset.samples[0].modality_name, "h5ad"
        )
        if cached_fingerprint(merged_path) != dataset_key:
            all_sample_computed = False
        else:
            active_ids = {s.sample_id for s in dataset.samples}
            merged_ids = anndata_io.read_merged_sample_ids(merged_path)
            if merged_ids != active_ids:
                all_sample_computed = False
            else:
                processed_samples["merged"] = merged_path

        # If the merged dataset already exists and force_recompute is False, skip the computation
        if all_sample_computed:
            for index, name in enumerate(_DATASET_STEPS, 1):
                reporter.step(index, len(_DATASET_STEPS), name, cached=True)
            return processed_samples
    return None
