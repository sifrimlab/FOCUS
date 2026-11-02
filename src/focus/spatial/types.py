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
    """Resolution-specific smoothing and count targets."""

    smoothing_rings: int
    signal_target: int | None


class ClusteringParameters(TypedDict, total=False):
    """Existing numerical settings for analytical ST clustering copies."""

    min_counts: int
    min_genes: int
    n_top_genes: int
    n_pcs: int
    n_neighbors: int
    leiden_resolution: float
    merge_valley_ratio: float
    random_state: int
    max_units: int
    smoothing_rings: int
    signal_target: int | None


class MergeInfo(TypedDict, total=False):
    """Diagnostics of density-based merging of adjacent clusters."""

    n_leiden_clusters: int
    n_merges: int
    merge_ratios: NDArray[np.float64]


class ClusterInfo(MergeInfo, total=False):
    """Existing analytical clustering diagnostics stored with each sample."""

    method: str
    parameters: ClusteringParameters
    software_versions: dict[str, str]
    n_input: int
    n_eligible: int
    n_selected_genes: int
    selected_genes: NDArray[np.str_]
    n_clusters: int
    status: str
    resolution: str
    units: UnitInfo
    n_spots: int
    n_eligible_spots: int
    cluster_sizes: dict[str, int]
    n_pcs: int
    smoothing_rings: int
    pc_variance_ratio: NDArray[np.float32 | np.float64]
    pc_log_counts_spearman: NDArray[np.float64]
    label_coherence: float
