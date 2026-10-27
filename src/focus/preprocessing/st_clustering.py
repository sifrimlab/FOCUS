"""ST clustering for alignment colors. Analytical copies never alter exported counts."""
from importlib.metadata import version

import anndata as ad
import numpy as np
import scanpy as sc
import scipy.sparse as sp
from scipy.stats import spearmanr

from focus.preprocessing._utils import _bin_sum_matrix, describe_units, plan_spatial_units
from focus.preprocessing.st_settings import METHOD_VERSION, clustering_settings


def _check_raw_counts(X, chunk=50_000_000):
    # Chunked so a matrix with hundreds of millions of entries never needs full-size temporaries.
    for start in range(0, X.nnz, chunk):
        data = X.data[start:start + chunk]
        if not np.all(np.isfinite(data)) or np.any(data < 0) or np.any(data != np.floor(data)):
            raise ValueError("ST clustering requires finite, nonnegative raw integer counts in .X")


def spot_cluster_labels(X, gene_names, coords, obs=None, spot_size=None, settings=None, reporter=None):
    """Per-spot labels, eligibility and provenance, with bounded cost at any spot count.

    :func:`focus.preprocessing._utils.plan_spatial_units` keeps the spots as they are, or groups
    them into k x k acquisition-lattice blocks (Voronoi supercells without a lattice) when the
    sample exceeds ``clustering_max_units`` or its median spot holds fewer than
    ``clustering_min_counts``. Unit counts are summed, :func:`native_cluster_labels` clusters
    the units, and every spot takes its unit's label and eligibility. Units are never persisted.
    """
    params = clustering_settings(settings or {})
    X = sp.csr_matrix(X)
    _check_raw_counts(X)
    bin_ids, n_units, units = plan_spatial_units(
        coords, cap=params["clustering_max_units"], obs=obs, spot_size=spot_size,
        counts=np.asarray(X.sum(axis=1)).ravel(), min_median_counts=params["clustering_min_counts"],
        seed=params["clustering_random_state"])
    if reporter:
        reporter.detail(describe_units(X.shape[0], units))
        if units.get("signal_limited"):
            reporter.warning(f"Clustering: median unit stays below {params['clustering_min_counts']} counts "
                             f"at the coarsest allowed size; most spots may be unassigned")
    if bin_ids is None:
        labels, eligible, info = native_cluster_labels(X, gene_names, params, reporter, checked=True)
    else:
        unit_labels, unit_eligible, info = native_cluster_labels(
            _bin_sum_matrix(X, bin_ids, n_units), gene_names, params, reporter, checked=True)
        labels, eligible = unit_labels[bin_ids], unit_eligible[bin_ids]
    info["units"] = units
    info["n_spots"] = X.shape[0]
    info["n_eligible_spots"] = int(eligible.sum())
    if info["status"] == "complete":
        unique, sizes = np.unique(labels[eligible], return_counts=True)
        info["cluster_sizes"] = dict(zip(unique.tolist(), sizes.tolist()))
    return labels, eligible, info


def native_cluster_labels(X, gene_names, settings=None, reporter=None, checked=False):
    """Return labels, eligibility and provenance for exactly the supplied rows (spots or units).

    Defaults of 50 UMIs / 20 detected genes are alignment-only display eligibility,
    not universal tissue QC thresholds. Users can lower them to 1 for sparse assays.
    No spatial coordinates enter the model; grouping spots into units is the job of
    :func:`spot_cluster_labels`.
    """
    params = clustering_settings(settings or {})
    X = sp.csr_matrix(X)
    if not checked:
        _check_raw_counts(X)
    counts = np.asarray(X.sum(axis=1)).ravel()
    detected = np.asarray((X > 0).sum(axis=1)).ravel()
    eligible = (counts > 0) & (counts >= params["clustering_min_counts"]) & (detected >= params["clustering_min_genes"])
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
    n_top = min(params["clustering_n_top_genes"], sub.n_vars)
    # Seurat dispersion selection consumes log-normalized data and needs no extra dependency.
    sc.pp.highly_variable_genes(sub, flavor="seurat", n_top_genes=n_top)
    mask = sub.var["highly_variable"].to_numpy()
    sub = sub[:, mask].copy()
    info["selected_genes"] = sub.var_names.to_numpy(dtype=str)
    info["n_selected_genes"] = sub.n_vars
    n_pcs = min(params["clustering_n_pcs"], sub.n_obs - 1, sub.n_vars - 1)
    if n_pcs < 2:
        info["status"] = "insufficient_features"
        return labels, eligible, info
    seed = params["clustering_random_state"]
    try:
        # Sparse centered PCA; never materialize an n_obs x n_genes dense matrix.
        sc.pp.pca(sub, n_comps=n_pcs, random_state=seed, svd_solver="arpack")
        # Scanpy uses approximate neighbor construction on large samples.
        sc.pp.neighbors(sub, n_neighbors=min(params["clustering_n_neighbors"], sub.n_obs - 1),
                        n_pcs=n_pcs, random_state=seed)
        sc.tl.leiden(sub, resolution=params["clustering_resolution"], key_added="cluster",
                     flavor="igraph", n_iterations=2, directed=False, random_state=seed)
    except MemoryError as error:
        raise MemoryError("ST clustering exceeded available memory. Lower clustering_max_units or clustering_n_top_genes.") from error
    labels[eligible] = sub.obs["cluster"].to_numpy(dtype=str)
    info["n_clusters"] = int(sub.obs["cluster"].nunique())
    info["status"] = "complete"
    info["n_pcs"] = n_pcs
    info["pc_variance_ratio"] = sub.uns["pca"]["variance_ratio"]
    depth = np.log1p(counts[eligible])
    correlations = []
    for pc in sub.obsm["X_pca"].T:
        correlations.append(float(spearmanr(pc, depth).statistic) if np.ptp(pc) > 0 and np.ptp(depth) > 0 else 0.0)
    info["pc_log_counts_spearman"] = np.asarray(correlations)
    unique, sizes = np.unique(labels[eligible], return_counts=True)
    info["cluster_sizes"] = dict(zip(unique.tolist(), sizes.tolist()))
    if reporter:
        reporter.detail(f"Clustering: {sub.n_vars} HVGs, {n_pcs} PCs, {info['n_clusters']} clusters; |PC1-UMI rho|={abs(correlations[0]):.3f}")
    return labels, eligible, info
