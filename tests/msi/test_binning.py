"""Gaussian binning behavioral scenarios."""

import numpy as np

from focus.preprocessing.msi.binning import bin_spectra_csr, bin_spectrum
from focus.preprocessing.msi.matching import _flatten_spectra


def reference_binning(
    mz: np.ndarray,
    intensity: np.ndarray,
    features: np.ndarray,
    sigma: np.ndarray,
    tolerance: float,
) -> np.ndarray:
    """Dense, unoptimized form of the Gaussian responsibilities."""
    row = np.zeros(features.size)
    for m, value in zip(mz, intensity):
        d = (m - features) / features * 1e6
        weight = np.where(np.abs(d) <= tolerance, np.exp(-0.5 * (d / sigma) ** 2), 0.0)
        if weight.sum() > 0:
            row += value * weight / weight.sum()
    return row


class TestBinning:
    """Behavioral contracts for the Gaussian binning."""

    def test_centred_peak_counts_fully(self) -> None:
        """A peak at the feature m/z gives its whole intensity to the feature."""
        row = bin_spectrum(
            np.array([700.0]), np.array([10.0]), np.array([700.0]), 2.0, 10
        )
        np.testing.assert_allclose(row, [10.0], rtol=1e-6)

    def test_isolated_feature_takes_every_peak_in_reach_whole(self) -> None:
        """A feature alone in reach takes the whole peak whatever its mass error: no attenuation."""
        for shift in (0.0, 2.0, 5.0, 9.5):
            row = bin_spectrum(
                np.array([700.0 * (1 + shift * 1e-6)]),
                np.array([10.0]),
                np.array([700.0]),
                1.0,
                10,
            )
            np.testing.assert_allclose(row, [10.0], rtol=1e-6)

    def test_close_features_share_a_peak_smoothly(self) -> None:
        """Between two features the share moves continuously with the peak's position."""
        features = np.array([700.0, 700.0 * (1 + 6e-6)])
        shares = [
            bin_spectrum(
                np.array([700.0 * (1 + shift * 1e-6)]),
                np.array([1.0]),
                features,
                3.0,
                10,
            )[0]
            for shift in np.arange(0.0, 6.01, 0.5)
        ]
        assert np.all(np.diff(shares) < 0)
        assert np.all(np.abs(np.diff(shares)) < 0.2)
        np.testing.assert_allclose(shares[6], 0.5, rtol=1e-4)

    def test_peak_out_of_reach_is_dropped(self) -> None:
        """A peak further than the tolerance from every feature is not binned."""
        row = bin_spectrum(
            np.array([700.0 * (1 + 12e-6)]),
            np.array([10.0]),
            np.array([700.0]),
            2.0,
            10,
        )
        np.testing.assert_array_equal(row, [0.0])

    def test_peak_between_features_is_shared_and_counted_once(self) -> None:
        """Two overlapping features share a peak in proportion to their weights; the total is conserved."""
        features = np.array([700.0, 700.0 * (1 + 2e-6)])
        middle = np.array([700.0 * (1 + 1e-6)])
        row = bin_spectrum(middle, np.array([10.0]), features, 4.0, 10)
        np.testing.assert_allclose(row[0], row[1], rtol=1e-4)
        np.testing.assert_allclose(row.sum(), 10.0, rtol=1e-5)

    def test_csr_rows_match_the_dense_reference(self) -> None:
        """The CSR kernel equals the dense formula on random spectra, columns sorted."""
        rng = np.random.default_rng(0)
        features = np.sort(rng.uniform(400, 900, 60))
        sigma = rng.uniform(0.5, 4.0, features.size)
        mz_list, intensity_list = [], []
        for _ in range(50):
            chosen = rng.choice(features, 20) * (1 + rng.normal(0, 3, 20) * 1e-6)
            mz = np.sort(np.concatenate([chosen, rng.uniform(400, 900, 30)]))
            mz_list.append(mz)
            intensity_list.append(rng.exponential(100, mz.size).astype(np.float32))
        flat_mz, flat_intensity, offsets = _flatten_spectra(mz_list, intensity_list)
        indptr, indices, data = bin_spectra_csr(
            flat_mz, flat_intensity, offsets, features, sigma, 10.0
        )
        assert data.dtype == np.float32
        for s, (mz, intensity) in enumerate(zip(mz_list, intensity_list)):
            columns = indices[indptr[s] : indptr[s + 1]]
            assert np.all(np.diff(columns) > 0)
            dense = np.zeros(features.size)
            dense[columns] = data[indptr[s] : indptr[s + 1]]
            np.testing.assert_allclose(
                dense,
                reference_binning(
                    mz, intensity.astype(np.float64), features, sigma, 10
                ),
                rtol=1e-5,
                atol=1e-6,
            )
