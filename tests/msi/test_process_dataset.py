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


def assert_merged_clustering(merged: ad.AnnData, representation: str) -> None:
    """Per-sample alignment labels: provenance per sample, colours of the merged categories."""
    assert sorted(merged.uns["focus_clustering"]) == ["A", "B"]
    for sample_id in ("A", "B"):
        info = merged.uns["focus_clustering"][sample_id]
        assert info["representation"] == representation
        assert info["mode"] == "domains"
    categories = list(merged.obs["cluster"].cat.categories)
    assert len(merged.uns["cluster_colors"]) == len(categories)
    assert merged.obs["clustering_eligible"].dtype == bool


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
            assert_merged_clustering(merged, "median + log1p")
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

    def test_resumed_run_equals_a_fresh_run(self) -> None:
        """An interrupted run resumes the remaining samples with the outputs of a fresh run."""
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)
            outputs = self.process(
                root, intensity_normalization=MsiIntensityNormalization.TIC
            )
            fresh = ad.read_h5ad(outputs["B"])
            os.remove(outputs["B"])
            os.remove(outputs["merged"])
            outputs = self.process(
                root, intensity_normalization=MsiIntensityNormalization.TIC
            )
            resumed = ad.read_h5ad(outputs["B"])
            np.testing.assert_array_equal(
                resumed.obs["recalibration_ppm_pos"], fresh.obs["recalibration_ppm_pos"]
            )
            np.testing.assert_array_equal(
                resumed.obs["foreground"], fresh.obs["foreground"]
            )
            np.testing.assert_array_equal(resumed.X.toarray(), fresh.X.toarray())
            np.testing.assert_array_equal(
                resumed.uns["recalibration_reference"]["pos"],
                fresh.uns["recalibration_reference"]["pos"],
            )


class TestMicrogridDataset:
    """End to end on a microgrid with drift: recalibration on the matrix ions, then the cells."""

    CELLS = {(8, 8), (8, 30), (20, 18), (31, 5), (33, 33), (14, 36)}
    CELL_IONS = np.array([705.5011, 750.6022, 780.7033])

    def write(self, root: str) -> None:
        """A 40 x 40 slide of matrix ions drifting 0 to 30 ppm down the lines, with one-pixel cells."""
        rng = np.random.default_rng(0)
        n = 40
        mz_list, intensity_list, coords = [], [], []
        for line in range(n):
            for col in range(n):
                drift = 30.0 * line / (n - 1)
                masses = CALIBRANTS.copy()
                values = rng.lognormal(np.log(1e4), 0.2, masses.size)
                if (line, col) in self.CELLS:
                    masses = np.concatenate([masses, self.CELL_IONS])
                    values = np.concatenate([values, np.full(3, 4e4)])
                peaks = masses * (1 + (drift + rng.normal(0, 0.5, masses.size)) * 1e-6)
                noise = rng.uniform(350, 1000, 40)
                mz = np.concatenate([peaks, noise])
                order = np.argsort(mz)
                mz_list.append(mz[order])
                intensity_list.append(
                    np.concatenate([values, rng.exponential(100, noise.size)])[
                        order
                    ].astype(np.float32)
                )
                coords.append((line, col))
        write_imzml(
            os.path.join(root, "G", "MSI", "pos"),
            mz_list,
            intensity_list,
            np.array(coords, dtype=np.int32),
        )

    def test_one_pixel_cells_are_found_after_recalibration(self) -> None:
        """Every cell is foreground, nothing else is, and the cell ions form backbone features."""
        with tempfile.TemporaryDirectory() as root:
            self.write(root)
            sample = MsiSample(root, "G", "MSI", ion_mode=MsiIonMode.POSITIVE)
            outputs = MsiDataset(root, [sample]).process_dataset(
                mass_tolerance=10, sample_type="microgrid"
            )
            adata = ad.read_h5ad(outputs["G"])
            # Pixel (line, col) is spectrum line * 40 + col.
            foreground = np.flatnonzero(adata.obs["foreground"].astype(bool).to_numpy())
            assert {(int(i) // 40, int(i) % 40) for i in foreground} == self.CELLS
            # Alignment labels: the cells, without clustering.
            labels = adata.obs["cluster"].astype(str).to_numpy()
            assert set(labels[foreground]) == {"0"}
            assert set(np.delete(labels, foreground)) == {"unassigned"}
            assert adata.uns["focus_clustering"]["status"] == "foreground_only"
            # Without internal standards the sample keeps its own mass scale (its mean drift): the
            # cell ions sit where the calibrants do.
            mz = adata.var["mz"].to_numpy(dtype=np.float64)

            def offset(mass: float) -> float:
                """ppm offset of the backbone feature nearest `mass`."""
                nearest = mz[np.argmin(np.abs(mz - mass))]
                return float((nearest - mass) / mass * 1e6)

            scale = np.median([offset(mass) for mass in CALIBRANTS])
            for mass in self.CELL_IONS:
                assert abs(offset(mass) - scale) < 3.0, mass
            # Recalibrated on the matrix ions: the drift is removed from every line.
            assert np.ptp(adata.obs["recalibration_ppm_pos"]) > 25
