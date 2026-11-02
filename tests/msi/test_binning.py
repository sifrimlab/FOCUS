"""Binning behavioral scenarios."""

import numpy as np

from focus.preprocessing.msi.binning import interpolate_single


class TestBinning:
    """Behavioral contracts for binning."""

    def test_peak_goes_whole_to_nearest_feature(self) -> None:
        """Verify peak goes whole to nearest feature."""
        features = np.array([700.0, 700.0 * (1 + 15e-6)])
        for shift_ppm in (0.0, 3.0, 6.0):
            peak = np.array([700.0 * (1 + shift_ppm * 1e-6)])
            binned = interpolate_single(peak, np.array([10.0]), features, 10)
            np.testing.assert_array_equal(binned, [10.0, 0.0])

    def test_peak_out_of_reach_is_dropped(self) -> None:
        """Verify peak out of reach is dropped."""
        binned = interpolate_single(
            np.array([700.0 * (1 + 12e-6)]), np.array([10.0]), np.array([700.0]), 10
        )
        np.testing.assert_array_equal(binned, [0.0])
