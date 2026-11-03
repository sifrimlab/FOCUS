"""RecalibrationModel behavioral scenarios."""

from tests.assertions import assert_close

import numpy as np

from focus.preprocessing.msi.recalibration import estimate_recalibration
from focus.preprocessing.msi.binning import bin_spectrum
from focus.preprocessing.msi.matching import sample_offset_ppm
from tests.msi.data import CALIBRANTS, ANALYTE, CENTRE, true_error_ppm, synthetic_sample


class TestRecalibrationModel:
    """Behavioral contracts for recalibration model."""

    def test_recovers_injected_drift_everywhere(self) -> None:
        """Verify recovers injected drift everywhere."""
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols)
        model = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            line_axis=0,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
        )

        assert np.all(np.isfinite(model.offset_ppm))
        for mass in (CALIBRANTS[0], CENTRE, CALIBRANTS[-1]):
            truth = true_error_ppm(coords[:, 0], coords[:, 1], mass, n_lines, n_cols)
            estimate = model.offset_ppm + model.slope_ppm_per_da * (
                mass - model.centre_mz
            )
            assert np.abs(estimate - truth).max() < 1.0, f"at m/z {mass}"

        # Dim lines are matched (the threshold follows each spectrum's own noise); lines without
        # calibrants have no match and are corrected from their neighbours.
        dim, missing = np.isin(coords[:, 0], (10, 11)), np.isin(coords[:, 0], (20, 21))
        assert np.all(model.matches[dim] >= 2)
        assert np.all(model.matches[missing] == 0)
        assert model.summary["lines_estimated"] == n_lines - 2

    def test_clean_spectra_match_every_calibrant(self) -> None:
        # No noise and calibrant intensities over two orders of magnitude: no spectrum-wide intensity
        # rule may drop the weaker calibrants.
        """Verify clean spectra match every calibrant."""
        n_lines, n_cols = 20, 25
        rng = np.random.default_rng(4)
        mz_list, intensity_list, coords = synthetic_sample(
            n_lines, n_cols, dim_lines=(), missing_lines=(), n_noise=0
        )
        level = rng.lognormal(0, 1.5, CALIBRANTS.size + 1)
        intensity_list = [
            (1e4 * level * rng.lognormal(0, 0.2, level.size)).astype(np.float32)
            for _ in mz_list
        ]
        model = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            line_axis=0,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
        )
        assert model.summary["pixels_matched"] > 0.95
        truth = true_error_ppm(coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols)
        estimate = model.offset_ppm + model.slope_ppm_per_da * (
            CENTRE - model.centre_mz
        )
        assert np.abs(estimate - truth).max() < 1.0

    def test_dense_low_intensity_spectra(self) -> None:
        # Like a timsTOF export: thousands of noise centroids per spectrum and calibrants far below
        # any fixed intensity threshold (about 800 counts over a noise median of about 35).
        """Verify dense low intensity spectra."""
        n_lines, n_cols = 20, 25
        rng = np.random.default_rng(1)
        mz_list, intensity_list, coords = synthetic_sample(
            n_lines, n_cols, dim_lines=(), n_noise=3000
        )
        intensity_list = [inten * np.float32(0.008) for inten in intensity_list]
        species = np.concatenate([CALIBRANTS, [ANALYTE]])
        for inten, mz in zip(intensity_list, mz_list):
            noise = (
                np.abs(mz[:, None] - species[None, :]) / species[None, :] * 1e6
            ).min(axis=1) > 100
            inten[noise] = rng.exponential(50, noise.sum())
        model = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            line_axis=0,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
        )
        present = ~np.isin(coords[:, 0], (20, 21))
        assert np.mean(model.matches[present] >= 2) > 0.95
        truth = true_error_ppm(coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols)
        estimate = model.offset_ppm + model.slope_ppm_per_da * (
            CENTRE - model.centre_mz
        )
        assert np.abs(estimate - truth).max() < 1.5

    def test_strong_drift_along_the_row(self) -> None:
        # 30 ppm along each row (e.g. laser decay): wider than the mass tolerance on either side of
        # the line median, so the row ends need the wide pixel-level window.
        """Verify strong drift along the row."""
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(
            n_lines, n_cols, row_ramp=30.0
        )
        model = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            line_axis=0,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
        )
        truth = true_error_ppm(
            coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols, row_ramp=30.0
        )
        estimate = model.offset_ppm + model.slope_ppm_per_da * (
            CENTRE - model.centre_mz
        )
        assert np.abs(estimate - truth).max() < 1.5

    def test_follows_drift_beyond_the_search_window(self) -> None:
        # Down the lines the drift grows to 120 ppm; in the other cases the whole sample sits 80 to
        # 110 and 80 to 200 ppm off: all beyond the +-50 ppm window around the reference masses.
        """Verify follows drift beyond the search window."""
        n_lines, n_cols = 30, 40
        for drift in ((0.0, 120.0), (80.0, 110.0), (80.0, 200.0)):
            mz_list, intensity_list, coords = synthetic_sample(
                n_lines, n_cols, dim_lines=(), missing_lines=(), line_drift=drift
            )
            model = estimate_recalibration(
                mz_list,
                intensity_list,
                coords,
                line_axis=0,
                reference_mz=CALIBRANTS,
                mass_tolerance=10,
            )
            assert model.summary["lines_estimated"] == n_lines, f"drift {drift}"
            truth = true_error_ppm(
                coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols, line_drift=drift
            )
            estimate = model.offset_ppm + model.slope_ppm_per_da * (
                CENTRE - model.centre_mz
            )
            assert np.abs(estimate - truth).max() < 2.0, f"drift {drift}"

    def test_sample_offset_needs_agreement_beyond_chance(self) -> None:
        """Verify sample offset needs agreement beyond chance."""
        rng = np.random.default_rng(3)

        def spectra(present: int) -> tuple[list[np.ndarray], list[np.ndarray]]:
            """Generate centroid spectra with the controlled mass drift used by this recalibration scenario."""
            mz_list, intensity_list = [], []
            for _ in range(2000):
                mz = np.concatenate(
                    [CALIBRANTS[:present] * (1 + 70e-6), rng.uniform(350, 1100, 3000)]
                )
                intensity = np.concatenate(
                    [[800.0] * present, rng.exponential(50, 3000)]
                )
                order = np.argsort(mz)
                mz_list.append(mz[order])
                intensity_list.append(intensity[order].astype(np.float32))
            return mz_list, intensity_list

        # Noise alone, one ion near one reference, or two ions that may agree by chance (about 2% over
        # +-500 ppm) do not move the search; four agreeing calibrants do.
        for present in (0, 1, 2):
            assert sample_offset_ppm(*spectra(present), CALIBRANTS, 500.0, 10) == 0.0, (
                f"{present} calibrants"
            )
        assert_close(
            sample_offset_ppm(*spectra(4), CALIBRANTS, 500.0, 10), 70.0, delta=2.0
        )

    def test_sample_offset_ignores_ions_lining_up_by_chance(self) -> None:
        # The calibrants are in 1% of the spectra; one to four other ions sit within +-480 ppm of every
        # reference, so some of them line up at a common offset by chance. With the calibrants in 60%
        # of the spectra, 120 ppm off, the offset is found.
        """Verify sample offset ignores ions lining up by chance."""
        chemistry = np.random.default_rng(42)
        others = [
            mass * (1 + chemistry.uniform(-480, 480, chemistry.integers(1, 5)) * 1e-6)
            for mass in CALIBRANTS
        ]
        rates = [chemistry.uniform(0.1, 0.6, ions.size) for ions in others]

        def spectra(
            calibrant_rate: float, offset_ppm: float, seed: int
        ) -> tuple[list[np.ndarray], list[np.ndarray]]:
            """Generate centroid spectra with the controlled mass drift used by this recalibration scenario."""
            rng = np.random.default_rng(seed)
            mz_list, intensity_list = [], []
            for _ in range(3000):
                present = rng.random(CALIBRANTS.size) < calibrant_rate
                parts = [
                    rng.uniform(350, 1100, 1000),
                    CALIBRANTS[present] * (1 + offset_ppm * 1e-6),
                ]
                parts += [
                    ions[rng.random(ions.size) < rate]
                    for ions, rate in zip(others, rates)
                ]
                mz = np.concatenate(parts)
                intensity = np.concatenate(
                    [rng.exponential(50, 1000), np.full(mz.size - 1000, 900.0)]
                )
                order = np.argsort(mz)
                mz_list.append(mz[order])
                intensity_list.append(intensity[order].astype(np.float32))
            return mz_list, intensity_list

        for seed in range(3):
            assert (
                sample_offset_ppm(*spectra(0.01, 0.0, seed), CALIBRANTS, 500.0, 10)
                == 0.0
            )
        assert_close(
            sample_offset_ppm(*spectra(0.6, 120.0, 0), CALIBRANTS, 500.0, 10),
            120.0,
            delta=2.0,
        )

    def test_zero_and_one_based_coordinates_agree(self) -> None:
        """Verify zero and one based coordinates agree."""
        mz_list, intensity_list, coords = synthetic_sample(
            10, 12, dim_lines=(), missing_lines=()
        )
        zero = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
            line_axis=0,
        )
        one = estimate_recalibration(
            mz_list,
            intensity_list,
            coords + 1,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
            line_axis=0,
        )
        np.testing.assert_allclose(zero.offset_ppm, one.offset_ppm)

    def test_recalibrated_analyte_bins_into_one_feature(self) -> None:
        """Verify recalibrated analyte bins into one feature."""
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols)
        model = estimate_recalibration(
            mz_list,
            intensity_list,
            coords,
            line_axis=0,
            reference_mz=CALIBRANTS,
            mass_tolerance=10,
        )
        backbone = np.array([ANALYTE])

        def analyte_intensity(spectra: list[np.ndarray]) -> np.ndarray:
            """Interpolate each supplied spectrum onto the single-analyte backbone and return its intensities."""
            return np.array(
                [
                    bin_spectrum(mz, inten, backbone, 10 / 2.3548, 10)[0]
                    for mz, inten in zip(spectra, intensity_list)
                ]
            )

        before = analyte_intensity(mz_list)
        after = analyte_intensity(model.apply(mz_list))
        assert (
            np.mean(before == 0) > 0.3
        )  # without recalibration the drift moves it out of reach
        expected = np.array(
            [
                inten[np.argmin(np.abs(mz - ANALYTE))]
                for mz, inten in zip(mz_list, intensity_list)
            ]
        )
        # No line drops the analyte: an isolated feature takes every peak in reach whole.
        np.testing.assert_allclose(after, expected, rtol=1e-6)

    def test_no_references_is_identity(self) -> None:
        """Verify no references is identity."""
        mz_list, intensity_list, coords = synthetic_sample(
            4, 4, dim_lines=(), missing_lines=()
        )
        model = estimate_recalibration(
            mz_list, intensity_list, coords, 0, np.array([]), 10
        )
        for original, corrected in zip(mz_list, model.apply(mz_list)):
            np.testing.assert_array_equal(original, corrected)
