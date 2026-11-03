"""ST normalization catalog, applied once to the merged raw counts with one global scale."""

from typing import cast

from anndata import AnnData
import numpy as np
import scanpy as sc
import scipy.sparse as sp

from focus.config.options import StNormalization
from focus.preprocessing.transcriptomics.technology import AREA_KEY
from focus.runtime.reporting import StepReporter

# Target library size of cp10k_log (Seurat LogNormalize, the scanpy convention).
_CP10K_TARGET = 1e4


def _scale_rows(X: sp.csr_matrix, factors: np.ndarray) -> sp.csr_matrix:
    """Divide row ``i`` by ``factors[i]`` (a zero factor leaves its all-zero row unchanged)."""
    inverse = np.divide(
        1.0, factors, out=np.zeros_like(factors, dtype=np.float64), where=factors > 0
    )
    scaled = cast(sp.csr_matrix, sp.diags(inverse.astype(np.float32)) @ X)
    return cast(sp.csr_matrix, scaled.tocsr())


def _median_scaled(values: np.ndarray) -> tuple[np.ndarray, float]:
    """``values`` divided by their median over the positive entries, and that median."""
    positive = values[values > 0]
    median = float(np.median(positive)) if positive.size else 1.0
    return values / median, median


def _log1p(X: sp.csr_matrix) -> sp.csr_matrix:
    """``log1p`` of the stored values in place (zeros stay zero)."""
    np.log1p(X.data, out=X.data)
    return X


def normalize(
    adata: AnnData, method: str, reporter: StepReporter | None = None
) -> dict[str, object]:
    """
    Normalize ``adata.X`` in place with ``method`` and return its provenance.

    Every method but ``none`` keeps the counts in ``layers['raw']`` and writes a float32
    ``.X``. Library sizes are taken over the whole merged matrix, so all samples share one scale
    (see :func:`normalized_matrix`).
    """
    if method not in StNormalization.list() or method == StNormalization.AUTO:
        raise ValueError(f"Unsupported ST normalization {method!r}")
    if method == StNormalization.NONE:
        return {"method": method}
    counts = cast(sp.csr_matrix, adata.X)
    area = (
        adata.obs[AREA_KEY].to_numpy(dtype=np.float64)
        if AREA_KEY in adata.obs
        else None
    )
    X, provenance = normalized_matrix(counts, method, area)
    adata.layers["raw"] = counts.copy()
    adata.X = X
    if reporter:
        reporter.detail(f"Normalized with {method}")
    return provenance


def normalized_matrix(
    counts: sp.csr_matrix, method: str, area: np.ndarray | None = None
) -> tuple[sp.csr_matrix, dict[str, object]]:
    """
    Float32 normalized copy of ``counts`` and its provenance; ``counts`` is not modified.

    - ``log_normalize``: ``log1p(counts / s)``, ``s`` = library size / median library size;
    - ``cp10k_log``: ``log1p(10^4 * counts / library size)``;
    - ``size_factors``: ``counts / s`` (linear);
    - ``area_log_normalize``: ``log1p(counts / a)``, ``a`` = ``area`` / its median.

    Raises
    ------
    ValueError
        For an unknown method, or ``area_log_normalize`` without a finite positive area for
        every row.
    """
    if method not in StNormalization.list() or method in (
        StNormalization.AUTO,
        StNormalization.NONE,
    ):
        raise ValueError(f"Unsupported ST normalization {method!r}")
    provenance: dict[str, object] = {"method": method}
    X = counts.astype(np.float32)
    library = np.asarray(X.sum(axis=1)).ravel().astype(np.float64)

    if method == StNormalization.CP10K_LOG:
        normalized = AnnData(X=X)
        sc.pp.normalize_total(normalized, target_sum=_CP10K_TARGET, inplace=True)
        sc.pp.log1p(normalized)
        X = cast(sp.csr_matrix, normalized.X).astype(np.float32)
        provenance["target_sum"] = _CP10K_TARGET
    elif method == StNormalization.AREA_LOG_NORMALIZE:
        if area is None:
            raise ValueError("area_log_normalize needs a spot or cell area")
        if not np.all(np.isfinite(area) & (area > 0)):
            raise ValueError(
                "area_log_normalize needs a finite positive area for every spot or cell"
            )
        factors, median = _median_scaled(area)
        X = _log1p(_scale_rows(X, factors))
        provenance["median_area"] = median
    else:
        factors, median = _median_scaled(library)
        X = _scale_rows(X, factors)
        if method == StNormalization.LOG_NORMALIZE:
            X = _log1p(X)
        provenance["median_library_size"] = median
    return sp.csr_matrix(X, dtype=np.float32), provenance
