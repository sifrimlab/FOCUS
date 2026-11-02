"""Cross-sample transcriptomic readout, filtering, and metadata operations."""

from __future__ import annotations
from focus.preprocessing.transcriptomics.gene_filter import filter_genes
from typing import TYPE_CHECKING
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
from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr


def collect_sample_metadata(
    dataset: SpatialTranscriptomicDataset, processed_samples: dict[str, str]
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    """Read spot sizes and clustering provenance without loading count matrices."""

    # ---- Collect per-sample spot sizes via backed reads (no .X materialized) ----
    spot_sizes: dict[str, np.ndarray] = {}
    clustering_provenance: dict[str, object] = {}
    for sample in dataset.samples:
        a = sc.read_h5ad(processed_samples[sample.sample_id], backed="r")
        spot_sizes[sample.sample_id] = np.asarray(
            a.uns.get("spot_size", SpatialTranscriptomic._DEFAULT_SPOT_SIZE.copy()),
            dtype=np.float32,
        )
        clustering_provenance[sample.sample_id] = dict(
            a.uns.get("focus_clustering", {})
        )
        if a.file is not None:
            a.file.close()
    return (spot_sizes, clustering_provenance)


def read_combined(
    dataset: SpatialTranscriptomicDataset,
    processed_samples: dict[str, str],
    merged_file: str,
    reporter: StepReporter,
) -> AnnData:
    """Concatenate sample files and restore raw counts for cross-sample processing."""

    reporter.detail(f"Concatenating {len(dataset.samples)} samples")
    # Memory-efficient on-disk concatenation: streams each per-sample file instead of
    # holding all of them plus the result in RAM simultaneously. Outer join preserves
    # genes when panels differ across samples; absent genes are filled with 0 counts.
    # uns is intentionally dropped (default uns_merge=None) so a per-sample 'log1p' flag
    # can't suppress the merged log1p below; spot_size is rebuilt from the backed reads.
    # The per-sample 'raw' layer (present only when those files were normalized) rides
    # along the concat and is recovered below.
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

    # Recover raw counts for cross-sample filtering / normalization. When the per-sample
    # files were normalized, raw lives in layers['raw']; otherwise .X already holds raw.
    # NOTE: a stale per-sample cache from a run with different normalization flags could
    # make this inconsistent — pair any flag change with force_recomputing=True.
    combined.X = combined.layers.pop("raw", combined.X)
    combined.uns.pop("log1p", None)

    # Ensure .X is sparse CSR for efficient row-based filter operations (preserve dtype).
    _ensure_sparse_csr(combined)
    gc.collect()
    return combined


def filter_combined(
    dataset: SpatialTranscriptomicDataset,
    combined: AnnData,
    min_spots_per_gene: float | None,
    min_count_spots_ratio_per_gene: float | None,
    reporter: StepReporter,
) -> AnnData:
    """Apply cross-sample gene filters and recompute raw-count QC metrics."""

    # ---- Cross-sample gene filtering (single pass over per-sample submatrices) ----
    reporter.detail(f"{combined.n_vars} genes before cross-sample filtering")
    if min_spots_per_gene is not None or min_count_spots_ratio_per_gene is not None:
        if min_spots_per_gene is not None:
            reporter.detail(
                f"Filtering genes by expression frequency (min_spots_per_gene={min_spots_per_gene})"
            )
        if min_count_spots_ratio_per_gene is not None:
            reporter.detail(
                f"Filtering genes by count/spot ratio (min_count_spots_ratio_per_gene={min_count_spots_ratio_per_gene})"
            )
        combined = filter_genes(
            combined, min_spots_per_gene, min_count_spots_ratio_per_gene
        )
        reporter.detail(f"{combined.n_vars} genes after cross-sample filtering")

    # Recompute QC metrics on the final merged matrix (raw counts) so .obs/.var QC
    # reflect the retained spots and genes; per-sample QC predates cross-sample filtering.
    combined.var["mt"] = combined.var_names.str.upper().str.match(r"^MT[-\.]")
    sc.pp.calculate_qc_metrics(combined, qc_vars=["mt"], percent_top=None, inplace=True)
    return combined


def set_merged_metadata(
    combined: AnnData,
    spot_sizes: dict[str, np.ndarray],
    clustering_provenance: dict[str, object],
    merged_key: str,
) -> None:
    """Store merged cluster colors, sample spot sizes, and provenance."""

    # Per-sample cluster labels are already in .obs["cluster"] from concatenation
    # (each sample was clustered independently to avoid batch effects)

    # ---- Set output metadata ----
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
