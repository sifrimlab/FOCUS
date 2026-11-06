"""Compile coverage filter: spots without full coverage in a modality are dropped."""

import anndata as ad
import numpy as np
import scipy.sparse

from focus.pipeline.assembly import _compute_valid_spot_mask


def test_empty_and_partly_nan_rows_are_uncovered() -> None:
    """All-zero rows, all-NaN rows and rows with any NaN feature leave the dataset."""
    raman = np.array(
        [
            [0.2, 0.8, 0.5],  # covered
            [0.0, 0.0, 0.0],  # empty
            [np.nan, np.nan, np.nan],  # nothing measured
            [0.3, 0.7, np.nan],  # one acquisition missing
            [0.0, 0.0, np.nan],  # empty and partly missing
        ],
        dtype=np.float32,
    )
    st = scipy.sparse.csr_matrix(np.ones((5, 2), dtype=np.float32))
    mods = {
        "MSI": ad.AnnData(np.ones((5, 2), dtype=np.float32)),
        "Raman": ad.AnnData(raman),
        "ST": ad.AnnData(st),
    }
    mask = _compute_valid_spot_mask(mods, "MSI", 5)
    assert mask is not None
    assert mask.tolist() == [True, False, False, False, False]


def test_fully_covered_modalities_need_no_filter() -> None:
    """No mask when every spot is covered everywhere."""
    mods = {
        "MSI": ad.AnnData(np.ones((3, 2), dtype=np.float32)),
        "Raman": ad.AnnData(np.full((3, 2), 0.5, dtype=np.float32)),
    }
    assert _compute_valid_spot_mask(mods, "MSI", 3) is None
