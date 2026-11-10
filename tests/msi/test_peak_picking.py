"""Peak picking of one profile spectrum: recovery of known peaks, baseline, noise and merging."""

import numpy as np
import pytest

from focus.preprocessing.msi.picking import centroid_spectrum
from tests.msi.profile_data import STEP, grid_for, profile_spectrum


@pytest.mark.parametrize(
    ("shape", "points", "limit"),
    [
        ("gaussian", 3, 0.02),
        ("gaussian", 5, 0.02),
        ("gaussian", 10, 0.03),
        ("lorentzian", 3, 0.03),
        ("lorentzian", 5, 0.03),
        ("lorentzian", 10, 0.03),
        # The apex of a skewed peak is fitted from its two neighbours, which see different flanks.
        ("asymmetric", 3, 0.15),
        ("asymmetric", 5, 0.15),
        ("asymmetric", 10, 0.15),
    ],
)
def test_known_peaks_are_recovered(shape: str, points: int, limit: float) -> None:
    """Every peak is found once, its m/z within `limit` of the full width at half maximum."""
    rng = np.random.default_rng(points)
    peaks = np.arange(320.123, 980.0, 7.31)
    heights = 10 ** rng.uniform(3, 5, peaks.size)
    mz, y = profile_spectrum(peaks, heights, points, shape=shape, rng=rng)
    picked_mz, picked_height = centroid_spectrum(mz, y, grid_for())
    width = points * STEP
    for peak, height in zip(peaks, heights):
        near = np.flatnonzero(np.abs(picked_mz - peak) < width)
        assert near.size == 1
        assert abs(picked_mz[near[0]] - peak) <= limit * width
        if shape == "gaussian":
            assert picked_height[near[0]] == pytest.approx(height, rel=0.05)
    assert np.all(np.diff(picked_mz) > 0)
    assert np.all(picked_height > 0)


def test_baseline_is_removed_and_noise_is_thresholded() -> None:
    """Heights are above the baseline; of the noise maxima only the 3-sigma tail is kept."""
    rng = np.random.default_rng(1)
    peaks = np.array([400.0, 600.0, 800.0])
    mz, y = profile_spectrum(
        peaks, np.full(3, 1e4), 5, baseline=500.0, noise=20.0, rng=rng
    )
    picked_mz, picked_height = centroid_spectrum(mz, y, grid_for())
    for peak in peaks:
        near = np.argmin(np.abs(picked_mz - peak))
        assert picked_height[near] == pytest.approx(1e4, rel=0.03)
    # About one maximum in a thousand points of pure noise exceeds three standard deviations.
    assert picked_mz.size < 0.003 * mz.size
    assert picked_mz.size > peaks.size


def test_noiseless_islands_keep_every_local_maximum() -> None:
    """Without a baseline estimate (zero-trimmed islands) every peak is kept, however small."""
    peaks = np.array([400.0, 500.0, 600.0])
    mz, y = profile_spectrum(
        peaks, np.array([1e5, 10.0, 1.0]), 6, baseline=0.0, noise=0.0, trim=True
    )
    picked_mz, picked_height = centroid_spectrum(mz, y, grid_for())
    assert picked_mz.size == 3
    np.testing.assert_allclose(picked_height, [1e5, 10.0, 1.0], rtol=0.02)


def test_split_top_is_one_peak_and_resolved_doublet_is_two() -> None:
    """Apexes are one peak unless the signal between them drops below half of the lower one."""
    mz = 500.0 + STEP * np.arange(200)
    split = np.zeros(200)
    split[50:57] = [5, 60, 100, 95, 99, 55, 5]
    doublet = np.zeros(200)
    doublet[50:57] = [5, 60, 100, 30, 100, 60, 5]
    one, one_height = centroid_spectrum(mz, split, grid_for())
    two, _ = centroid_spectrum(mz, doublet, grid_for())
    assert one.size == 1
    assert one_height[0] >= 100.0
    assert two.size == 2


def test_input_order_does_not_matter() -> None:
    """Unsorted arrays give the same centroids as sorted ones."""
    rng = np.random.default_rng(2)
    mz, y = profile_spectrum(np.array([450.0, 700.0]), np.full(2, 5e4), 5, rng=rng)
    order = rng.permutation(mz.size)
    sorted_result = centroid_spectrum(mz, y, grid_for())
    shuffled = centroid_spectrum(mz[order], y[order], grid_for())
    np.testing.assert_array_equal(sorted_result[0], shuffled[0])
    np.testing.assert_array_equal(sorted_result[1], shuffled[1])
