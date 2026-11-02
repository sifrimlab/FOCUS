"""Compute analytical display labels on independent spatial-unit expression copies."""

from typing import cast
from focus.spatial.units import SpatialUnits
from focus.spatial.types import Matrix

import gc

import numpy as np
import scipy.sparse as sp

from focus.runtime.reporter_context import get_reporter
from focus.spatial.settings import N_TOP_FEATURES, _SPATIAL_CAP
from focus.spatial.units import describe_units, plan_spatial_units
from focus.spatial.clusters import label_coherence, merge_unsupported_clusters
from focus.spatial.smoothing import smoothing_operator, spatial_embedding


def _bin_sum_matrix(X: Matrix, bin_ids: np.ndarray, n_bins: int) -> Matrix:
    """Sum the rows of ``X`` within each bin: returns ``A @ X`` of shape ``(n_bins, n_vars)``.

    ``A`` is the ``(n_bins, n_obs)`` sparse 0/1 assignment matrix (one ``1`` per column at the
    point's bin). Sparse ``X`` stays sparse (CSR @ CSR -> CSR); dense ``X`` yields a dense result.
    The summed matrix is an internal, throwaway intermediate — it is never persisted.
    """
    n_obs = X.shape[0]
    A = sp.csr_matrix(
        (np.ones(n_obs, dtype=np.float32), (bin_ids, np.arange(n_obs, dtype=np.intp))),
        shape=(n_bins, n_obs),
    )
    # SciPy widens sparse products to its base type; CSR @ CSR remains CSR.
    return cast(Matrix, A @ X)


def _divide_rows_by_nonzero_median(X: Matrix) -> sp.csr_matrix:
    """``X`` with every row divided by the median of its nonzero values (rows without any stay 0)."""
    X = sp.csr_matrix(X, dtype=np.float32)
    X.eliminate_zeros()
    medians = np.ones(X.shape[0], dtype=np.float32)
    for row in range(X.shape[0]):
        values = X.data[X.indptr[row] : X.indptr[row + 1]]
        if values.size:
            medians[row] = np.median(values)
    return cast(sp.csr_matrix, sp.diags(1.0 / medians).astype(np.float32) @ X)


def compute_cluster_labels(
    X: Matrix,
    *,
    leiden_resolution: float,
    normalize_target_sum: float | None,
    coordinates: np.ndarray | None = None,
    smoothing_rings: int = 1,
    cap: int = _SPATIAL_CAP,
    n_pcs_cap: int = 50,
    random_state: int = 0,
) -> np.ndarray:
    """Compute per-spot cluster labels for alignment-stage spot colouring, scalably.

    The labels are consumed only by the alignment GUI for categorical colouring (no
    downstream algorithm uses them numerically), so the goal is a fast, "Leiden-like"
    partition rather than an exact Leiden over every spot — which does not scale to
    millions of points. Large samples are *coarsened* instead of subsampled, which also
    aggregates the weak per-spot signal of ultra-high-resolution modalities:

    1. When ``n_obs > cap`` (and ``coordinates`` are given), :func:`plan_spatial_units` groups
       the spots into at most ``cap`` units: k x k blocks of the acquisition raster (inferred
       from ``coordinates``) or Voronoi supercells when no raster is found. Every spot of a unit
       is SUMMED into one pseudo-spot (:func:`_bin_sum_matrix`). Smaller samples run on every spot.
    2. Normalize and log1p the run matrix (see ``normalize_target_sum``), keep the
       ``N_TOP_FEATURES`` most variable features (as ST does), run PCA, append each unit's
       ``smoothing_rings`` neighbourhood mean to its PCs (:func:`spatial_embedding`), then
       ``neighbors -> leiden``, bounded by ``cap`` rows.
    3. Merge adjacent clusters that no density valley separates
       (:func:`merge_unsupported_clusters`).
    4. Propagate each unit's label back to every spot of the unit (identity when no
       coarsening happened). No per-spot projection.

    ``X`` is never mutated and the summed / normalized matrix lives only on a throwaway
    ``AnnData`` that is discarded — none of these internal intermediates are persisted; the
    function returns only the per-spot label array. Fully deterministic (binning is RNG-free;
    PCA / Leiden seeded via ``random_state``).

    Parameters
    ----------
    X : scipy.sparse matrix | np.ndarray
            Raw, non-negative counts or intensities, shape (n_obs, n_vars).
    leiden_resolution : float
            Resolution for the Leiden run on the (binned or full) run matrix.
    normalize_target_sum : float | None
            Normalization of every row of the run matrix before log1p and PCA. A float (spatial
            transcriptomics counts): total-count normalization to this target. ``None`` (MSI
            intensities): division by the median of the row's nonzero values, which a few intense
            ions cannot dominate the way they dominate the total, and which puts the log1p offset at a
            typical peak of the row whatever the instrument's intensity scale.
    coordinates : np.ndarray | None
            (n_obs, 2) spatial coordinates enabling coarsening. When ``None``, no coarsening is
            done and Leiden runs on every spot regardless of ``cap``.
    smoothing_rings : int
            Neighbourhood of the embedding smoothing (``CLUSTERING_PRESETS``); 0 disables it.
    cap : int
            Maximum number of rows Leiden runs on (= maximum number of units). Samples at or
            below this are clustered directly.
    n_pcs_cap : int
            Upper bound on the number of principal components.
    random_state : int
            Seed for PCA / Leiden.

    Returns
    -------
    np.ndarray
            Length-n_obs array of string cluster labels (dtype object). All ``'0'`` when the
            sample is too small to cluster or resolves to a single cluster.
    """
    import anndata as ad
    import scanpy as sc

    n_obs, n_vars = X.shape

    # 1. Build the matrix Leiden runs on: summed units for large samples, else all spots.
    units, bin_ids = None, None
    if coordinates is not None:
        units = plan_spatial_units(coordinates, cap=cap, seed=random_state)
        bin_ids = units.bin_ids
        get_reporter().detail(describe_units(n_obs, units.info))
    if bin_ids is not None:
        X_run = _bin_sum_matrix(
            X, bin_ids, cast(SpatialUnits, units).n_units
        )  # (n_units, n_vars); sparse-preserving, throwaway
    else:
        X_run = X.copy()  # copy: normalize_total/log1p mutate in place

    n_run = X_run.shape[0]
    if n_run < 2 or n_vars < 3:
        return np.array(["0"] * n_obs, dtype=object)

    # 2. Normalize the run matrix, then pca -> neighbors -> leiden on the bounded matrix. The row
    # normalization also washes out how many spots a summed unit holds.
    if normalize_target_sum is None:
        X_run = _divide_rows_by_nonzero_median(X_run)
    sub = ad.AnnData(X=X_run)
    if normalize_target_sum is not None:
        sc.pp.normalize_total(sub, target_sum=normalize_target_sum, inplace=True)
    sc.pp.log1p(sub)
    # Same feature budget as ST; the 'cell_ranger' flavor ranks mean-binned normalized dispersion.
    if sub.n_vars > N_TOP_FEATURES:
        sc.pp.highly_variable_genes(
            sub, flavor="cell_ranger", n_top_genes=N_TOP_FEATURES
        )
        sub = sub[:, sub.var["highly_variable"].to_numpy()].copy()
    n_pcs = min(n_pcs_cap, n_run - 1, sub.n_vars - 1)
    if n_pcs < 2:
        return np.array(["0"] * n_obs, dtype=object)
    sc.pp.pca(sub, n_comps=n_pcs)  # random_state=0 by default
    sub.obsm["X_spatial"] = (
        spatial_embedding(sub.obsm["X_pca"], smoothing_operator(units, smoothing_rings))
        if units is not None and smoothing_rings > 0
        else sub.obsm["X_pca"]
    )
    sc.pp.neighbors(sub, n_neighbors=min(15, sub.n_obs - 1), use_rep="X_spatial")
    sc.tl.leiden(
        sub,
        resolution=leiden_resolution,
        key_added="leiden",
        flavor="igraph",
        n_iterations=2,
        directed=False,
    )
    # 3. Merge the splits of continuous variation.
    run_labels, merged = merge_unsupported_clusters(
        sub.obsm["X_spatial"], sub.obs["leiden"].to_numpy(), sub.obsp["connectivities"]
    )
    del sub
    gc.collect()
    if units is not None:
        coherence = label_coherence(run_labels, units)
        get_reporter().detail(
            f"Clustering: {merged['n_leiden_clusters']} -> {np.unique(run_labels).size} clusters, "
            f"smoothing {smoothing_rings} rings, coherence {'n/a' if coherence is None else f'{coherence:.2f}'}"
        )

    if np.unique(run_labels).shape[0] == 1:
        return np.array(["0"] * n_obs, dtype=object)

    # 4. Propagate: each spot inherits its unit's label (identity when no coarsening happened).
    labels = run_labels if bin_ids is None else run_labels[bin_ids]
    return labels.astype(str).astype(object)
