"""ST adapter of the shared alignment clustering. Analytical copies never alter exported counts."""

from collections.abc import Iterable

import numpy as np
import scipy.sparse as sp

from focus.config.options import StNormalization
from focus.preprocessing.transcriptomics.normalization import (
    _log1p,
    normalized_matrix,
)
from focus.preprocessing.transcriptomics.technology import Technology
from focus.runtime.reporting import StepReporter
from focus.spatial.clustering import cluster_spots
from focus.spatial.settings import COUNTS, DOMAINS, clustering_preset
from focus.spatial.types import ClusterInfo, ClusteringParameters, Matrix
from focus.spatial.units import SpatialUnits

METHOD_VERSION = "st-7"
# Per-unit display eligibility of the alignment labels, not tissue QC.
PARAMETERS: ClusteringParameters = {"min_counts": 50, "min_genes": 20}


def _check_raw_counts(X: sp.csr_matrix, chunk: int = 50_000_000) -> None:
    # Chunked so a matrix with hundreds of millions of entries never needs full-size temporaries.
    """Validate the expression matrix before analytical clustering."""
    for start in range(0, X.nnz, chunk):
        data = X.data[start : start + chunk]
        if (
            not np.all(np.isfinite(data))
            or np.any(data < 0)
            or np.any(data != np.floor(data))
        ):
            raise ValueError(
                "ST clustering requires finite, nonnegative raw integer counts in .X"
            )


def clustering_normalization(
    tech: Technology, requested: str, area: np.ndarray | None
) -> str:
    """The normalization the labels are computed on: the configured one, never raw counts.

    ``none`` falls back to the technology's ``auto`` method, and area normalization falls back to
    ``log_normalize`` when some spot has no finite positive area, as the merged normalization does
    for ``auto``.
    """
    method = tech.normalization(
        StNormalization.AUTO if requested == StNormalization.NONE else requested
    )
    if method == StNormalization.AREA_LOG_NORMALIZE and (
        area is None or not np.all(np.isfinite(area) & (area > 0))
    ):
        method = StNormalization.LOG_NORMALIZE
    return method


def spot_cluster_labels(
    X: Matrix,
    gene_names: Iterable[str],
    coords: np.ndarray,
    tech: Technology,
    normalization: str = StNormalization.AUTO,
    area: np.ndarray | None = None,
    reporter: StepReporter | None = None,
    params: ClusteringParameters | None = None,
) -> tuple[np.ndarray, np.ndarray, ClusterInfo]:
    """Per-spot labels, eligibility and provenance of one sample (raw counts in ``X``).

    :func:`focus.spatial.clustering.cluster_spots` in ``domains`` mode. ``tech.resolution`` sets
    the unit growth (``sub_cellular``: until the median unit holds 1,000 UMIs). A unit is
    eligible with at least 50 UMIs and 20 detected genes. The summed counts of the eligible units
    are normalized with :func:`clustering_normalization` (``area``: per-spot area for area
    normalization) and log-transformed (``size_factors`` is linear, so ``log1p`` follows).
    ``params`` overrides the shared and ST parameters (tests only).
    """
    params = {**PARAMETERS, **(params or {})}
    X = sp.csr_matrix(X)
    _check_raw_counts(X)
    method = clustering_normalization(tech, normalization, area)

    def unit_eligible(summed: sp.csr_matrix) -> np.ndarray:
        """Units with enough counts and detected genes."""
        counts = np.asarray(summed.sum(axis=1)).ravel()
        detected: np.ndarray = np.diff(summed.indptr)
        return (counts >= params["min_counts"]) & (detected >= params["min_genes"])

    def represent(
        summed: sp.csr_matrix, units: SpatialUnits, kept: np.ndarray
    ) -> tuple[sp.csr_matrix, str]:
        """Configured normalization of the summed unit counts, on a log scale."""
        unit_area = None
        if method == StNormalization.AREA_LOG_NORMALIZE:
            spot_area = np.asarray(area, dtype=np.float64)
            unit_area = (
                spot_area
                if units.bin_ids is None
                else np.bincount(
                    units.bin_ids, weights=spot_area, minlength=units.n_units
                )
            )[kept]
        normalized, _ = normalized_matrix(summed, method, unit_area)
        if method == StNormalization.SIZE_FACTORS:
            return _log1p(normalized), f"{method} + log1p"
        return normalized, method

    labels, eligible, info = cluster_spots(
        X,
        coords,
        clustering_preset(DOMAINS, tech.resolution, COUNTS),
        represent,
        feature_names=np.asarray(list(gene_names), dtype=str),
        unit_eligible=unit_eligible,
        params=params,
        reporter=reporter,
    )
    info["resolution"] = tech.resolution
    return labels, eligible, info
