"""Cross-sample transcriptomic gene selection, merge, QC, and metadata operations."""

from __future__ import annotations
from typing import TYPE_CHECKING, cast
from anndata import AnnData
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.dataset import SpatialTranscriptomicDataset

import gc
import numpy as np
import pandas as pd
import scanpy as sc

from focus.io.anndata import concat_on_disk_compat
from focus.spot_colors import cluster_colors, label_order
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr


def collect_sample_metadata(
    dataset: SpatialTranscriptomicDataset, processed_samples: dict[str, str]
) -> tuple[dict[str, np.ndarray], dict[str, object], dict[str, pd.Series]]:
    """Read spot sizes, clustering provenance and per-gene detection without loading counts.

    The detection counts are each sample's ``var['n_cells_by_counts']``, measured on its
    retained spots.
    """
    spot_sizes: dict[str, np.ndarray] = {}
    clustering_provenance: dict[str, object] = {}
    detection: dict[str, pd.Series] = {}
    for sample in dataset.samples:
        a = sc.read_h5ad(processed_samples[sample.sample_id], backed="r")
        spot_sizes[sample.sample_id] = np.asarray(a.uns["spot_size"], dtype=np.float32)
        clustering_provenance[sample.sample_id] = dict(
            a.uns.get("focus_clustering", {})
        )
        detection[sample.sample_id] = a.var["n_cells_by_counts"].copy()
        if a.file is not None:
            a.file.close()
    return spot_sizes, clustering_provenance, detection


def gene_mask(
    var_names: pd.Index, detection: dict[str, pd.Series], min_observations: int
) -> np.ndarray:
    """
    Genes of ``var_names`` detected in at least ``min_observations`` spots of at least one sample.

    A gene confined to one sample is kept on the strength of that sample alone.
    """
    keep = np.zeros(len(var_names), dtype=bool)
    for counts in detection.values():
        observed = counts.reindex(var_names).fillna(0).to_numpy()
        keep |= observed >= min_observations
    return keep


def read_combined(
    dataset: SpatialTranscriptomicDataset,
    processed_samples: dict[str, str],
    merged_file: str,
    detection: dict[str, pd.Series],
    min_observations: int,
    reporter: StepReporter,
) -> AnnData:
    """Concatenate the per-sample raw counts on the union panel and keep the selected genes."""

    reporter.detail(f"Concatenating {len(dataset.samples)} samples")
    # Streams each per-sample file instead of holding all of them plus the result in RAM. The
    # outer join builds the union panel; a gene absent from a sample is 0 there. uns is dropped
    # (spot_size is rebuilt from the backed reads).
    sample_files = {
        s.sample_id: processed_samples[s.sample_id] for s in dataset.samples
    }
    concat_on_disk_compat(
        sample_files,
        merged_file,
        axis=0,
        join="outer",
        fill_value=0,
        merge="same",
    )
    combined = sc.read_h5ad(merged_file)
    _ensure_sparse_csr(combined)
    n_union = combined.n_vars
    keep = gene_mask(combined.var_names, detection, min_observations)
    combined._inplace_subset_var(keep)
    reporter.detail(
        f"Kept {combined.n_vars} / {n_union} genes "
        f"(detected in at least {min_observations} spots of one sample)"
    )
    gc.collect()
    return combined


def merged_qc(combined: AnnData) -> None:
    """
    Recompute the library-size and gene QC on the final panel.

    ``total_counts``, ``n_genes_by_counts`` (and ``log1p_`` variants) and the var QC describe the
    merged matrix. ``total_counts_mt``, ``pct_counts_mt`` and the control metrics keep the
    per-sample values, measured before mitochondrial and control features were removed.
    """
    combined.var["mt"] = combined.var_names.str.upper().str.match(r"^MT[-\.]")
    sc.pp.calculate_qc_metrics(combined, percent_top=None, inplace=True)


def set_merged_metadata(
    combined: AnnData,
    spot_sizes: dict[str, np.ndarray],
    clustering_provenance: dict[str, object],
    merged_key: str,
) -> None:
    """Store merged cluster colors, sample spot sizes, and provenance."""

    # Per-sample cluster labels are already in .obs["cluster"] from concatenation
    # (each sample was clustered independently to avoid batch effects)
    combined.obs["sample_id"] = pd.Categorical(combined.obs["sample_id"])
    categories = label_order(combined.obs["cluster"].astype(str).to_numpy())
    combined.obs["cluster"] = pd.Categorical(
        combined.obs["cluster"], categories=categories
    )
    colors = cluster_colors(categories)
    combined.uns["cluster_colors"] = [colors[label] for label in categories]
    combined.obsm["spatial"] = np.asarray(combined.obsm["spatial"], dtype=np.float32)
    combined.uns["spot_size"] = {sid: ss.tolist() for sid, ss in spot_sizes.items()}
    combined.uns["focus_clustering"] = clustering_provenance
    combined.uns["focus_preprocessing_fingerprint"] = merged_key
    cast(dict[str, object], combined.uns).pop("focus_input_signature", None)
