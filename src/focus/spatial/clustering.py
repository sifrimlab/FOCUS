"""Alignment display labels of spot modalities (ST, MSI), shared by both and driven by presets."""

from collections.abc import Callable
from importlib.metadata import version
from typing import TypeAlias, cast

import gc

import anndata as ad
import numpy as np
import scipy.sparse as sp

from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter
from focus.spatial.clusters import label_coherence
from focus.spatial.display import all_spot_matrix, levels, spot_display
from focus.spatial.selection import clustering_features
from focus.spatial.partition import Neighbourhood, partition
from focus.spatial.smoothing import similarity_operator
from focus.spatial.settings import (
    COUNTS,
    DOMAINS,
    FOREGROUND,
    GRADIENT,
    PARAMETERS,
)
from focus.spatial.types import (
    ClusterInfo,
    ClusteringParameters,
    ClusteringPreset,
    Matrix,
)
from focus.spatial.units import (
    SpatialUnits,
    UnitSignal,
    describe_units,
    plan_spatial_units,
)

# Version of the shared clustering method, recorded in every sample's provenance.
CLUSTERING_VERSION = "clusters-4"

# (summed raw rows of the kept units, units, kept unit ids) -> (log-scale rows, description)
Represent: TypeAlias = Callable[
    [sp.csr_matrix, SpatialUnits, np.ndarray], tuple[sp.csr_matrix, str]
]
# summed raw rows of the units -> eligible units
UnitEligibility: TypeAlias = Callable[[sp.csr_matrix], np.ndarray]


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


def unit_mean(
    X: Matrix, units: SpatialUnits, kept: np.ndarray, include: np.ndarray
) -> sp.csr_matrix:
    """Mean of the included rows of ``X`` within each kept unit (``X`` itself for native units)."""
    X = sp.csr_matrix(X, dtype=np.float32)
    if units.bin_ids is None:
        return cast(sp.csr_matrix, X[kept])
    weights = include.astype(np.float32)
    sums = sp.csr_matrix(
        _bin_sum_matrix(sp.diags(weights) @ X, units.bin_ids, units.n_units)
    )
    size = np.bincount(units.bin_ids, weights=weights, minlength=units.n_units)[kept]
    return cast(sp.csr_matrix, sp.diags(1.0 / size).astype(np.float32) @ sums[kept])


def _unit_signal(raw: sp.csr_matrix, include: np.ndarray, signal: str) -> UnitSignal:
    """Signal of every unit holding an included spot: summed counts or detected features.

    Detected features are counted on the summed rows of a unit, so they are not additive over
    its spots and are recomputed for every candidate unit plan.
    """
    rows = cast(sp.csr_matrix, raw[include])
    totals = np.asarray(rows.sum(axis=1), dtype=np.float64).ravel()

    def unit_signal(flat: np.ndarray) -> np.ndarray:
        """Signal of every unit id of ``flat`` that holds an included spot."""
        _, inverse = np.unique(flat[include], return_inverse=True)
        n_units = int(inverse.max()) + 1
        if signal == COUNTS:
            return np.bincount(inverse, weights=totals, minlength=n_units)
        summed = sp.csr_matrix(_bin_sum_matrix(rows, inverse, n_units))
        summed.eliminate_zeros()
        return cast(np.ndarray, np.diff(summed.indptr).astype(np.float64))

    return unit_signal


def cluster_spots(
    raw: Matrix,
    coords: np.ndarray,
    preset: ClusteringPreset,
    represent: Represent,
    *,
    feature_names: np.ndarray,
    include: np.ndarray | None = None,
    unit_eligible: UnitEligibility | None = None,
    params: ClusteringParameters | None = None,
    reporter: StepReporter | None = None,
) -> tuple[np.ndarray, np.ndarray, ClusterInfo]:
    """Per-spot display labels, eligibility and provenance of one sample.

    The labels colour spots in the alignment GUI only; no algorithm reads them. Steps:

    1. ``foreground`` mode: the included spots are ``'0'``, the rest ``'unassigned'``; no clustering.
    2. :func:`plan_spatial_units` keeps native spots, or groups them into k x k lattice blocks
       (Voronoi supercells without a lattice) above the unit cap or, under the sub_cellular
       resolution, until the median unit reaches the preset's signal target.
    3. The raw rows of the included spots are summed per unit. A unit is eligible when it holds an
       included spot and passes ``unit_eligible`` (default: any nonzero feature).
    4. ``represent`` (from the modality) turns the summed raw rows of the eligible units into a
       normalized, log-scale matrix: raw values are never clustered.
    5. Up to ``n_top_features`` highly variable features (Scanpy, Seurat dispersion flavor) and
       sparse PCA.
    6. ``domains`` mode: kNN graph on the spatial embedding, Leiden at an overclustering resolution (lowered
       while the median cluster is too small for the valley test, :func:`_overcluster`), then
       :func:`merge_unsupported_clusters` joins adjacent clusters without a density valley.
       ``gradient`` mode: ordered quantile levels of the neighbourhood mean of the first component.
    7. Every spot takes its unit's label; spots of ineligible units and excluded spots are
       ``'unassigned'``.

    Space enters through the clustering embedding only: each unit's components are joined to their
    mean over its spatial neighbourhood (``preset['rings']``), weighted by expression similarity so
    the mean does not cross sharp boundaries (:func:`similarity_operator`). The representation and
    the labels are never smoothed. Units and every intermediate
    live in memory only. Deterministic (seeded PCA, neighbours and Leiden).

    Parameters
    ----------
    raw : scipy.sparse matrix | np.ndarray
            (n_spots, n_features) raw counts or intensities; not modified.
    coords : np.ndarray
            (n_spots, 2) spot coordinates (``obsm['spatial']``).
    preset : ClusteringPreset
            From :func:`focus.spatial.settings.clustering_preset`.
    represent : Represent
            Normalization and log transform of the summed raw rows of the eligible units.
    feature_names : np.ndarray
            (n_features,) names, recorded for the selected features.
    include : np.ndarray | None
            (n_spots,) spots taking part (MSI foreground); default every spot.
    unit_eligible : UnitEligibility | None
            Eligibility of the units from their summed raw rows (ST counts and genes).
    params : ClusteringParameters | None
            Overrides of :data:`focus.spatial.settings.PARAMETERS`.
    reporter : StepReporter | None
            Receives the unit plan and the clustering summary.
    """
    reporter = reporter or get_reporter()
    params = {**PARAMETERS, **(params or {})}
    raw = sp.csr_matrix(raw, dtype=np.float32)
    n_spots = raw.shape[0]
    include = (
        np.ones(n_spots, dtype=bool)
        if include is None
        else np.asarray(include, dtype=bool)
    )
    labels = np.full(n_spots, "unassigned", dtype=object)
    info = _new_info(preset, params, n_spots)
    if preset["mode"] == FOREGROUND:
        labels[include] = "0"
        info["status"] = "foreground_only"
        info["n_clusters"] = int(include.any())
        reporter.detail(
            f"Clustering: foreground only, {int(include.sum())}/{n_spots} foreground spots"
        )
        return _finish(labels, include.copy(), info)
    if include.sum() < 3:
        return _finish(labels, include & False, info)

    units = _plan_units(raw, coords, include, preset, params, reporter)
    summed, unit_ok = _eligible_units(raw, units, include, unit_eligible)
    bin_ids = np.arange(n_spots) if units.bin_ids is None else units.bin_ids
    kept = np.flatnonzero(unit_ok)
    eligible = include & unit_ok[bin_ids]
    info["units"] = units.info
    info["n_input"] = units.n_units
    info["n_eligible"] = len(kept)
    reporter.detail(f"Clustering: {len(kept)}/{units.n_units} eligible units")
    if len(kept) < 3:
        return _finish(labels, eligible, info)

    X, info["representation"] = represent(
        cast(sp.csr_matrix, summed[kept]), units, kept
    )
    sub, ranking = clustering_features(X, feature_names, units, unit_ok, params, info)
    unit_labels = _partition(sub, summed[kept], units, unit_ok, preset, params, info)
    if sub is None or unit_labels is None:
        info["status"] = "insufficient_features"
        return _finish(labels, eligible, info)
    fine, unit_labels = levels(
        sub, unit_labels, preset["mode"] == DOMAINS, units, unit_ok, info
    )
    del sub
    gc.collect()
    info["status"] = "complete"
    info["n_clusters"] = int(np.unique(unit_labels).size)
    labels[eligible] = unit_labels[np.searchsorted(kept, bin_ids[eligible])]
    spots = (eligible, kept, bin_ids)
    everywhere = all_spot_matrix(raw, represent, units)
    info["display"] = spot_display(fine, X, ranking, feature_names, spots, everywhere)
    _record_coherence(unit_labels, units, unit_ok, info)
    reporter.detail(_summary(info, preset["mode"]))
    return _finish(labels, eligible, info)


def _partition(
    sub: ad.AnnData | None,
    summed: sp.csr_matrix,
    units: SpatialUnits,
    unit_ok: np.ndarray,
    preset: ClusteringPreset,
    params: ClusteringParameters,
    info: ClusterInfo,
) -> np.ndarray | None:
    """Unit labels of the preset's mode (:func:`partition`); None without features."""
    if sub is None:
        return None
    depth = np.log1p(np.asarray(summed.sum(axis=1), dtype=np.float64).ravel())
    neighbourhood = _neighbourhood(units, unit_ok, preset["rings"])
    return partition(sub, preset["mode"], params, depth, info, neighbourhood)


def _record_coherence(
    labels: np.ndarray, units: SpatialUnits, unit_ok: np.ndarray, info: ClusterInfo
) -> None:
    """Record the chance-corrected neighbour agreement of the labels, when defined."""
    coherence = label_coherence(labels, units, unit_ok)
    if coherence is not None:
        info["label_coherence"] = coherence


def _neighbourhood(
    units: SpatialUnits, unit_ok: np.ndarray, rings: int
) -> Neighbourhood | None:
    """Edge-preserving neighbourhood of the eligible units, built from their components."""
    if rings == 0:
        return None
    return lambda pcs: similarity_operator(units, rings, unit_ok, pcs)


def _new_info(
    preset: ClusteringPreset, params: ClusteringParameters, n_spots: int
) -> ClusterInfo:
    """Provenance of a clustering before any step has run."""
    return {
        "method": CLUSTERING_VERSION,
        "mode": preset["mode"],
        "parameters": {**params, **preset},
        "software_versions": {
            name: version(name)
            for name in ("scanpy", "numpy", "scipy", "scikit-learn", "igraph")
        },
        "n_spots": n_spots,
        "n_clusters": 0,
        "status": "insufficient_observations",
    }


def _finish(
    labels: np.ndarray, eligible: np.ndarray, info: ClusterInfo
) -> tuple[np.ndarray, np.ndarray, ClusterInfo]:
    """Record per-spot eligibility and cluster sizes; return labels, eligibility, provenance."""
    info["n_eligible_spots"] = int(eligible.sum())
    unique, sizes = np.unique(labels[eligible].astype(str), return_counts=True)
    info["cluster_sizes"] = dict(zip(unique.tolist(), sizes.tolist()))
    return labels, eligible, info


def _plan_units(
    raw: sp.csr_matrix,
    coords: np.ndarray,
    include: np.ndarray,
    preset: ClusteringPreset,
    params: ClusteringParameters,
    reporter: StepReporter,
) -> SpatialUnits:
    """Unit plan of the preset (cap, and the signal target under sub_cellular), reported."""
    signal = preset["signal"]
    units = plan_spatial_units(
        coords,
        cap=params["max_units"],
        unit_signal=None if signal is None else _unit_signal(raw, include, signal),
        signal_target=preset["signal_target"],
        seed=params["random_state"],
    )
    reporter.detail(describe_units(raw.shape[0], units.info))
    if units.info.get("signal_limited"):
        reporter.warning(
            f"Clustering: median unit stays below {preset['signal_target']:g} {signal} at the "
            f"coarsest allowed size; labels may be noisy"
        )
    return units


def _eligible_units(
    raw: sp.csr_matrix,
    units: SpatialUnits,
    include: np.ndarray,
    unit_eligible: UnitEligibility | None,
) -> tuple[sp.csr_matrix, np.ndarray]:
    """Summed raw rows of the included spots of every unit, and the eligible units.

    A unit is eligible when it holds an included spot, has a nonzero feature and passes
    ``unit_eligible``.
    """
    bin_ids = np.arange(raw.shape[0]) if units.bin_ids is None else units.bin_ids
    if units.bin_ids is None and include.all():
        summed = raw.copy()
    else:
        summed = sp.csr_matrix(
            _bin_sum_matrix(
                sp.diags(include.astype(np.float32)) @ raw, bin_ids, units.n_units
            )
        )
    summed.eliminate_zeros()
    present = np.bincount(bin_ids, weights=include, minlength=units.n_units) > 0
    unit_ok = present & (np.diff(summed.indptr) > 0)
    if unit_eligible is not None:
        unit_ok &= unit_eligible(summed)
    return summed, unit_ok


def _summary(info: ClusterInfo, mode: str) -> str:
    """One reporter line for a completed clustering."""
    partition = (
        f"{info['n_clusters']} gradient levels"
        if mode == GRADIENT
        else (
            f"Leiden {info['leiden_resolution']:g}: {info['n_leiden_clusters']} -> "
            f"{info['n_clusters']} clusters"
        )
    )
    coherence = info.get("label_coherence")
    rings = info["parameters"].get("rings", 0)
    return (
        f"Clustering: {info['representation']}, {info['n_selected_features']} features, "
        f"{info['n_pcs']} PCs, neighbourhood {rings} rings, {partition}, "
        f"coherence {'n/a' if coherence is None else f'{coherence:.2f}'}"
    )
