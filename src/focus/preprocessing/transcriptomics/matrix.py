"""Ensure native and layer expression matrices use the existing CSR storage representation."""

from focus.spatial.matrices import SparseStorage
from typing import cast

import anndata as ad
import scipy.sparse as sp


def _ensure_sparse_csr(adata: ad.AnnData) -> None:
    """
    Guarantee .X and every layer are stored as CSR sparse matrices, in place and
    without changing dtype. No-op (no copy) for elements already in CSR format.

    This is the defensive guarantee that preprocessed spatial-transcriptomics output
    is always written as a sparse matrix, regardless of upstream scanpy operations or
    the sparse format (CSC/COO) of the input file.
    """
    if not sp.issparse(adata.X):
        adata.X = sp.csr_matrix(adata.X)
    elif cast(SparseStorage, adata.X).format != "csr":
        adata.X = cast(SparseStorage, adata.X).tocsr()

    for key in list(adata.layers.keys()):
        layer = adata.layers[key]
        if not sp.issparse(layer):
            adata.layers[key] = sp.csr_matrix(layer)
        elif cast(SparseStorage, layer).format != "csr":
            adata.layers[key] = cast(SparseStorage, layer).tocsr()
