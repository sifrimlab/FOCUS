"""Merge per-sample outputs and stamp their unchanged provenance."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np
import os
import h5py
from anndata.io import write_elem

import focus.io.anndata as anndata_io
from focus.io.atomic import atomic_path
from focus.io.paths import merged_preprocessed_file
from focus.preprocessing.msi.settings import _step


def merge_samples(
    dataset: MsiDataset,
    processed_samples: dict[str, str],
    spot_sizes: dict[str, list[float]],
    dataset_key: str,
    stored_references: dict[str, dict[str, np.ndarray]],
    stored_pools: dict[str, dict[str, dict[str, np.ndarray | float]]],
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

        with atomic_path(merged_file) as tmp:
            anndata_io.concat_on_disk_compat(
                sample_files,
                tmp,
                axis=0,
                join="inner",
                merge="same",
                uns_merge="first",
            )
            # The per-sample values are written into the concatenated file in place: per sample
            # dicts for spot_size, the recalibration references and pools, and the fingerprint.
            with h5py.File(tmp, "r+") as handle:
                if "uns" not in handle:
                    write_elem(handle, "uns", {})
                uns = handle["uns"]
                for key, value in (
                    ("spot_size", spot_sizes),
                    (
                        "recalibration_reference",
                        {s: stored_references[s] for s in sample_files},
                    ),
                    (
                        "recalibration_pool",
                        {s: stored_pools.get(s, {}) for s in sample_files},
                    ),
                    ("focus_preprocessing_fingerprint", dataset_key),
                ):
                    if key in uns:
                        del uns[key]
                    write_elem(uns, key, value)

        processed_samples["merged"] = merged_file
