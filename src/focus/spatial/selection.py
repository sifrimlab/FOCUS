"""Feature selection of the alignment clustering: spatial coherence, or dispersion (fallback)."""

import anndata as ad
import numpy as np
import scipy.sparse as sp

from focus.spatial.features import SpatialFeatures, rank_spatial_features
from focus.spatial.settings import MIN_SPATIAL_FEATURES, SPATIAL
from focus.spatial.types import ClusterInfo, ClusteringParameters
from focus.spatial.units import SpatialUnits


def spatial_ranking(
    X: sp.csr_matrix,
    units: SpatialUnits,
    unit_ok: np.ndarray,
    params: ClusteringParameters,
    info: ClusterInfo,
) -> SpatialFeatures | None:
    """Spatial coherence ranking of the represented units (``feature_selection`` = spatial)."""
    if params.get("feature_selection") != SPATIAL:
        info["feature_selection"] = "dispersion"
        return None
    ranking = rank_spatial_features(X, units, unit_ok, params["n_image_features"])
    n = params["n_spatial_features"]
    if ranking.columns.size >= MIN_SPATIAL_FEATURES:
        info["feature_selection"] = "spatial"
        info["spatial_coherence"] = ranking.coherence[:n]
    else:
        info["feature_selection"] = "dispersion (too few spatially coherent features)"
    return ranking


def selected_features(
    X: sp.csr_matrix,
    feature_names: np.ndarray,
    params: ClusteringParameters,
    info: ClusterInfo,
    ranking: SpatialFeatures | None = None,
) -> ad.AnnData | None:
    """Clustering features of the represented units.

    The ``n_spatial_features`` most spatially coherent ones under the spatial selection;
    otherwise the expressed features, at most ``n_top_features`` highly variable ones. None when
    fewer than three features are expressed.
    """
    import scanpy as sc

    sub = ad.AnnData(X=sp.csr_matrix(X, dtype=np.float32))
    sub.var_names = np.asarray(feature_names, dtype=str)
    if ranking is not None and info.get("feature_selection") == "spatial":
        sub = sub[:, ranking.columns[: params["n_spatial_features"]]].copy()
        info["selected_features"] = sub.var_names.to_numpy(dtype=str)
        info["n_selected_features"] = sub.n_vars
        return sub
    n_top = params["n_top_features"]
    sub = sub[:, np.diff(sp.csc_matrix(sub.X).indptr) > 0].copy()
    if sub.n_vars < 3:
        return None
    if sub.n_vars > n_top:
        # Seurat dispersion selection consumes log-scale data and needs no extra dependency.
        sc.pp.highly_variable_genes(sub, flavor="seurat", n_top_genes=n_top)
        sub = sub[:, sub.var["highly_variable"].to_numpy()].copy()
    info["selected_features"] = sub.var_names.to_numpy(dtype=str)
    info["n_selected_features"] = sub.n_vars
    return sub


def clustering_features(
    X: sp.csr_matrix,
    feature_names: np.ndarray,
    units: SpatialUnits,
    unit_ok: np.ndarray,
    params: ClusteringParameters,
    info: ClusterInfo,
) -> tuple[ad.AnnData | None, SpatialFeatures | None]:
    """The clustering features of the represented units, and the spatial ranking behind them."""
    ranking = spatial_ranking(X, units, unit_ok, params, info)
    return selected_features(X, feature_names, params, info, ranking), ranking
