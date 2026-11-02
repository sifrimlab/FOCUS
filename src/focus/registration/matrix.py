"""Convert a registration matrix product to its original dense representation."""

import numpy as np
import scipy.sparse


def _dense(matrix: np.ndarray | scipy.sparse.csr_matrix) -> np.ndarray:
    """Return dense feature values, converting sparse inputs with their existing dtype."""
    return (
        np.asarray(matrix.todense())
        if scipy.sparse.issparse(matrix)
        else np.asarray(matrix)
    )
