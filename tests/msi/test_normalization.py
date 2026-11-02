"""Normalization behavioral scenarios."""

import pytest
from tests.assertions import assert_close

import numpy as np

from focus.config.options import MsiIntensityNormalization
from focus.preprocessing.msi.normalization import normalize_intensities


class TestNormalization:
    """Behavioral contracts for normalization."""

    coords = np.array([(0, 0), (0, 1), (0, 2), (1, 1)])

    def test_median_uses_nonzero_features(self) -> None:
        """Verify median uses nonzero features."""
        raw = np.array([[0.0, 2.0, 4.0, 0.0], [0.0, 0.0, 0.0, 0.0]])
        out = normalize_intensities(raw, MsiIntensityNormalization.MEDIAN)
        np.testing.assert_allclose(
            out, [[0.0, 2 / 3, 4 / 3, 0.0], [0.0, 0.0, 0.0, 0.0]]
        )

    def test_internal_standard_ratio_and_neighbour_fill(self) -> None:
        """Verify internal standard ratio and neighbour fill."""
        raw = np.array([[4.0, 2.0], [6.0, 3.0], [8.0, 4.0], [5.0, 0.0]])
        out = normalize_intensities(
            raw, MsiIntensityNormalization.INTERNAL_STANDARD, self.coords, np.array([1])
        )
        np.testing.assert_allclose(out[:3, 0], [2.0, 2.0, 2.0])
        # Spectrum 3 has no standard: divisor = median of its neighbours' divisors (2, 3, 4).
        assert_close(out[3, 0], 5.0 / 3.0)

    def test_every_method_keeps_the_input_dtype(self) -> None:
        # A float64 result would double the memory of the dense per-sample matrix.
        """Verify every method keeps the input dtype."""
        raw = np.random.default_rng(0).uniform(0, 10, (4, 3)).astype(np.float32)
        for method in MsiIntensityNormalization.list():
            out = normalize_intensities(raw, method, self.coords, np.array([1]))
            assert out.dtype == np.float32, method

    def test_internal_standard_absent_everywhere_raises(self) -> None:
        """Verify internal standard absent everywhere raises."""
        raw = np.array([[4.0, 0.0], [6.0, 0.0], [8.0, 0.0], [5.0, 0.0]])
        with pytest.raises(ValueError):
            normalize_intensities(
                raw,
                MsiIntensityNormalization.INTERNAL_STANDARD,
                self.coords,
                np.array([1]),
            )
