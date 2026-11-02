"""Sample-level transcriptomic validation, QC, labels, and metadata."""

from __future__ import annotations
from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.normalization import normalize_counts
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr
from focus.io.anndata import write_h5ad_compat
from typing import TYPE_CHECKING
from anndata import AnnData
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic

import numpy as np
import pandas as pd
import scanpy as sc


from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.spot_colors import cluster_colors, label_order


def validate_spot_filters(
    min_count_per_spot: int | None,
    max_count_per_spot: int | None,
    min_genes_per_spot: int | None,
    max_genes_per_spot: int | None,
) -> None:
    """Validate opt-in spot thresholds without changing their error messages."""

    # Validate filter parameters
    if min_count_per_spot is not None and min_count_per_spot <= 0:
        raise ValueError("min_count_per_spot must be greater than 0.")
    if max_count_per_spot is not None and max_count_per_spot <= 0:
        raise ValueError("max_count_per_spot must be greater than 0.")
    if min_genes_per_spot is not None and min_genes_per_spot <= 0:
        raise ValueError("min_genes_per_spot must be greater than 0.")
    if max_genes_per_spot is not None and max_genes_per_spot <= 0:
        raise ValueError("max_genes_per_spot must be greater than 0.")


def filter_sample(
    sample: SpatialTranscriptomic,
    min_count_per_spot: int | None,
    max_count_per_spot: int | None,
    min_genes_per_spot: int | None,
    max_genes_per_spot: int | None,
    remove_mitochondrial_genes: bool,
    reporter: StepReporter,
) -> AnnData:
    """Load counts, apply spot QC, and assign sample-unique observation names."""

    # 1. Load and validate
    adata = sample.load_data()
    n_spots_total = adata.n_obs

    # 2. Flag mitochondrial genes (case-insensitive 'MT-'/'MT.' prefix).
    # Note: name-based detection; datasets keyed by Ensembl IDs won't be flagged.
    adata.var["mt"] = adata.var_names.str.upper().str.match(r"^MT[-\.]")

    # 3. Filter spots (all thresholds opt-in)
    if min_count_per_spot is not None:
        sc.pp.filter_cells(adata, min_counts=min_count_per_spot)
    if max_count_per_spot is not None:
        sc.pp.filter_cells(adata, max_counts=max_count_per_spot)
    if min_genes_per_spot is not None:
        sc.pp.filter_cells(adata, min_genes=min_genes_per_spot)
    if max_genes_per_spot is not None:
        sc.pp.filter_cells(adata, max_genes=max_genes_per_spot)

    # Report how many spots survived the (opt-in) spot filters, on every interface.
    n_spots_retained = adata.n_obs
    pct_retained = (100.0 * n_spots_retained / n_spots_total) if n_spots_total else 0.0
    reporter.detail(
        f"Retained {n_spots_retained} / {n_spots_total} spots ({pct_retained:.1f}%) after filtering"
    )

    # 4. QC metrics on the retained spots, with mitochondrial genes still present
    # so pct_counts_mt is meaningful. percent_top=None keeps the output lean.
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, inplace=True)

    # 5. Optional QC-based gene filter: drop mitochondrial genes (opt-in).
    if remove_mitochondrial_genes:
        adata = adata[:, ~adata.var["mt"].to_numpy()].copy()

    # Make observation names unique across samples (vectorized; avoids per-spot Python loops)
    prefix = f"{sample.sample_id}_"
    if not adata.obs_names.str.startswith(prefix).all():
        adata.obs_names = prefix + adata.obs_names.astype(str)
    # A named index that shadows an obs column (e.g. Xenium 'cell_label') no longer
    # matches it after prefixing; anndata refuses to write that, so drop the name.
    # The column keeps the original, unprefixed IDs.
    if adata.obs.index.name in adata.obs.columns:
        adata.obs.index.name = None
    return adata


def label_sample(
    adata: AnnData, resolution: str, reporter: StepReporter, cache_key: str
) -> np.ndarray:
    """Compute display labels on an analytical copy and record provenance."""

    # Independent analytical copy: original observations and genes are retained in .X.
    # Large or sparse samples are clustered on lattice blocks / supercells (in memory only).
    cluster_labels, eligible, provenance = spot_cluster_labels(
        adata.X, adata.var_names, adata.obsm["spatial"], resolution, reporter
    )
    adata.obs["clustering_eligible"] = eligible
    adata.uns["focus_clustering"] = provenance
    adata.uns["focus_preprocessing_fingerprint"] = cache_key
    return cluster_labels


def set_sample_metadata(
    adata: AnnData, sample: SpatialTranscriptomic, cluster_labels: np.ndarray
) -> None:
    """Store sample identifiers, cluster colors, and native coordinates."""

    # 9. Set metadata
    adata.obs["sample_id"] = pd.Categorical([sample.sample_id] * adata.n_obs)
    categories = label_order(cluster_labels)
    adata.obs["cluster"] = pd.Categorical(cluster_labels, categories=categories)
    colors = cluster_colors(categories)
    adata.uns["cluster_colors"] = [colors[label] for label in categories]
    adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)


def write_sample(
    sample: SpatialTranscriptomic,
    settings: StSampleSettings,
    reporter: StepReporter,
    cache_key: str,
    output_file: str,
) -> None:
    """Validate filters, prepare counts/labels and write CSR data with gzip compression.

    Parameters
    ----------
    sample : SpatialTranscriptomic
        Source sample, read after threshold validation. Source AnnData is not overwritten.
    settings : StSampleSettings
        Spot filters, normalization flags and clustering resolution.
    reporter : StepReporter
        Receives retention and clustering diagnostics.
    cache_key : str
        Stored preprocessing fingerprint, computed before validating thresholds.
    output_file : str
        Processed H5AD destination; errors propagate without wrapping.
    """
    validate_spot_filters(
        settings["min_count_per_spot"],
        settings["max_count_per_spot"],
        settings["min_genes_per_spot"],
        settings["max_genes_per_spot"],
    )
    adata = filter_sample(
        sample,
        settings["min_count_per_spot"],
        settings["max_count_per_spot"],
        settings["min_genes_per_spot"],
        settings["max_genes_per_spot"],
        settings["remove_mitochondrial_genes"],
        reporter,
    )
    cluster_labels = label_sample(adata, settings["resolution"], reporter, cache_key)
    normalize_counts(
        adata,
        settings["total_counts_normalize"],
        settings["log1p_transform"],
        sample._NORMALIZE_TARGET_SUM,
    )
    set_sample_metadata(adata, sample, cluster_labels)

    # Guarantee sparse CSR storage, then save with gzip compression
    _ensure_sparse_csr(adata)
    write_h5ad_compat(adata, output_file, compression=sample._H5AD_COMPRESSION)
