"""Sample-level transcriptomic cleanup, QC, labels, and metadata (raw counts throughout)."""

from __future__ import annotations
from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr
from focus.preprocessing.transcriptomics.counts import remove_controls
from focus.preprocessing.transcriptomics.provenance import INPUT_SIGNATURE_KEY
from focus.preprocessing.transcriptomics.qc import quality_control
from focus.preprocessing.transcriptomics.technology import (
    AREA_COLUMNS,
    AREA_KEY,
    Technology,
)
from focus.io.anndata import write_h5ad_atomic
from typing import TYPE_CHECKING, cast
from anndata import AnnData
from focus.config.options import StUnit
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic

import numpy as np
import pandas as pd
import scanpy as sc


from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.spot_colors import cluster_colors, label_order


def unit_area(adata: AnnData, tech: Technology) -> np.ndarray | None:
    """
    Area of every observation, the denominator of area normalization.

    Spots and bins share the sample's ``spot_size`` area; cells take the first area column of
    ``AREA_COLUMNS`` found in ``obs``. None for cells without one.
    """
    if tech.unit != StUnit.CELL:
        size = np.asarray(adata.uns["spot_size"], dtype=np.float64)
        return np.full(adata.n_obs, float(size[0] * size[1]))
    for column in AREA_COLUMNS:
        if column in adata.obs:
            return cast(np.ndarray, adata.obs[column].to_numpy(dtype=np.float64))
    return None


def prepare_sample(
    sample: SpatialTranscriptomic,
    tech: Technology,
    settings: StSampleSettings,
    reporter: StepReporter,
) -> AnnData:
    """Load counts, remove controls and low-quality spots, add QC, and name observations."""
    adata = sample.load_data(reporter)
    adata = remove_controls(adata, reporter)

    # Case-insensitive 'MT-'/'MT.' prefix; name-based, so Ensembl-keyed panels are not flagged.
    adata.var["mt"] = adata.var_names.str.upper().str.match(r"^MT[-\.]")

    adata, removed = quality_control(adata, tech, settings["quality_control"], reporter)
    adata.uns["focus_qc"] = {
        "rule": tech.outlier_rule if settings["quality_control"] else "none",
        "removed": removed,
    }

    # QC metrics with mitochondrial genes still present, so pct_counts_mt is meaningful.
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, inplace=True)

    area = unit_area(adata, tech)
    if area is not None:
        adata.obs[AREA_KEY] = area.astype(np.float32)

    if settings["remove_mitochondrial_genes"]:
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
    adata.obs["sample_id"] = pd.Categorical([sample.sample_id] * adata.n_obs)
    categories = label_order(cluster_labels)
    adata.obs["cluster"] = pd.Categorical(cluster_labels, categories=categories)
    colors = cluster_colors(categories)
    adata.uns["cluster_colors"] = [colors[label] for label in categories]
    adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)


def write_sample(
    sample: SpatialTranscriptomic,
    tech: Technology,
    settings: StSampleSettings,
    reporter: StepReporter,
    cache_key: str,
    signature: str,
    output_file: str,
) -> None:
    """Prepare counts and labels, then write CSR raw counts (gzip, atomic).

    Parameters
    ----------
    sample : SpatialTranscriptomic
        Source sample. The source AnnData is not overwritten.
    tech : Technology
        Calibration of the sample's capture and unit.
    settings : StSampleSettings
        Technology, quality control and mitochondrial removal settings.
    reporter : StepReporter
        Receives retention and clustering diagnostics.
    cache_key : str
        Stored preprocessing fingerprint.
    signature : str
        Input stat signature and SHA-256 (JSON), stored so later runs skip re-hashing.
    output_file : str
        Processed H5AD destination; errors propagate without wrapping.
    """
    adata = prepare_sample(sample, tech, settings, reporter)
    cluster_labels = label_sample(adata, tech.resolution, reporter, cache_key)
    set_sample_metadata(adata, sample, cluster_labels)
    adata.uns[INPUT_SIGNATURE_KEY] = signature
    _ensure_sparse_csr(adata)
    write_h5ad_atomic(adata, output_file, compression=sample._H5AD_COMPRESSION)
