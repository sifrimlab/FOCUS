"""Merge per-sample outputs and stamp their unchanged provenance."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np
import pandas as pd
import os
import h5py
from anndata.io import read_elem, write_elem

import focus.io.anndata as anndata_io
from focus.io.atomic import atomic_path
from focus.io.paths import merged_preprocessed_file
from focus.preprocessing.msi.settings import _step
from focus.spot_colors import cluster_colors, label_order


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
            _stamp_merged(
                tmp,
                {
                    "spot_size": spot_sizes,
                    "recalibration_reference": {
                        s: stored_references[s] for s in sample_files
                    },
                    "recalibration_pool": {
                        s: stored_pools.get(s, {}) for s in sample_files
                    },
                    "focus_clustering": {
                        s: _clustering_provenance(f) for s, f in sample_files.items()
                    },
                    "focus_preprocessing_fingerprint": dataset_key,
                },
            )

        processed_samples["merged"] = merged_file


def _clustering_provenance(path: str) -> dict[str, object]:
    """``uns['focus_clustering']`` of a per-sample file, without loading its matrices."""
    with h5py.File(path, "r") as handle:
        if "uns/focus_clustering" not in handle:
            return {}
        return dict(read_elem(handle["uns/focus_clustering"]))


def _stamp_merged(path: str, values: dict[str, object]) -> None:
    """Write the per-sample ``uns`` values and the merged cluster colours into a merged file.

    The per-sample values (spot sizes, recalibration references and pools, clustering provenance)
    and the fingerprint are written in place. The cluster categories are reordered like the
    per-sample files (numeric, then ``unassigned``) and ``uns['cluster_colors']`` follows them.
    """
    with h5py.File(path, "r+") as handle:
        if "uns" not in handle:
            write_elem(handle, "uns", {})
        uns = handle["uns"]
        categories: list[str] = []
        if "obs/cluster" in handle:
            cluster = np.asarray(read_elem(handle["obs/cluster"]), dtype=str)
            categories = label_order(cluster)
            del handle["obs/cluster"]
            write_elem(
                handle["obs"], "cluster", pd.Categorical(cluster, categories=categories)
            )
        colors = cluster_colors(categories)
        values = {**values, "cluster_colors": [colors[label] for label in categories]}
        for key, value in values.items():
            if key in uns:
                del uns[key]
            write_elem(uns, key, value)
