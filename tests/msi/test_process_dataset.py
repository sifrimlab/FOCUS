"""ProcessDataset behavioral scenarios."""

from typing import Unpack, TypedDict

import os
import tempfile

import anndata as ad
import numpy as np

from focus.config.options import MsiIntensityNormalization, MsiIonMode
from focus.preprocessing.msi.dataset import MsiDataset
from focus.preprocessing.msi.sample import MsiSample
from tests.msi.data import CALIBRANTS, ANALYTE, synthetic_sample, write_imzml


class RunSettings(TypedDict, total=False):
    """Optional settings exercised by the dataset fixtures."""

    intensity_normalization: str
    internal_standard_mz: dict[str, np.ndarray | list[float]] | str | None


def reference_offsets(
    stored: dict[str, dict[str, np.ndarray]], species: np.ndarray, sample_id: str
) -> dict[float, float]:
    """Return each matched calibrant mass and its stored reference offset in ppm for one sample."""
    references = np.asarray(stored[sample_id]["pos"])
    nearest = species[np.abs(references[:, None] - species[None, :]).argmin(axis=1)]
    return dict(zip(np.round(nearest, 4), (references - nearest) / nearest * 1e6))


class TestProcessDataset:
    """End to end on two synthetic samples with different drift (a sample-level batch)."""

    DRIFTS = {"A": (0.0, 30.0), "B": (10.0, 40.0)}

    def write_dataset(self, root: str) -> None:
        """Write two positive-mode synthetic MSI samples with their specified line-drift profiles."""
        for seed, (sample_id, drift) in enumerate(self.DRIFTS.items()):
            mz_list, intensity_list, coords = synthetic_sample(
                line_drift=drift, seed=seed
            )
            write_imzml(
                os.path.join(root, sample_id, "MSI", "pos"),
                mz_list,
                intensity_list,
                coords,
            )

    def process(self, root: str, **settings: Unpack[RunSettings]) -> dict[str, str]:
        """Construct MSI samples, preprocess them with the supplied settings and return output paths."""
        samples = [
            MsiSample(root, sample_id, "MSI", ion_mode=MsiIonMode.POSITIVE)
            for sample_id in self.DRIFTS
        ]
        return MsiDataset(root, samples).process_dataset(mass_tolerance=10, **settings)

    def test_recalibrated_dataset_has_no_stripes_and_is_cached(self) -> None:
        """Verify recalibrated dataset has no stripes and is cached."""
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)

            def run() -> dict[str, str]:
                """Preprocess the fixture dataset with median intensity normalization and the existing defaults."""
                return self.process(
                    root, intensity_normalization=MsiIntensityNormalization.MEDIAN
                )

            outputs = run()
            merged = ad.read_h5ad(outputs["merged"])
            assert "focus_preprocessing_fingerprint" in merged.uns
            assert "recalibration_ppm_pos" in merged.obs
            assert np.all(np.isfinite(merged.obs["recalibration_ppm_pos"]))

            # One backbone feature per species, the highest m/z included, and no noise feature;
            # every recalibration reference is a real species.
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            species = np.concatenate([CALIBRANTS, [ANALYTE]])
            assert mz.size == species.size
            for mass in species:
                assert int(np.sum(np.abs(mz - mass) / mass * 1e6 <= 100)) == 1, (
                    f"m/z {mass}"
                )
            # Each sample has its own references, shifted onto the samples' common mass scale.
            stored = merged.uns["recalibration_reference"]
            assert sorted(stored) == ["A", "B"]
            for sample_id in stored:
                for reference in stored[sample_id]["pos"]:
                    assert np.min(np.abs(species - reference) / species * 1e6) < 50

            a, b = (
                reference_offsets(stored, species, "A"),
                reference_offsets(stored, species, "B"),
            )
            assert len(set(a) & set(b)) >= 3
            assert abs(np.mean([a[m] - b[m] for m in set(a) & set(b)])) < 1.0

            # The analyte lands in exactly one feature, in every spectrum of both samples.
            nearby = np.flatnonzero(np.abs(mz - ANALYTE) / ANALYTE * 1e6 <= 100)
            raw = merged.layers["raw"][:, nearby].toarray()
            assert int((raw.sum(axis=0) > 0).sum()) == 1
            assert np.all(raw.sum(axis=1) > 0)

            # Unchanged inputs and settings: the fingerprint matches and nothing is rewritten.
            sample_file = outputs["A"]
            written = os.path.getmtime(sample_file)
            run()
            assert os.path.getmtime(sample_file) == written

    def test_internal_standard_anchors_mass_scale_and_normalizes(self) -> None:
        """Verify internal standard anchors mass scale and normalizes."""
        species = np.concatenate([CALIBRANTS, [ANALYTE]])
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)

            # One standard anchors the offset: exact at the standard, the drift's m/z slope remains elsewhere.
            outputs = self.process(
                root,
                intensity_normalization=MsiIntensityNormalization.INTERNAL_STANDARD,
                internal_standard_mz=f"pos: {ANALYTE}",
            )
            merged = ad.read_h5ad(outputs["merged"])
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            standard = int(np.argmin(np.abs(mz - ANALYTE)))
            assert abs(mz[standard] - ANALYTE) / ANALYTE * 1e6 < 1.0
            np.testing.assert_allclose(
                merged.X[:, standard].toarray().ravel(), 1.0, rtol=1e-5
            )

            # Two standards spanning the range anchor offset and slope: every feature on the true scale.
            outputs = self.process(
                root,
                intensity_normalization=MsiIntensityNormalization.INTERNAL_STANDARD,
                internal_standard_mz={"pos": [ANALYTE, CALIBRANTS[0]]},
            )
            merged = ad.read_h5ad(outputs["merged"])
            mz = merged.var["mz"].to_numpy(dtype=np.float64)
            for mass in species:
                assert np.min(np.abs(mz - mass) / mass * 1e6) < 1.0, f"m/z {mass}"
            columns = [
                int(np.argmin(np.abs(mz - mass))) for mass in (ANALYTE, CALIBRANTS[0])
            ]
            np.testing.assert_allclose(
                merged.X[:, columns].toarray().sum(axis=1), 1.0, rtol=1e-5
            )
