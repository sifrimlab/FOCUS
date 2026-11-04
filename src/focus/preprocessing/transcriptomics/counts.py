"""Resolve the raw count matrix of an input AnnData and remove non-gene control features."""

from typing import cast

import anndata as ad
import numpy as np
import scipy.sparse as sp

from focus.preprocessing.transcriptomics.technology import (
    CONTROL_PREFIXES,
    GENE_FEATURE_TYPE,
)
from focus.runtime.reporting import StepReporter

# Chunk of stored values checked at a time, so large matrices need no full-size temporaries.
_CHECK_CHUNK = 50_000_000


def _as_csr(matrix: object) -> sp.csr_matrix:
    """CSR view or copy of a dense or sparse matrix, dtype preserved."""
    if sp.issparse(matrix):
        return cast(sp.csr_matrix, cast(sp.spmatrix, matrix).tocsr())
    return sp.csr_matrix(np.asarray(matrix))


def is_count_matrix(matrix: sp.csr_matrix) -> bool:
    """True when every stored value is finite, non-negative and integer-valued."""
    data = matrix.data
    for start in range(0, data.size, _CHECK_CHUNK):
        chunk = data[start : start + _CHECK_CHUNK]
        if (
            not np.all(np.isfinite(chunk))
            or np.any(chunk < 0)
            or np.any(chunk != np.floor(chunk))
        ):
            return False
    return True


def resolve_counts(adata: ad.AnnData, sample_id: str) -> ad.AnnData:
    """
    Return ``adata`` with its raw counts in ``.X`` and no other matrix.

    The counts are the first integer-valued, non-negative matrix among ``.X``,
    ``layers['counts']``, ``layers['raw']`` and ``.raw.X`` (when ``.raw`` has the same genes).
    Every input layer and ``.raw`` is dropped, so no input matrix can later be taken for
    the counts.

    Raises
    ------
    ValueError
        When none of the candidates holds raw counts.
    """
    candidates: list[tuple[str, object]] = [(".X", adata.X)]
    for key in ("counts", "raw"):
        if key in adata.layers:
            candidates.append((f"layers['{key}']", adata.layers[key]))
    if adata.raw is not None and adata.raw.var_names.equals(adata.var_names):
        candidates.append((".raw.X", adata.raw.X))
    for _, matrix in candidates:
        counts = _as_csr(matrix)
        if is_count_matrix(counts):
            result = ad.AnnData(
                X=counts,
                obs=adata.obs.copy(),
                var=adata.var.copy(),
                obsm={key: value for key, value in adata.obsm.items()},
                uns=dict(adata.uns),
            )
            return result
    names = ", ".join(name for name, _ in candidates)
    raise ValueError(
        f"Sample {sample_id}: no raw count matrix found (checked {names}). "
        "ST preprocessing needs non-negative integer counts."
    )


def control_features(adata: ad.AnnData) -> np.ndarray:
    """Boolean mask of the non-gene features: probes, codewords and other controls."""
    names = adata.var_names.astype(str)
    mask = np.zeros(adata.n_vars, dtype=bool)
    for prefix in CONTROL_PREFIXES:
        mask |= names.str.startswith(prefix)
    if "feature_types" in adata.var:
        mask |= (adata.var["feature_types"].astype(str) != GENE_FEATURE_TYPE).to_numpy()
    return mask


def remove_controls(
    adata: ad.AnnData, reporter: StepReporter, drop: bool = True
) -> ad.AnnData:
    """Record each spot's control counts in ``obs``, then drop the control features if ``drop``.

    ``obs['total_counts_control']`` and ``obs['pct_counts_control']`` (percent of all counts,
    controls included) are written for every input, 0 when the panel has no controls.
    """
    controls = control_features(adata)
    X = cast(sp.csr_matrix, adata.X)
    total = np.asarray(X.sum(axis=1)).ravel()
    control_total = (
        np.asarray(X[:, controls].sum(axis=1)).ravel()
        if controls.any()
        else np.zeros(adata.n_obs)
    )
    adata.obs["total_counts_control"] = control_total.astype(np.float32)
    adata.obs["pct_counts_control"] = np.divide(
        100.0 * control_total,
        total,
        out=np.zeros(adata.n_obs),
        where=total > 0,
    ).astype(np.float32)
    if not drop or not controls.any():
        return adata
    reporter.detail(f"Removed {int(controls.sum())} control features")
    return adata[:, ~controls].copy()
