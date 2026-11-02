"""Typed adapters for supported SciPy constructor overloads missing from its stubs."""

from typing import Protocol, cast

import scipy.sparse as sp
import numpy as np
from numpy.typing import DTypeLike


class SparseStorage(Protocol):
    """Common sparse attributes omitted from the stub's base matrix class."""

    format: str
    shape: tuple[int, int]
    dtype: np.dtype

    def tocsr(self) -> sp.csr_matrix:
        """Return SciPy's CSR conversion with its normal copy semantics."""
        ...


class ShapeConstructor(Protocol):
    """The documented empty-CSR constructor overload."""

    def __call__(self, shape: tuple[int, int], *, dtype: DTypeLike) -> sp.csr_matrix:
        """Create an empty matrix with the supplied shape and dtype."""
        ...


class SparseConstructor(Protocol):
    """The existing sparse-to-CSR constructor overload."""

    def __call__(self, matrix: sp.spmatrix) -> sp.csr_matrix:
        """Apply SciPy's normal constructor conversion to an existing sparse matrix."""
        ...


def empty_csr(shape: tuple[int, int], *, dtype: DTypeLike) -> sp.csr_matrix:
    """Construct the same zero CSR matrix through SciPy's shape-only overload."""
    return cast(ShapeConstructor, sp.csr_matrix)(shape, dtype=dtype)


def sparse_csr(matrix: sp.spmatrix) -> sp.csr_matrix:
    """Convert a sparse matrix using the unchanged SciPy constructor."""
    return cast(SparseConstructor, sp.csr_matrix)(matrix)
