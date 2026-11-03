"""Typed geometry and clustering provenance dictionaries, without runtime conversion."""

from numpy.typing import NDArray

from typing import TypeAlias, TypedDict

import numpy as np
import scipy.sparse as sp

Matrix: TypeAlias = (
    NDArray[
        np.bool_
        | np.int8
        | np.int16
        | np.int32
        | np.int64
        | np.uint8
        | np.uint16
        | np.uint32
        | np.uint64
        | np.float32
        | np.float64
    ]
    | sp.csr_matrix
)


class UnitInfo(TypedDict, total=False):
    """Geometry and occupancy of the temporary clustering units."""

    method: str
    layout: str
    n_units: int
    lloyd_iterations: int
    seed: int
    driver: str
    signal_limited: bool
    step: list[float]
    k: int
    unit_size: list[float]


class ClusteringPreset(TypedDict):
    """Clustering mode and the signal sub_cellular aggregation grows units for."""

    mode: str
    signal: str | None
    signal_target: float | None


class ClusteringParameters(TypedDict, total=False):
    """Numerical settings of the alignment clustering (ST adds its eligibility thresholds)."""

    min_counts: int
    min_genes: int
    n_top_features: int
    n_pcs: int
    n_neighbors: int
    leiden_resolution: float
    min_cluster_units: int
    merge_valley_ratio: float
    gradient_bins: int
    random_state: int
    max_units: int
    mode: str
    signal: str | None
    signal_target: float | None


class MergeInfo(TypedDict, total=False):
    """Diagnostics of density-based merging of adjacent clusters."""

    n_leiden_clusters: int
    n_merges: int
    merge_ratios: NDArray[np.float64]


class ClusterInfo(MergeInfo, total=False):
    """Alignment clustering diagnostics stored with each sample."""

    method: str
    mode: str
    representation: str
    parameters: ClusteringParameters
    software_versions: dict[str, str]
    n_input: int
    n_eligible: int
    n_selected_features: int
    selected_features: NDArray[np.str_]
    n_clusters: int
    status: str
    resolution: str
    sample_type: str
    units: UnitInfo
    n_spots: int
    n_eligible_spots: int
    cluster_sizes: dict[str, int]
    n_pcs: int
    leiden_resolution: float
    pc_variance_ratio: NDArray[np.float32 | np.float64]
    pc_log_counts_spearman: NDArray[np.float64]
    label_coherence: float
