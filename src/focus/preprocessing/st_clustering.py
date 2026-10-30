"""ST clustering for alignment colors. Analytical copies never alter exported counts."""
from importlib.metadata import version

import anndata as ad
import numpy as np
import scanpy as sc
import scipy.sparse as sp
from scipy.stats import spearmanr

from focus.constants import SpotResolution
from focus.preprocessing._utils import (
    N_TOP_FEATURES, _SPATIAL_CAP, _bin_sum_matrix, clustering_preset, describe_units, label_coherence,
    plan_spatial_units, smoothing_operator, spatial_embedding,
)

METHOD_VERSION = "st-units-3"
# Shared by both resolution presets (CLUSTERING_PRESETS holds what differs). min_counts and
# min_genes are per-unit display eligibility, not tissue QC.
PARAMETERS = {
    "min_counts": 50,
    "min_genes": 20,
    "n_top_genes": N_TOP_FEATURES,
    "n_pcs": 50,
    "n_neighbors": 15,
    "leiden_resolution": 0.5,
    "random_state": 0,
    "max_units": _SPATIAL_CAP,
}


def _check_raw_counts(X, chunk=50_000_000):
    # Chunked so a matrix with hundreds of millions of entries never needs full-size temporaries.
    for start in range(0, X.nnz, chunk):
        data = X.data[start:start + chunk]
        if not np.all(np.isfinite(data)) or np.any(data < 0) or np.any(data != np.floor(data)):
            raise ValueError("ST clustering requires finite, nonnegative raw integer counts in .X")


def spot_cluster_labels(X, gene_names, coords, resolution=SpotResolution.STANDARD, reporter=None, params=None):
    """Per-spot labels, eligibility and provenance, with bounded cost at any spot count.

    ``resolution`` selects the preset (:data:`focus.preprocessing._utils.CLUSTERING_PRESETS`).
    :func:`focus.preprocessing._utils.plan_spatial_units` reads the layout from ``coords`` and
    keeps the spots as they are, or groups them into k x k lattice blocks (Voronoi supercells
    without a lattice) when the sample exceeds the unit cap or, for the sub-cellular preset, its
    median spot holds fewer than the preset's ``signal_target`` counts. Unit counts are summed,
    :func:`native_cluster_labels` clusters the units with the preset's smoothing, and every spot
    takes its unit's label and eligibility. Units are never persisted. ``params`` overrides
    :data:`PARAMETERS` (tests only).
    """
    preset = clustering_preset(resolution)
    params = {**PARAMETERS, **(params or {})}
    X = sp.csr_matrix(X)
    _check_raw_counts(X)
    target = preset["signal_target"]
    counts = np.asarray(X.sum(axis=1)).ravel() if target else None
    units = plan_spatial_units(coords, cap=params["max_units"], counts=counts,
                               min_median_counts=target, seed=params["random_state"])
    if reporter:
        reporter.detail(describe_units(X.shape[0], units.info))
        if units.info.get("signal_limited"):
            reporter.warning(f"Clustering: median unit stays below {target} counts at the coarsest "
                             f"allowed size; labels may be noisy")
    rows = X if units.bin_ids is None else _bin_sum_matrix(X, units.bin_ids, units.n_units)
    unit_labels, unit_eligible, info = native_cluster_labels(
        rows, gene_names, params, reporter, units=units, rings=preset["smoothing_rings"], checked=True)
    if units.bin_ids is None:
        labels, eligible = unit_labels, unit_eligible
    else:
        labels, eligible = unit_labels[units.bin_ids], unit_eligible[units.bin_ids]
    info["resolution"] = resolution
    info["parameters"] = {**params, **preset}
    info["units"] = units.info
    info["n_spots"] = X.shape[0]
    info["n_eligible_spots"] = int(eligible.sum())
    if info["status"] == "complete":
        unique, sizes = np.unique(labels[eligible], return_counts=True)
        info["cluster_sizes"] = dict(zip(unique.tolist(), sizes.tolist()))
    return labels, eligible, info


def native_cluster_labels(X, gene_names, params=None, reporter=None, units=None, rings=0, checked=False):
    """Return labels, eligibility and provenance for exactly the supplied rows (spots or units).

    Eligibility defaults (50 UMIs / 20 detected genes) are alignment-only display thresholds,
    not universal tissue QC. Without ``units`` no spatial information enters the model. With
    ``units`` (the geometry of the rows, see :func:`spot_cluster_labels`) and ``rings > 0``, each
    eligible row's PCs are joined by the mean PCs of its spatial neighbourhood
    (:func:`focus.preprocessing._utils.spatial_embedding`) before the kNN graph, so labels follow
    tissue domains instead of per-row noise. Expression is never smoothed.
    """
    params = {**PARAMETERS, **(params or {})}
    X = sp.csr_matrix(X)
    if not checked:
        _check_raw_counts(X)
    counts = np.asarray(X.sum(axis=1)).ravel()
    detected = np.asarray((X > 0).sum(axis=1)).ravel()
    eligible = (counts > 0) & (counts >= params["min_counts"]) & (detected >= params["min_genes"])
    labels = np.full(X.shape[0], "unassigned", dtype=object)
    info = {
        "method": METHOD_VERSION, "parameters": params,
        "software_versions": {name: version(name) for name in ("scanpy", "numpy", "scipy", "scikit-learn", "igraph")},
        "n_input": X.shape[0],
        "n_eligible": int(eligible.sum()), "n_selected_genes": 0,
        "selected_genes": np.array([], dtype=str), "n_clusters": 0,
        "status": "insufficient_observations",
    }
    if reporter:
        reporter.detail(f"Clustering: {eligible.sum()}/{len(eligible)} eligible units")
    if eligible.sum() < 3:
        return labels, eligible, info
    # Explicit count-based eligibility can admit a one-gene spot, but it cannot create a
    # meaningful embedding when the entire retained panel has fewer than three genes.
    sub = ad.AnnData(X=X[eligible].astype(np.float32))
    sub.var_names = np.asarray(gene_names, dtype=str)
    expressed = np.asarray((sub.X > 0).sum(axis=0)).ravel() > 0
    sub = sub[:, expressed].copy()
    if sub.n_vars < 3:
        info["status"] = "insufficient_genes"
        return labels, eligible, info
    sc.pp.normalize_total(sub, target_sum=1e4)
    sc.pp.log1p(sub)
    n_top = min(params["n_top_genes"], sub.n_vars)
    # Seurat dispersion selection consumes log-normalized data and needs no extra dependency.
    sc.pp.highly_variable_genes(sub, flavor="seurat", n_top_genes=n_top)
    mask = sub.var["highly_variable"].to_numpy()
    sub = sub[:, mask].copy()
    info["selected_genes"] = sub.var_names.to_numpy(dtype=str)
    info["n_selected_genes"] = sub.n_vars
    n_pcs = min(params["n_pcs"], sub.n_obs - 1, sub.n_vars - 1)
    if n_pcs < 2:
        info["status"] = "insufficient_features"
        return labels, eligible, info
    seed = params["random_state"]
    rings = rings if units is not None else 0
    try:
        # Sparse centered PCA; never materialize an n_obs x n_genes dense matrix.
        sc.pp.pca(sub, n_comps=n_pcs, random_state=seed, svd_solver="arpack")
        sub.obsm["X_spatial"] = (spatial_embedding(sub.obsm["X_pca"], smoothing_operator(units, rings, eligible))
                                 if rings else sub.obsm["X_pca"])
        # Scanpy uses approximate neighbor construction on large samples.
        sc.pp.neighbors(sub, n_neighbors=min(params["n_neighbors"], sub.n_obs - 1),
                        use_rep="X_spatial", random_state=seed)
        sc.tl.leiden(sub, resolution=params["leiden_resolution"], key_added="cluster",
                     flavor="igraph", n_iterations=2, directed=False, random_state=seed)
    except MemoryError as error:
        raise MemoryError("ST clustering exceeded available memory; use the sub-cellular resolution "
                          "preset for very large samples.") from error
    labels[eligible] = sub.obs["cluster"].to_numpy(dtype=str)
    info["n_clusters"] = int(sub.obs["cluster"].nunique())
    info["status"] = "complete"
    info["n_pcs"] = n_pcs
    info["smoothing_rings"] = rings
    info["pc_variance_ratio"] = sub.uns["pca"]["variance_ratio"]
    depth = np.log1p(counts[eligible])
    correlations = []
    for pc in sub.obsm["X_pca"].T:
        correlations.append(float(spearmanr(pc, depth).statistic) if np.ptp(pc) > 0 and np.ptp(depth) > 0 else 0.0)
    info["pc_log_counts_spearman"] = np.asarray(correlations)
    coherence = label_coherence(labels[eligible].astype(str), units, eligible) if units is not None else None
    if coherence is not None:
        info["label_coherence"] = coherence
    unique, sizes = np.unique(labels[eligible], return_counts=True)
    info["cluster_sizes"] = dict(zip(unique.tolist(), sizes.tolist()))
    if reporter:
        reporter.detail(f"Clustering: {sub.n_vars} HVGs, {n_pcs} PCs, smoothing {rings} rings, "
                        f"{info['n_clusters']} clusters, coherence {'n/a' if coherence is None else f'{coherence:.2f}'}; "
                        f"|PC1-UMI rho|={abs(correlations[0]):.3f}")
    return labels, eligible, info
