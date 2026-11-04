"""Technology-calibrated ST preprocessing: input contract, cleanup, QC, gene rule, normalization."""

import copy
from typing import cast
import tempfile
from pathlib import Path

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import scanpy as sc
import scipy.sparse as sp

from focus.config.types import PipelineConfig
from focus.config.validation import parse_config
from focus.preprocessing.transcriptomics.counts import remove_controls, resolve_counts
from focus.preprocessing.transcriptomics.dataset import SpatialTranscriptomicDataset
from focus.preprocessing.transcriptomics.normalization import normalize
from focus.preprocessing.transcriptomics.qc import global_outliers, local_outliers
from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic
from focus.runtime.reporting import StepReporter
from tests.spatial.data import lattice


def counts(n: int = 60, genes: int = 40, seed: int = 0) -> sp.csr_matrix:
    """Seeded Poisson counts, every spot non-empty."""
    rng = np.random.default_rng(seed)
    return sp.csr_matrix(rng.poisson(3.0, (n, genes)).astype(np.float32) + 0)


def write_sample(
    root: Path, sample: str, adata: ad.AnnData, modality: str = "ST"
) -> SpatialTranscriptomic:
    """Write ``adata`` as the single input of ``sample`` and return the sample object."""
    folder = root / sample / modality
    folder.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(folder / f"{sample}.h5ad")
    return SpatialTranscriptomic(str(root), sample, modality)


def spatial_adata(X: sp.csr_matrix, genes: list[str] | None = None) -> ad.AnnData:
    """AnnData on a square 10 µm lattice with spot_size 10."""
    n = X.shape[0]
    side = int(np.ceil(np.sqrt(n)))
    xy = np.array([(i % side, i // side) for i in range(n)], dtype=np.float32) * 10
    adata = ad.AnnData(X=X)
    adata.var_names = genes or [f"g{i}" for i in range(X.shape[1])]
    adata.obs_names = [f"s{i}" for i in range(n)]
    adata.obsm["spatial"] = xy
    adata.uns["spot_size"] = 10.0
    return adata


class TestInput:
    """The input contract: one file, spot_size, raw counts."""

    def test_counts_come_from_the_first_integer_matrix_and_other_layers_drop(
        self,
    ) -> None:
        """A log-normalized .X falls back to layers['counts']; no input layer survives."""
        raw = counts()
        adata = spatial_adata(raw.copy())
        adata.layers["counts"] = raw.copy()
        adata.layers["raw"] = (raw * 7).astype(np.float32)
        logged = ad.AnnData(X=raw.copy())
        sc.pp.normalize_total(logged)
        sc.pp.log1p(logged)
        adata.X = logged.X
        result = resolve_counts(adata, "s")
        assert (result.X != raw).nnz == 0
        assert not result.layers and result.raw is None

    def test_no_count_matrix_raises(self) -> None:
        """Non-integer data with no count layer is refused."""
        adata = spatial_adata(sp.csr_matrix(np.full((4, 3), 0.5, np.float32)))
        with pytest.raises(ValueError, match="no raw count matrix"):
            resolve_counts(adata, "s")

    def test_spot_size_is_required_and_one_input_file_is_allowed(self) -> None:
        """A missing spot_size and two input files raise ValueError."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adata = spatial_adata(counts())
            del adata.uns["spot_size"]
            sample = write_sample(root, "a", adata)
            with pytest.raises(ValueError, match="spot_size"):
                sample.load_data()
            adata.uns["spot_size"] = 10.0
            adata.write_h5ad(root / "a" / "ST" / "second.h5ad")
            with pytest.raises(ValueError, match="keep exactly one"):
                sample.load_data()

    def test_controls_are_recorded_then_removed(self) -> None:
        """Probe and codeword features leave the panel; their share stays in obs."""
        X = sp.csr_matrix(np.array([[4, 0, 1], [2, 2, 0]], dtype=np.float32))
        adata = spatial_adata(X, ["Gene1", "NegControlProbe_1", "BLANK_0001"])
        result = remove_controls(adata, StepReporter())
        assert list(result.var_names) == ["Gene1"]
        np.testing.assert_allclose(result.obs["pct_counts_control"], [20.0, 50.0])

    def test_controls_are_kept_when_removal_is_off(self) -> None:
        """With drop=False the panel is untouched and the control share is still recorded."""
        X = sp.csr_matrix(np.array([[4, 0, 1], [2, 2, 0]], dtype=np.float32))
        adata = spatial_adata(X, ["Gene1", "NegControlProbe_1", "BLANK_0001"])
        result = remove_controls(adata, StepReporter(), drop=False)
        assert list(result.var_names) == ["Gene1", "NegControlProbe_1", "BLANK_0001"]
        np.testing.assert_allclose(result.obs["pct_counts_control"], [20.0, 50.0])


class TestQualityControl:
    """Local outliers keep low-depth domains; global outliers do not."""

    def test_local_outliers_spare_a_low_depth_domain(self) -> None:
        """Planted low spots inside a normal domain are flagged; a whole low domain is not."""
        xy, ij = lattice(40, 40, [10, 0], [0, 10])
        # A low-depth domain covering 15% of the section, 10 times below the rest.
        low_domain = ij[:, 1] >= 34
        library = np.where(low_domain, 200.0, 2000.0)
        library *= np.random.default_rng(1).lognormal(0, 0.1, library.size)
        planted = np.flatnonzero((ij[:, 1] == 10) & (ij[:, 0] % 8 == 0))
        library[planted] = 5.0
        local = local_outliers(library, xy, "lower")
        assert local[planted].all()
        # A 3-MAD cutoff flags about 0.1 to 0.3% of spots under noise alone, never a domain.
        assert local[low_domain].mean() < 0.01
        assert global_outliers(library)[low_domain].mean() > 0.9

    def test_dataset_removes_planted_spots_and_reports_them(self) -> None:
        """Spot units drop local outliers; bins keep them (structural cleanup only)."""
        rng = np.random.default_rng(3)
        X = rng.poisson(5.0, (400, 30)).astype(np.float32)
        X[[10, 55, 230]] = 0
        X[[10, 55, 230], 0] = 1
        X[[3, 4]] = 0
        for unit, expected in (("spot", 395), ("bin", 398)):
            with tempfile.TemporaryDirectory() as directory:
                sample = write_sample(
                    Path(directory), "a", spatial_adata(sp.csr_matrix(X))
                )
                path = sample.preprocess_data(unit=unit)
                output = ad.read_h5ad(path)
                assert output.n_obs == expected
                assert output.uns["focus_qc"]["removed"]["zero_counts"] == 2

    def test_controls_and_empty_cells_are_kept_when_removal_is_off(self) -> None:
        """remove_controls_and_empty=False keeps probe features and zero-count cells end to end."""
        rng = np.random.default_rng(3)
        X = rng.poisson(5.0, (60, 6)).astype(np.float32)
        X[[3, 4]] = 0
        names = ["g0", "g1", "g2", "g3", "NegControlProbe_1", "BLANK_0001"]
        for remove, n_obs, n_vars in ((True, 58, 4), (False, 60, 6)):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                sample = write_sample(root, "a", spatial_adata(sp.csr_matrix(X), names))
                files = SpatialTranscriptomicDataset(
                    str(root), [sample]
                ).process_dataset(
                    capture="imaging",
                    unit="cell",
                    quality_control=False,
                    remove_controls_and_empty=remove,
                )
                merged = ad.read_h5ad(files["merged"])
            assert (merged.n_obs, merged.n_vars) == (n_obs, n_vars)


class TestMergedDataset:
    """Gene rule, union panel and the normalization catalog on the merged dataset."""

    def build(
        self, root: Path, capture: str, unit: str, normalization: str
    ) -> ad.AnnData:
        """Two samples with different panels; returns the merged AnnData."""
        rng = np.random.default_rng(4)
        samples = []
        for index, genes in enumerate((["a", "b", "rare"], ["a", "b", "other"])):
            X = rng.poisson(4.0, (100, 3)).astype(np.float32)
            X[:, 2] = 0
            X[: 25 if index == 0 else 5, 2] = 1
            adata = spatial_adata(sp.csr_matrix(X), genes)
            adata.obs["cell_area"] = rng.uniform(20, 80, 100)
            samples.append(write_sample(root, f"s{index}", adata))
        dataset = SpatialTranscriptomicDataset(str(root), samples)
        files = dataset.process_dataset(
            capture=capture, unit=unit, normalization=normalization
        )
        return ad.read_h5ad(files["merged"])

    def test_sequencing_gene_rule_and_log_normalize(self) -> None:
        """A gene detected in 25 spots of one sample stays; one detected in 5 spots goes."""
        with tempfile.TemporaryDirectory() as directory:
            merged = self.build(Path(directory), "sequencing", "bin", "auto")
        assert sorted(merged.var_names) == ["a", "b", "rare"]
        assert merged.X.dtype == np.float32
        raw = merged.layers["raw"].toarray()
        library = raw.sum(axis=1)
        median = np.median(library[library > 0])
        expected = np.log1p(raw / (library / median)[:, None])
        np.testing.assert_allclose(merged.X.toarray(), expected, rtol=1e-5)
        assert merged.uns["focus_normalization"]["median_library_size"] == median

    def test_imaging_keeps_every_detected_gene_and_uses_cell_area(self) -> None:
        """Designed panels keep rare genes; auto normalization divides by the cell area."""
        with tempfile.TemporaryDirectory() as directory:
            merged = self.build(Path(directory), "imaging", "cell", "auto")
        assert sorted(merged.var_names) == ["a", "b", "other", "rare"]
        area = merged.obs["focus_area"].to_numpy(dtype=np.float64)
        expected = np.log1p(
            merged.layers["raw"].toarray() / (area / np.median(area))[:, None]
        )
        np.testing.assert_allclose(merged.X.toarray(), expected, rtol=1e-5)
        assert merged.uns["focus_normalization"]["method"] == "area_log_normalize"

    def test_cp10k_log_matches_scanpy_and_none_keeps_counts(self) -> None:
        """cp10k_log is normalize_total(1e4) + log1p; none leaves raw counts and no layer."""
        with tempfile.TemporaryDirectory() as directory:
            merged = self.build(Path(directory), "sequencing", "spot", "cp10k_log")
        reference = ad.AnnData(X=merged.layers["raw"].copy())
        sc.pp.normalize_total(reference, target_sum=1e4)
        sc.pp.log1p(reference)
        np.testing.assert_allclose(merged.X.toarray(), reference.X.toarray(), rtol=1e-6)
        with tempfile.TemporaryDirectory() as directory:
            plain = self.build(Path(directory), "sequencing", "spot", "none")
        assert "raw" not in plain.layers
        assert np.all(plain.X.data == np.floor(plain.X.data))

    def test_area_normalization_without_area_raises_and_auto_falls_back(self) -> None:
        """Explicit area normalization needs an area; auto falls back to log_normalize."""
        adata = ad.AnnData(X=counts(10, 4))
        with pytest.raises(ValueError, match="area"):
            normalize(adata, "area_log_normalize")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = spatial_adata(counts(30, 5))
            sample = write_sample(root, "s0", source)
            files = SpatialTranscriptomicDataset(str(root), [sample]).process_dataset(
                capture="imaging", unit="cell"
            )
            merged = ad.read_h5ad(files["merged"])
        assert merged.uns["focus_normalization"]["method"] == "log_normalize"

    def test_mitochondrial_share_survives_removal(self) -> None:
        """pct_counts_mt is measured before removal and kept in the merged obs."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            X = counts(50, 4)
            sample = write_sample(
                root, "s0", spatial_adata(X, ["mt-Co1", "Actb", "Gapdh", "Sox2"])
            )
            files = SpatialTranscriptomicDataset(str(root), [sample]).process_dataset(
                unit="bin", remove_mitochondrial_genes=True
            )
            merged = ad.read_h5ad(files["merged"])
        assert "mt-Co1" not in merged.var_names
        expected = 100 * X[:, 0].toarray().ravel() / np.asarray(X.sum(axis=1)).ravel()
        np.testing.assert_allclose(merged.obs["pct_counts_mt"], expected, rtol=1e-5)
        assert not merged.obs.isna().any().any()


def test_removed_st_settings_raise_and_name_their_replacement() -> None:
    """Old numeric settings are rejected with the setting that replaces them."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "s1", "rna").mkdir(parents=True)
        config: PipelineConfig = {
            "dataset_path": root,
            "reference_modality": "rna",
            "perform_alignment": False,
            "perform_registration": False,
            "modalities": [
                {
                    "name": "rna",
                    "type": "st",
                    "alignment_strategy": "manual",
                    "registration_type": "none",
                    "processing_settings": {"capture": "imaging", "unit": "cell"},
                    "registration_settings": {},
                }
            ],
        }
        parse_config(copy.deepcopy(config), require_raw_inputs=False)
        settings = cast(
            dict[str, object], config["modalities"][0]["processing_settings"]
        )
        settings["total_counts_normalize"] = True
        with pytest.raises(
            ValueError, match="'total_counts_normalize' \\(use 'normalization'\\)"
        ):
            parse_config(copy.deepcopy(config), require_raw_inputs=False)
        del settings["total_counts_normalize"]
        settings["unit"] = "pixel"
        with pytest.raises(ValueError, match="unit must be one of"):
            parse_config(config, require_raw_inputs=False)


def test_merged_obs_carries_no_index_collisions() -> None:
    """Samples sharing barcodes stay distinct through the sample prefix."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        samples = [
            write_sample(root, s, spatial_adata(counts(40, 6, i)))
            for i, s in enumerate(("a", "b"))
        ]
        files = SpatialTranscriptomicDataset(str(root), samples).process_dataset(
            unit="bin"
        )
        merged = ad.read_h5ad(files["merged"])
    assert merged.obs_names.is_unique
    assert isinstance(merged.obs["sample_id"].dtype, pd.CategoricalDtype)
