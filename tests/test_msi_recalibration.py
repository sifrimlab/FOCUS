"""Run with: PYTHONPATH=src python -m unittest tests.test_msi_recalibration -v."""
import os
import tempfile
import unittest

import anndata as ad
import numpy as np

from focus.constants import MsiIntensityNormalization, MsiIonMode
from focus.preprocessing.lipidomics import (
    MsiDataset, MsiSample, _CalibrationReferenceSelector, _parse_mz_by_mode, estimate_recalibration,
    interpolate_single, normalize_intensities,
)

# Ten isolated calibrants spread over the mass range, plus one analyte that is never a reference.
CALIBRANTS = np.array([401.2512, 455.3021, 512.4150, 568.3307, 624.4789,
                       703.5751, 766.5381, 828.5519, 886.6191, 952.7022])
ANALYTE = 678.5070
CENTRE = 700.0


def true_error_ppm(line, col, mz, n_lines, n_cols, line_drift=(0.0, 30.0), row_ramp=5.0):
    """Injected drift: line drift down the rows, a 0 to `row_ramp` ppm ramp along each row, a TOF-like slope."""
    start, stop = line_drift
    return (start + (stop - start) * line / (n_lines - 1)
            + row_ramp * col / (n_cols - 1)
            + 0.01 * (mz - CENTRE))


def synthetic_sample(n_lines=30, n_cols=40, dim_lines=(10, 11), missing_lines=(20, 21), line_drift=(0.0, 30.0),
                     seed=0, row_ramp=5.0, n_noise=30):
    """
    Drifted centroid spectra on a raster. `dim_lines` lose 98% of every intensity (a weak laser);
    `missing_lines` have no calibrant at all.
    """
    rng = np.random.default_rng(seed)
    mz_list, intensity_list, coords = [], [], []
    for line in range(n_lines):
        for col in range(n_cols):
            true = np.concatenate([CALIBRANTS, [ANALYTE]])
            error = true_error_ppm(line, col, true, n_lines, n_cols, line_drift, row_ramp) + rng.normal(0, 0.3, true.size)
            peaks = true * (1 + error * 1e-6)
            intensity = 1e5 * rng.lognormal(0.0, 0.2, true.size)  # pixel-to-pixel variation for the clustering
            if line in missing_lines:
                peaks, intensity = peaks[-1:], intensity[-1:]     # analyte only
            noise = rng.uniform(300, 1100, n_noise)
            noise = noise[np.abs(noise[:, None] - true[None, :]).min(axis=1) > 200e-6 * noise]
            mz = np.concatenate([peaks, noise])
            intensity = np.concatenate([intensity, rng.exponential(1e3, noise.size)])
            if line in dim_lines:
                intensity = intensity * 0.02
            order = np.argsort(mz)
            mz_list.append(mz[order])
            intensity_list.append(intensity[order].astype(np.float32))
            coords.append((line, col))
    return mz_list, intensity_list, np.array(coords, dtype=np.int32)


class RecalibrationModelTests(unittest.TestCase):
    def test_recovers_injected_drift_everywhere(self):
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols)
        model = estimate_recalibration(mz_list, intensity_list, coords, line_axis=0, reference_mz=CALIBRANTS,
                                       mass_tolerance=10)

        self.assertTrue(np.all(np.isfinite(model.offset_ppm)))
        for mass in (CALIBRANTS[0], CENTRE, CALIBRANTS[-1]):
            truth = true_error_ppm(coords[:, 0], coords[:, 1], mass, n_lines, n_cols)
            estimate = model.offset_ppm + model.slope_ppm_per_da * (mass - model.centre_mz)
            self.assertLess(np.abs(estimate - truth).max(), 1.0, f"at m/z {mass}")

        # Dim lines are matched (the threshold follows each spectrum's own noise); lines without
        # calibrants have no match and are corrected from their neighbours.
        dim, missing = np.isin(coords[:, 0], (10, 11)), np.isin(coords[:, 0], (20, 21))
        self.assertTrue(np.all(model.matches[dim] >= 2))
        self.assertTrue(np.all(model.matches[missing] == 0))
        self.assertEqual(model.summary["lines_estimated"], n_lines - 2)

    def test_dense_low_intensity_spectra(self):
        # Like a timsTOF export: thousands of noise centroids per spectrum and calibrants far below
        # any fixed intensity threshold (about 800 counts over a noise median of about 35).
        n_lines, n_cols = 20, 25
        rng = np.random.default_rng(1)
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols, dim_lines=(), n_noise=3000)
        intensity_list = [inten * np.float32(0.008) for inten in intensity_list]
        species = np.concatenate([CALIBRANTS, [ANALYTE]])
        for inten, mz in zip(intensity_list, mz_list):
            noise = (np.abs(mz[:, None] - species[None, :]) / species[None, :] * 1e6).min(axis=1) > 100
            inten[noise] = rng.exponential(50, noise.sum())
        model = estimate_recalibration(mz_list, intensity_list, coords, line_axis=0, reference_mz=CALIBRANTS,
                                       mass_tolerance=10)
        present = ~np.isin(coords[:, 0], (20, 21))
        self.assertGreater(np.mean(model.matches[present] >= 2), 0.95)
        truth = true_error_ppm(coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols)
        estimate = model.offset_ppm + model.slope_ppm_per_da * (CENTRE - model.centre_mz)
        self.assertLess(np.abs(estimate - truth).max(), 1.5)

    def test_strong_drift_along_the_row(self):
        # 30 ppm along each row (e.g. laser decay): wider than the mass tolerance on either side of
        # the line median, so the row ends need the wide pixel-level window.
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols, row_ramp=30.0)
        model = estimate_recalibration(mz_list, intensity_list, coords, line_axis=0, reference_mz=CALIBRANTS,
                                       mass_tolerance=10)
        truth = true_error_ppm(coords[:, 0], coords[:, 1], CENTRE, n_lines, n_cols, row_ramp=30.0)
        estimate = model.offset_ppm + model.slope_ppm_per_da * (CENTRE - model.centre_mz)
        self.assertLess(np.abs(estimate - truth).max(), 1.5)

    def test_zero_and_one_based_coordinates_agree(self):
        mz_list, intensity_list, coords = synthetic_sample(10, 12, dim_lines=(), missing_lines=())
        kwargs = dict(reference_mz=CALIBRANTS, mass_tolerance=10, line_axis=0)
        zero = estimate_recalibration(mz_list, intensity_list, coords, **kwargs)
        one = estimate_recalibration(mz_list, intensity_list, coords + 1, **kwargs)
        np.testing.assert_allclose(zero.offset_ppm, one.offset_ppm)

    def test_recalibrated_analyte_bins_into_one_feature(self):
        n_lines, n_cols = 30, 40
        mz_list, intensity_list, coords = synthetic_sample(n_lines, n_cols)
        model = estimate_recalibration(mz_list, intensity_list, coords, line_axis=0, reference_mz=CALIBRANTS,
                                       mass_tolerance=10)
        backbone = np.array([ANALYTE])

        def analyte_intensity(spectra):
            return np.array([interpolate_single(mz, inten, backbone, 10)[0]
                             for mz, inten in zip(spectra, intensity_list)])

        before = analyte_intensity(mz_list)
        after = analyte_intensity(model.apply(mz_list))
        self.assertGreater(np.mean(before == 0), 0.3)  # without recalibration the drift moves it out of reach
        expected = np.array([inten[np.argmin(np.abs(mz - ANALYTE))] for mz, inten in zip(mz_list, intensity_list)])
        np.testing.assert_allclose(after, expected)  # no line drops or splits the analyte

    def test_no_references_is_identity(self):
        mz_list, intensity_list, coords = synthetic_sample(4, 4, dim_lines=(), missing_lines=())
        model = estimate_recalibration(mz_list, intensity_list, coords, 0, np.array([]), 10)
        for original, corrected in zip(mz_list, model.apply(mz_list)):
            np.testing.assert_array_equal(original, corrected)


class BinningTests(unittest.TestCase):
    def test_peak_goes_whole_to_nearest_feature(self):
        features = np.array([700.0, 700.0 * (1 + 15e-6)])
        for shift_ppm in (0.0, 3.0, 6.0):
            peak = np.array([700.0 * (1 + shift_ppm * 1e-6)])
            binned = interpolate_single(peak, np.array([10.0]), features, 10)
            np.testing.assert_array_equal(binned, [10.0, 0.0])

    def test_peak_out_of_reach_is_dropped(self):
        binned = interpolate_single(np.array([700.0 * (1 + 12e-6)]), np.array([10.0]), np.array([700.0]), 10)
        np.testing.assert_array_equal(binned, [0.0])


def selector_sample(n_spectra=400, seed=0):
    """Spectra with isolated calibrants, one crowded candidate and one calibrant smeared by drift."""
    rng = np.random.default_rng(seed)
    mz_list, intensity_list = [], []
    for k in range(n_spectra):
        peaks = [400.10, 405.20, 410.30]                      # frequent, low mass
        intensities = [1e5, 1e5, 1e5]
        if k % 2 == 0:
            peaks.append(900.50)                              # less frequent, high mass
            intensities.append(1e5)
        peaks += [700.0, 700.0 * (1 + 20e-6)]                 # crowded: competitor within the window
        intensities += [2e5, 1e5]
        peaks.append(600.0 * (1 + rng.uniform(-15, 15) * 1e-6))  # one calibrant smeared over 30 ppm
        intensities.append(1e5)
        order = np.argsort(peaks)
        mz_list.append(np.array(peaks)[order])
        intensity_list.append(np.array(intensities)[order])
    return mz_list, intensity_list


def dense_selector_sample(n_spectra=600, n_noise=3000, seed=0):
    """Calibrants at about 800 counts among thousands of noise centroids (about 50 counts), drifting 0 to 30 ppm."""
    rng = np.random.default_rng(seed)
    mz_list, intensity_list = [], []
    for k in range(n_spectra):
        drift = 30.0 * k / n_spectra
        calibrants = CALIBRANTS * (1 + (drift + rng.normal(0, 1, CALIBRANTS.size)) * 1e-6)
        mz = np.concatenate([calibrants, rng.uniform(350, 2000, n_noise)])
        intensity = np.concatenate([800 * rng.lognormal(0, 0.3, CALIBRANTS.size), rng.exponential(50, n_noise)])
        order = np.argsort(mz)
        mz_list.append(mz[order])
        intensity_list.append(intensity[order].astype(np.float32))
    return mz_list, intensity_list


class ReferenceSelectorTests(unittest.TestCase):
    def select(self, number_of_references, required=None):
        mz_list, intensity_list = selector_sample()
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample({MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list})
        return selector.select(number_of_references, required_mz=required)[MsiIonMode.POSITIVE]

    @staticmethod
    def near(references, mass, ppm=100):
        return references[np.abs(references - mass) / mass * 1e6 <= ppm]

    def test_dense_noise_selects_every_calibrant_without_bias(self):
        mz_list, intensity_list = dense_selector_sample()
        selector = _CalibrationReferenceSelector(mass_tolerance=10)
        selector.add_sample({MsiIonMode.POSITIVE: mz_list}, {MsiIonMode.POSITIVE: intensity_list})
        references = selector.select(10)[MsiIonMode.POSITIVE]
        self.assertEqual(references.size, CALIBRANTS.size)
        offsets = (references - CALIBRANTS) / CALIBRANTS * 1e6
        # Every reference is a calibrant at the dataset's mean drift (15 ppm), with no extra bias.
        np.testing.assert_allclose(offsets, 15.0, atol=1.0)

    def test_rejects_crowded_candidate(self):
        references = self.select(4)
        self.assertEqual(self.near(references, 700.0).size, 0)

    def test_smeared_calibrant_gives_one_reference_at_its_centre(self):
        references = self.select(6)
        smeared = self.near(references, 600.0)
        self.assertEqual(smeared.size, 1)
        self.assertLess(abs(smeared[0] - 600.0) / 600.0 * 1e6, 2.0)

    def test_references_spread_over_the_mass_range(self):
        references = self.select(2)
        self.assertEqual(self.near(references, 900.50).size, 1)

    def test_required_mass_is_always_selected(self):
        references = self.select(2, required={MsiIonMode.POSITIVE: np.array([700.0])})
        self.assertEqual(self.near(references, 700.0, ppm=30).size, 1)


class NormalizationTests(unittest.TestCase):
    coords = np.array([(0, 0), (0, 1), (0, 2), (1, 1)])

    def test_median_uses_nonzero_features(self):
        raw = np.array([[0.0, 2.0, 4.0, 0.0], [0.0, 0.0, 0.0, 0.0]])
        out = normalize_intensities(raw, MsiIntensityNormalization.MEDIAN)
        np.testing.assert_allclose(out, [[0.0, 2 / 3, 4 / 3, 0.0], [0.0, 0.0, 0.0, 0.0]])

    def test_internal_standard_ratio_and_neighbour_fill(self):
        raw = np.array([[4.0, 2.0], [6.0, 3.0], [8.0, 4.0], [5.0, 0.0]])
        out = normalize_intensities(raw, MsiIntensityNormalization.INTERNAL_STANDARD, self.coords, np.array([1]))
        np.testing.assert_allclose(out[:3, 0], [2.0, 2.0, 2.0])
        # Spectrum 3 has no standard: divisor = median of its neighbours' divisors (2, 3, 4).
        self.assertAlmostEqual(out[3, 0], 5.0 / 3.0)

    def test_internal_standard_absent_everywhere_raises(self):
        raw = np.array([[4.0, 0.0], [6.0, 0.0], [8.0, 0.0], [5.0, 0.0]])
        with self.assertRaises(ValueError):
            normalize_intensities(raw, MsiIntensityNormalization.INTERNAL_STANDARD, self.coords, np.array([1]))


class SettingParserTests(unittest.TestCase):
    def test_forms(self):
        self.assertIsNone(_parse_mz_by_mode(None, "x"))
        self.assertIsNone(_parse_mz_by_mode("  ", "x"))
        parsed = _parse_mz_by_mode("pos: 798.54, 760.58; neg: 885.55", "x")
        np.testing.assert_array_equal(parsed["pos"], [760.58, 798.54])
        np.testing.assert_array_equal(parsed["neg"], [885.55])
        parsed = _parse_mz_by_mode({"POS": [760.58], MsiIonMode.NEGATIVE: np.array([885.55])}, "x")
        self.assertEqual(sorted(parsed), ["neg", "pos"])

    def test_invalid(self):
        for value in ("pos 760.58", {"both": [1.0]}, {"pos": ["abc"]}, {"pos": [-1.0]}, [760.58]):
            with self.assertRaises(ValueError):
                _parse_mz_by_mode(value, "x")


def write_imzml(directory, mz_list, intensity_list, coords, pixel_size=20):
    """Minimal processed-mode imzML + IBD (positions are written 1-based, as the spec requires)."""
    os.makedirs(directory, exist_ok=True)
    entries = []
    with open(os.path.join(directory, "data.ibd"), "wb") as ibd:
        ibd.write(b"\0" * 16)
        for mz, intensity in zip(mz_list, intensity_list):
            mz_offset = ibd.tell()
            ibd.write(mz.astype("<f8").tobytes())
            intensity_offset = ibd.tell()
            ibd.write(intensity.astype("<f4").tobytes())
            entries.append((mz.size, mz_offset, intensity_offset))

    def array(ref, n, size, offset):
        return (f'<binaryDataArray encodedLength="0"><referenceableParamGroupRef ref="{ref}"/>'
                f'<cvParam name="external array length" value="{n}"/>'
                f'<cvParam name="external encoded length" value="{n * size}"/>'
                f'<cvParam name="external offset" value="{offset}"/><binary/></binaryDataArray>')

    spectra = []
    for k, ((line, col), (n, mz_offset, intensity_offset)) in enumerate(zip(coords, entries)):
        spectra.append(
            f'<spectrum id="s{k}" index="{k}" defaultArrayLength="0"><scanList count="1"><scan>'
            f'<cvParam name="position x" value="{col + 1}"/><cvParam name="position y" value="{line + 1}"/>'
            f'</scan></scanList><binaryDataArrayList count="2">'
            f'{array("mzArray", n, 8, mz_offset)}{array("intensities", n, 4, intensity_offset)}'
            f'</binaryDataArrayList></spectrum>')
    xml = ('<?xml version="1.0" encoding="utf-8"?><mzML xmlns="http://psi.hupo.org/ms/mzml" version="1.1">'
           '<referenceableParamGroupList count="2">'
           '<referenceableParamGroup id="mzArray"><cvParam name="64-bit float" value=""/></referenceableParamGroup>'
           '<referenceableParamGroup id="intensities"><cvParam name="32-bit float" value=""/></referenceableParamGroup>'
           '</referenceableParamGroupList><scanSettingsList count="1"><scanSettings id="ss">'
           f'<cvParam name="pixel size x" value="{pixel_size}"/><cvParam name="pixel size y" value="{pixel_size}"/>'
           '<cvParam name="horizontal line scan" value=""/></scanSettings></scanSettingsList>'
           f'<run id="r"><spectrumList count="{len(spectra)}">{"".join(spectra)}</spectrumList></run></mzML>')
    with open(os.path.join(directory, "data.imzML"), "w") as f:
        f.write(xml)


class ProcessDatasetTests(unittest.TestCase):
    """End to end on two synthetic samples with different drift (a sample-level batch)."""
    DRIFTS = {"A": (0.0, 30.0), "B": (10.0, 40.0)}

    def write_dataset(self, root):
        for seed, (sample_id, drift) in enumerate(self.DRIFTS.items()):
            mz_list, intensity_list, coords = synthetic_sample(line_drift=drift, seed=seed)
            write_imzml(os.path.join(root, sample_id, "MSI", "pos"), mz_list, intensity_list, coords)

    def process(self, root, **settings):
        samples = [MsiSample(root, sample_id, "MSI", ion_mode=MsiIonMode.POSITIVE) for sample_id in self.DRIFTS]
        return MsiDataset(root, samples).process_dataset(mass_tolerance=10, **settings)

    def test_recalibrated_dataset_has_no_stripes_and_is_cached(self):
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)

            def run():
                return self.process(root, intensity_normalization=MsiIntensityNormalization.MEDIAN)

            outputs = run()
            merged = ad.read_h5ad(outputs["merged"])
            self.assertIn("focus_preprocessing_fingerprint", merged.uns)
            self.assertIn("recalibration_ppm_pos", merged.obs)
            self.assertTrue(np.all(np.isfinite(merged.obs["recalibration_ppm_pos"])))

            # One backbone feature per species, the highest m/z included, and no noise feature;
            # every recalibration reference is a real species.
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            species = np.concatenate([CALIBRANTS, [ANALYTE]])
            self.assertEqual(mz.size, species.size)
            for mass in species:
                self.assertEqual(int(np.sum(np.abs(mz - mass) / mass * 1e6 <= 100)), 1, f"m/z {mass}")
            for reference in merged.uns["recalibration_reference"]["pos"]:
                self.assertLess(np.min(np.abs(species - reference) / species * 1e6), 50)

            # The analyte lands in exactly one feature, in every spectrum of both samples.
            nearby = np.flatnonzero(np.abs(mz - ANALYTE) / ANALYTE * 1e6 <= 100)
            raw = merged.layers["raw"][:, nearby].toarray()
            self.assertEqual(int((raw.sum(axis=0) > 0).sum()), 1)
            self.assertTrue(np.all(raw.sum(axis=1) > 0))

            # Unchanged inputs and settings: the fingerprint matches and nothing is rewritten.
            sample_file = outputs["A"]
            written = os.path.getmtime(sample_file)
            run()
            self.assertEqual(os.path.getmtime(sample_file), written)

    def test_internal_standard_anchors_mass_scale_and_normalizes(self):
        species = np.concatenate([CALIBRANTS, [ANALYTE]])
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)

            # One standard anchors the offset: exact at the standard, the drift's m/z slope remains elsewhere.
            outputs = self.process(root, intensity_normalization=MsiIntensityNormalization.INTERNAL_STANDARD,
                                   internal_standard_mz=f"pos: {ANALYTE}")
            merged = ad.read_h5ad(outputs["merged"])
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            standard = int(np.argmin(np.abs(mz - ANALYTE)))
            self.assertLess(abs(mz[standard] - ANALYTE) / ANALYTE * 1e6, 1.0)
            np.testing.assert_allclose(merged.X[:, standard].toarray().ravel(), 1.0, rtol=1e-5)

            # Two standards spanning the range anchor offset and slope: every feature on the true scale.
            outputs = self.process(root, intensity_normalization=MsiIntensityNormalization.INTERNAL_STANDARD,
                                   internal_standard_mz={"pos": [ANALYTE, CALIBRANTS[0]]})
            merged = ad.read_h5ad(outputs["merged"])
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            for mass in species:
                self.assertLess(np.min(np.abs(mz - mass) / mass * 1e6), 1.0, f"m/z {mass}")
            columns = [int(np.argmin(np.abs(mz - mass))) for mass in (ANALYTE, CALIBRANTS[0])]
            np.testing.assert_allclose(merged.X[:, columns].toarray().sum(axis=1), 1.0, rtol=1e-5)


if __name__ == "__main__":
    unittest.main()
