"""Partition of the clustering units: overclustering and merging, or ordered gradient levels."""

from typing import cast

import anndata as ad
import numpy as np

from focus.spatial.clusters import merge_unsupported_clusters
from focus.spatial.settings import GRADIENT
from focus.spatial.types import ClusterInfo, ClusteringParameters


def _overcluster(
    sub: ad.AnnData, resolution: float, min_units: int, seed: int
) -> float:
    """Leiden partition of ``sub`` into ``obs['leiden']``; returns the resolution used.

    The resolution starts at ``resolution`` and halves, down to a quarter of it, while the median
    cluster holds fewer than ``min_units`` units: below a few hundred units per cluster, the valley
    ratio of the merge shows dips on unimodal data, which would keep fragments apart.
    """
    import scanpy as sc

    floor = resolution / 4
    while True:
        sc.tl.leiden(
            sub,
            resolution=resolution,
            key_added="leiden",
            flavor="igraph",
            n_iterations=2,
            directed=False,
            random_state=seed,
        )
        sizes = sub.obs["leiden"].value_counts().to_numpy()
        if np.median(sizes) >= min_units or resolution <= floor:
            return resolution
        resolution /= 2


def _gradient_levels(pcs: np.ndarray, loadings: np.ndarray, bins: int) -> np.ndarray:
    """Ordered quantile levels ``0`` (low) to ``bins - 1`` of the first principal component.

    The sign of the component is fixed so its largest absolute loading is positive, which makes the
    level order deterministic. Tied quantiles collapse, so a component with few distinct values
    gets fewer levels.
    """
    first = loadings[:, 0]
    score = pcs[:, 0] * (1.0 if first[np.argmax(np.abs(first))] >= 0 else -1.0)
    edges = np.unique(np.quantile(score, np.linspace(0, 1, bins + 1)[1:-1]))
    return np.searchsorted(edges, score, side="right").astype(str)


def partition(
    sub: ad.AnnData,
    mode: str,
    params: ClusteringParameters,
    depth: np.ndarray,
    info: ClusterInfo,
) -> np.ndarray | None:
    """Unit labels of the mode from a sparse PCA of ``sub``; None with fewer than two PCs.

    ``depth`` (log summed signal of each unit) gives the descriptive correlation of every
    component with the depth.
    """
    import scanpy as sc

    n_pcs = min(params["n_pcs"], sub.n_obs - 1, sub.n_vars - 1)
    if n_pcs < 2:
        return None
    seed = params["random_state"]
    try:
        # Sparse centered PCA; never materialize an n_units x n_features dense matrix.
        sc.pp.pca(sub, n_comps=n_pcs, random_state=seed, svd_solver="arpack")
        if mode == GRADIENT:
            labels = _gradient_levels(
                sub.obsm["X_pca"], sub.varm["PCs"], params["gradient_bins"]
            )
        else:
            labels = _domains(sub, params, info)
    except MemoryError as error:
        raise MemoryError(
            "Clustering exceeded available memory; use the sub_cellular resolution, which "
            "aggregates spots into fewer units."
        ) from error
    _pca_diagnostics(sub, depth, info)
    return labels


def _domains(
    sub: ad.AnnData, params: ClusteringParameters, info: ClusterInfo
) -> np.ndarray:
    """Overclustered Leiden partition of the PCs, merged where no density valley separates."""
    import scanpy as sc

    seed = params["random_state"]
    # Scanpy uses approximate neighbor construction on large samples.
    sc.pp.neighbors(
        sub,
        n_neighbors=min(params["n_neighbors"], sub.n_obs - 1),
        use_rep="X_pca",
        random_state=seed,
    )
    info["leiden_resolution"] = _overcluster(
        sub, params["leiden_resolution"], params["min_cluster_units"], seed
    )
    labels, merge_info = merge_unsupported_clusters(
        sub.obsm["X_pca"],
        sub.obs["leiden"].to_numpy(),
        sub.obsp["connectivities"],
        params["merge_valley_ratio"],
    )
    info.update(cast(ClusterInfo, merge_info))
    return labels


def _pca_diagnostics(sub: ad.AnnData, depth: np.ndarray, info: ClusterInfo) -> None:
    """Record the components, their variance ratios and their correlation with the depth."""
    info["n_pcs"] = int(sub.obsm["X_pca"].shape[1])
    info["pc_variance_ratio"] = sub.uns["pca"]["variance_ratio"]
    info["pc_log_counts_spearman"] = np.asarray(
        [
            _spearman(pc, depth) if np.ptp(pc) > 0 and np.ptp(depth) > 0 else 0.0
            for pc in sub.obsm["X_pca"].T
        ]
    )


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman rank correlation of two vectors."""
    from scipy.stats import spearmanr

    return float(spearmanr(a, b).statistic)
