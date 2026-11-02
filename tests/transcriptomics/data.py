"""Synthetic data and shared setup for transcriptomics scenarios."""

import scipy.sparse as sp

import numpy as np


def expression(n: int = 120) -> sp.csr_matrix:
    """Return seeded float32 CSR counts for n spots with two distinct 20-gene marker domains."""
    rng = np.random.default_rng(9)
    means = np.ones((n, 60))
    means[: n // 2, :20] = 15
    means[n // 2 :, 20:40] = 15
    return sp.csr_matrix(rng.poisson(means).astype(np.float32))
