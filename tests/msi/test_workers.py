"""MSI worker importability, serialization, intensity sums, and dtype contracts."""

import numpy as np
import pytest
from joblib import Parallel, delayed

from focus.preprocessing.msi.interpolation import interpolate_spectra


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_loky_interpolation_serializes_moved_function(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    """Import the worker in separate processes; isolated features keep their exact intensity sums."""
    mz = [np.array([200.0, 100.0001, 100.0, 300.0], dtype=dtype)]
    intensities = [np.array([7, 2, 3, 11], dtype=dtype)]
    references = np.array([100, 200], dtype=dtype)
    sigma = np.array([4.0, 4.0])
    results: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = Parallel(
        n_jobs=2, backend="loky"
    )(
        delayed(interpolate_spectra)(mz, intensities, references, sigma, 10)
        for _ in range(2)
    )
    for indptr, indices, data in results:
        np.testing.assert_array_equal(indptr, [0, 2])
        np.testing.assert_array_equal(indices, [0, 1])
        assert data.dtype == np.float32
        np.testing.assert_allclose(data, [5, 7], rtol=1e-6)
