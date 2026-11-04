"""Spot quality control: structural cleanup and the technology's outlier rule."""

from typing import cast

import anndata as ad
import numpy as np
import scipy.sparse as sp
from scipy.spatial import cKDTree

from focus.preprocessing.transcriptomics.technology import (
    GLOBAL_NMADS,
    LOCAL_CUTOFF,
    LOCAL_NEIGHBOURS,
    Technology,
)
from focus.runtime.reporting import StepReporter

# Consistency constant that makes the MAD estimate the standard deviation of a normal sample.
_MAD_SCALE = 1.4826


def spot_metrics(adata: ad.AnnData) -> dict[str, np.ndarray]:
    """Per-spot library size, detected genes and mitochondrial percent, from ``.X`` and ``var['mt']``."""
    X = cast(sp.csr_matrix, adata.X)
    total = np.asarray(X.sum(axis=1)).ravel().astype(np.float64)
    detected = np.diff(X.indptr).astype(np.float64)
    if X.nnz and np.any(X.data == 0):
        detected = np.asarray((X > 0).sum(axis=1)).ravel().astype(np.float64)
    metrics = {"total_counts": total, "n_genes_by_counts": detected}
    mt = adata.var["mt"].to_numpy() if "mt" in adata.var else None
    if mt is not None and mt.any():
        mito = np.asarray(X[:, mt].sum(axis=1)).ravel()
        metrics["pct_counts_mt"] = np.divide(
            100.0 * mito, total, out=np.zeros_like(total), where=total > 0
        )
    return metrics


def local_outliers(
    values: np.ndarray,
    coords: np.ndarray,
    direction: str,
    n_neighbours: int = LOCAL_NEIGHBOURS,
    cutoff: float = LOCAL_CUTOFF,
) -> np.ndarray:
    """
    Spots whose log value departs from their spatial neighbourhood (SpotSweeper).

    The neighbourhood is the spot and its ``n_neighbours`` nearest spots. The robust z is
    ``(x - median) / (1.4826 * MAD)`` over the neighbourhood, on ``log1p`` values; a spot is an
    outlier when ``z < -cutoff`` (``direction='lower'``) or ``z > cutoff`` (``'higher'``). A
    neighbourhood with zero MAD flags nothing.
    """
    n = values.shape[0]
    if n < 2:
        return np.zeros(n, dtype=bool)
    k = min(n_neighbours + 1, n)
    _, index = cKDTree(coords).query(coords, k=k)
    logged = np.log1p(values).astype(np.float32)
    neighbourhood = logged[index]
    median = np.median(neighbourhood, axis=1)
    mad = _MAD_SCALE * np.median(np.abs(neighbourhood - median[:, None]), axis=1)
    z = np.divide(logged - median, mad, out=np.zeros(n, np.float32), where=mad > 0)
    flagged = z < -cutoff if direction == "lower" else z > cutoff
    return cast(np.ndarray, flagged)


def global_outliers(values: np.ndarray, nmads: float = GLOBAL_NMADS) -> np.ndarray:
    """Low-tail outliers: ``log1p`` value below the median minus ``nmads`` scaled MADs."""
    logged = np.log1p(values)
    median = np.median(logged)
    mad = _MAD_SCALE * np.median(np.abs(logged - median))
    if mad == 0:
        return np.zeros(values.shape[0], dtype=bool)
    return cast(np.ndarray, logged < median - nmads * mad)


def quality_control(
    adata: ad.AnnData,
    tech: Technology,
    enabled: bool,
    reporter: StepReporter,
    drop_empty: bool = True,
) -> tuple[ad.AnnData, dict[str, int]]:
    """
    Remove out-of-tissue spots, zero-count spots (``drop_empty``), then the technology's outliers
    when ``enabled``.

    ``spot`` units use local outliers (low library size, low detected genes, high mitochondrial
    percent); ``cell`` units use global low-tail outliers of library size and detected genes;
    ``bin`` units have no outlier rule. Returns the retained spots and the number of spots removed
    per reason (a spot is counted under the first reason that removes it).
    """
    removed: dict[str, int] = {}
    keep = np.ones(adata.n_obs, dtype=bool)

    def drop(reason: str, mask: np.ndarray) -> None:
        """Record and apply one removal reason."""
        new = mask & keep
        removed[reason] = int(new.sum())
        keep[new] = False

    if "in_tissue" in adata.obs:
        drop("outside_tissue", adata.obs["in_tissue"].astype(int).to_numpy() == 0)
    metrics = spot_metrics(adata)
    if drop_empty:
        drop("zero_counts", metrics["total_counts"] == 0)

    rule = tech.outlier_rule if enabled else "none"
    if rule != "none":
        kept = np.flatnonzero(keep)
        tests = [("low_counts", "total_counts"), ("low_genes", "n_genes_by_counts")]
        flags: dict[str, np.ndarray] = {}
        if rule == "local":
            coords = np.asarray(adata.obsm["spatial"], dtype=np.float64)[kept]
            for reason, metric in tests:
                flags[reason] = local_outliers(metrics[metric][kept], coords, "lower")
            if "pct_counts_mt" in metrics:
                flags["high_mito"] = local_outliers(
                    metrics["pct_counts_mt"][kept], coords, "higher"
                )
        else:
            for reason, metric in tests:
                flags[reason] = global_outliers(metrics[metric][kept])
        for reason, flagged in flags.items():
            mask = np.zeros(adata.n_obs, dtype=bool)
            mask[kept[flagged]] = True
            drop(reason, mask)

    total_removed = adata.n_obs - int(keep.sum())
    reasons = ", ".join(f"{name} {count}" for name, count in removed.items() if count)
    reporter.detail(
        f"Retained {int(keep.sum())} / {adata.n_obs} spots"
        + (f" (removed {total_removed}: {reasons})" if total_removed else "")
    )
    if total_removed:
        adata = adata[keep].copy()
    return adata, removed
