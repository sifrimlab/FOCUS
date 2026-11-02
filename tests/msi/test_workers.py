"""MSI worker importability, serialization, intensity sums, and dtype contracts."""

import numpy as np
import pytest
from joblib import Parallel, delayed

from focus.preprocessing.msi.interpolation import interpolate_spectra


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_loky_interpolation_serializes_moved_function(
    dtype: type[np.float32] | type[np.float64],
) -> None:
    """Import the relocated worker in separate processes and retain exact bin sums."""
    mz = [np.array([200.0, 100.0001, 100.0, 300.0], dtype=dtype)]
    intensities = [np.array([7, 2, 3, 11], dtype=dtype)]
    references = np.array([100, 200], dtype=dtype)
    results: list[np.ndarray] = Parallel(n_jobs=2, backend="loky")(
        delayed(interpolate_spectra)(mz, intensities, references, 10) for _ in range(2)
    )
    for actual in results:
        np.testing.assert_array_equal(
            actual, np.array([[5, 7]], dtype=dtype), strict=True
        )
