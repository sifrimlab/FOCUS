"""Merge per-sample outputs and stamp their unchanged provenance."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np
import os
import anndata as ad
import gc

import focus.io.anndata as anndata_io
from focus.io.paths import merged_preprocessed_file
from focus.preprocessing.msi.settings import _step


def merge_samples(
    dataset: MsiDataset,
    processed_samples: dict[str, str],
    spot_sizes: dict[str, list[float]],
    dataset_key: str,
    stored_references: dict[str, dict[str, np.ndarray]],
    reporter: StepReporter,
) -> None:
    """Merge per-sample outputs and stamp their unchanged provenance."""

    # STEP 9: Merge all samples into a single dataset (memory-efficient on-disk concat)
    reporter.step(*_step(8))
    merged_file = merged_preprocessed_file(
        dataset.dataset_source_path, dataset.samples[0].modality_name, "h5ad"
    )

    if processed_samples:
        os.makedirs(os.path.dirname(merged_file), exist_ok=True)
        # Filter to only per-sample files (exclude any "merged" key)
        sample_files = {k: v for k, v in processed_samples.items() if k != "merged"}

        anndata_io.concat_on_disk_compat(
            sample_files,
            merged_file,
            axis=0,
            join="inner",
            merge="same",
            uns_merge="first",
        )

        # Update the merged file's .uns["spot_size"] to be a per-sample dict
        merged_adata = ad.read_h5ad(merged_file)
        merged_adata.uns["spot_size"] = spot_sizes
        merged_adata.uns["recalibration_reference"] = {
            s: stored_references[s] for s in sample_files
        }
        merged_adata.uns["focus_preprocessing_fingerprint"] = dataset_key
        anndata_io.write_h5ad_compat(
            merged_adata, merged_file, compression=dataset._H5AD_COMPRESSION
        )
        del merged_adata
        gc.collect()

        processed_samples["merged"] = merged_file
