"""Preset behavioral scenarios: how the modality presets drive the shared clustering."""

from typing import Any
import anndata as ad
from focus.config.types import PipelineConfig
from typing import cast

import pytest
import copy
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score, homogeneity_score

from focus.config.options import MsiSampleType, SpotResolution
from focus.config.validation import parse_config
from focus.preprocessing.msi.normalization import normalize_intensities
from focus.preprocessing.msi.sample_data import label_sample
from focus.preprocessing.msi.technology import sample_profile
from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.preprocessing.transcriptomics.technology import technology
from focus.spatial.settings import (
    COUNTS,
    DOMAINS,
    FEATURES,
    FOREGROUND,
    GRADIENT,
    SIGNAL_TARGETS,
    clustering_preset,
)
from focus.spatial.units import plan_spatial_units, summed_signal
from tests.spatial.data import lattice


def msi_sample(X: np.ndarray, xy: np.ndarray, normalization: str = "tic") -> ad.AnnData:
    """An MSI-like per-sample AnnData: normalized .X, raw layer and coordinates."""
    raw = sp.csr_matrix(X, dtype=np.float32)
    adata = ad.AnnData(X=normalize_intensities(raw, normalization), layers={"raw": raw})
    adata.var_names = [f"mz{i}" for i in range(X.shape[1])]
    adata.obsm["spatial"] = xy.astype(np.float32)
    return adata


class TestPreset:
    """Behavioral contracts for preset."""

    def test_unknown_resolution_is_rejected(self) -> None:
        """Verify unknown resolution is rejected."""
        with pytest.raises(ValueError):
            clustering_preset(DOMAINS, "ultra", COUNTS)
        with tempfile.TemporaryDirectory() as root:
            Path(root, "s1", "MSI").mkdir(parents=True)
            config: PipelineConfig = {
                "dataset_path": root,
                "reference_modality": "MSI",
                "perform_alignment": False,
                "perform_registration": False,
                "modalities": [
                    {
                        "name": "MSI",
                        "type": "msi",
                        "alignment_strategy": "manual",
                        "registration_type": "none",
                        "processing_settings": {
                            "resolution": SpotResolution.SUB_CELLULAR
                        },
                        "registration_settings": {},
                    }
                ],
            }
            parse_config(copy.deepcopy(config), require_raw_inputs=False)
            config["modalities"][0]["processing_settings"]["resolution"] = "ultra"
            with pytest.raises(ValueError, match="resolution must be one of"):
                parse_config(config, require_raw_inputs=False)

    def test_modality_presets_set_the_mode_and_the_aggregation(self) -> None:
        """ST units and MSI sample types map onto the clustering modes and signals."""
        assert technology("sequencing", "spot").resolution == SpotResolution.STANDARD
        assert technology("sequencing", "bin").resolution == SpotResolution.SUB_CELLULAR
        assert technology("imaging", "cell").resolution == SpotResolution.SUB_CELLULAR
        assert clustering_preset(DOMAINS, SpotResolution.STANDARD, COUNTS) == {
            "mode": DOMAINS,
            "signal": None,
            "signal_target": None,
        }
        assert clustering_preset(DOMAINS, SpotResolution.SUB_CELLULAR, COUNTS) == {
            "mode": DOMAINS,
            "signal": COUNTS,
            "signal_target": SIGNAL_TARGETS[COUNTS],
        }
        modes = {t: sample_profile(t).clustering_mode for t in MsiSampleType.list()}
        assert modes == {
            MsiSampleType.TISSUE: DOMAINS,
            MsiSampleType.TISSUE_NOISY: DOMAINS,
            MsiSampleType.CELL_CULTURE: GRADIENT,
            MsiSampleType.MICROGRID: FOREGROUND,
        }
        culture = clustering_preset(GRADIENT, SpotResolution.SUB_CELLULAR, FEATURES)
        assert culture["signal"] == FEATURES
        grid = clustering_preset(FOREGROUND, SpotResolution.SUB_CELLULAR, FEATURES)
        assert grid["signal"] is None

    def test_sub_cellular_coarsens_sparse_data_and_labels_reach_every_spot(
        self,
    ) -> None:
        """Verify sub cellular coarsens sparse data and labels reach every spot."""
        xy, ij = lattice(240, 240, [2, 0], [0, 2])
        right = ij[:, 1] >= 120
        means = np.full((len(xy), 60), 0.05)
        means[~right, :20] = 0.6
        means[right, 20:40] = 0.6
        X = np.random.default_rng(5).poisson(means).astype(np.float32)
        X[(ij[:, 0] < 30) & (ij[:, 1] < 30)] = 0
        genes = [f"g{i}" for i in range(60)]
        labels, eligible, info = spot_cluster_labels(
            sp.csr_matrix(X), genes, xy, technology("sequencing", "bin")
        )
        units = plan_spatial_units(
            xy, unit_signal=summed_signal(X.sum(axis=1)), signal_target=1000
        )
        assert info["units"]["method"] == "lattice"
        assert info["units"]["k"] == units.info["k"]
        assert info["resolution"] == SpotResolution.SUB_CELLULAR
        assert len(labels) == len(xy)
        unit_counts = np.bincount(
            cast(np.ndarray, units.bin_ids), weights=X.sum(axis=1)
        )
        empty = unit_counts[units.bin_ids] == 0
        assert empty.any()
        assert np.all(labels[empty] == "unassigned")
        assert not eligible[empty].any()
        # Blocks straddling the domain boundary hold both domains; on the pure ones, no label
        # may mix the two domains (partial edge blocks may still form their own cluster).
        share = np.bincount(
            cast(np.ndarray, units.bin_ids), weights=right
        ) / np.bincount(cast(np.ndarray, units.bin_ids))
        pure = eligible & np.isin(share, (0, 1))[units.bin_ids]
        assert homogeneity_score(right[pure], labels[pure]) > 0.99
        for unit in np.unique(cast(np.ndarray, units.bin_ids))[:300]:
            assert len(set(labels[units.bin_ids == unit])) == 1
        _, _, standard = spot_cluster_labels(
            sp.csr_matrix(X), genes, xy, technology("sequencing", "spot")
        )
        assert standard["units"]["method"] == "native"

    def test_msi_labels_ignore_the_intensity_scale_and_a_few_intense_ions(
        self,
    ) -> None:
        """Labels follow the configured normalization at any intensity scale.

        The median (and the fallback for none) also resists a few intense ions; TIC and RMS let
        them dominate the labels as they dominate the normalized matrix.
        """
        xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(8)
        X = rng.poisson(20, (len(xy), 200)).astype(np.float64)
        X[ij[:, 0] < 20, 10:60] *= 2.0  # composition: half the features double
        intense = X.copy()
        intense[:, :3] = rng.uniform(
            1e4, 1e5, (len(xy), 3)
        )  # three intense ions at random
        truth = ij[:, 0] < 20
        profile = sample_profile(MsiSampleType.TISSUE)
        everywhere = np.ones(len(xy), bool)

        def labels_of(data: np.ndarray, normalization: str) -> np.ndarray:
            """Labels of ``data`` normalized with ``normalization``."""
            adata = msi_sample(data, xy, normalization)
            label_sample(adata, everywhere, normalization, "standard", profile)
            return adata.obs["cluster"].astype(str).to_numpy()

        for scale in (1.0, 1e-4):
            for normalization in ("none", "tic", "median", "rms"):
                labels = labels_of(X * scale, normalization)
                assert adjusted_rand_score(truth, labels) > 0.95, normalization
            for normalization in ("none", "median"):
                labels = labels_of(intense * scale, normalization)
                assert adjusted_rand_score(truth, labels) > 0.95, normalization
        none = msi_sample(X, xy, "none")
        label_sample(none, everywhere, "none", "standard", profile)
        assert none.uns["focus_clustering"]["representation"].startswith("median")
        rms = msi_sample(X, xy, "rms")
        label_sample(rms, everywhere, "rms", "standard", profile)
        assert rms.uns["focus_clustering"]["representation"] == "rms + log1p"

    def test_msi_background_is_unassigned_and_provenance_is_stored(self) -> None:
        """Background spectra take no part in the clustering; colours follow the categories."""
        xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(9)
        X = rng.poisson(20, (len(xy), 100)).astype(np.float64)
        X[ij[:, 1] < 20, :30] *= 3
        foreground = ij[:, 0] < 30
        X[~foreground] = rng.poisson(1, (int((~foreground).sum()), 100))
        adata = msi_sample(X, xy)
        label_sample(adata, foreground, "tic", "standard", sample_profile("tissue"))
        labels = adata.obs["cluster"].astype(str).to_numpy()
        assert np.all(labels[~foreground] == "unassigned")
        np.testing.assert_array_equal(adata.obs["clustering_eligible"], foreground)
        assert adjusted_rand_score(ij[foreground, 1] < 20, labels[foreground]) > 0.95
        categories = list(adata.obs["cluster"].cat.categories)
        assert categories[-1] == "unassigned"
        assert len(adata.uns["cluster_colors"]) == len(categories)
        info = adata.uns["focus_clustering"]
        assert (info["mode"], info["sample_type"], info["status"]) == (
            DOMAINS,
            "tissue",
            "complete",
        )
        assert info["n_eligible_spots"] == int(foreground.sum())

    def test_msi_microgrid_and_culture_follow_their_modes(self) -> None:
        """Microgrid: foreground only; cell culture: ordered gradient levels."""
        xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(10)
        X = rng.poisson(10, (len(xy), 100)).astype(np.float64)
        X[:, :30] *= np.exp(2.0 * ij[:, 1:2] / 39)
        cells = rng.random(len(xy)) < 0.05
        grid = msi_sample(X, xy)
        label_sample(grid, cells, "tic", "sub_cellular", sample_profile("microgrid"))
        labels = grid.obs["cluster"].astype(str).to_numpy()
        np.testing.assert_array_equal(labels[cells], "0")
        np.testing.assert_array_equal(labels[~cells], "unassigned")
        assert grid.uns["focus_clustering"]["status"] == "foreground_only"
        culture = msi_sample(X, xy)
        everywhere = np.ones(len(xy), bool)
        label_sample(
            culture, everywhere, "tic", "standard", sample_profile("cell_culture")
        )
        levels = culture.obs["cluster"].astype(str).to_numpy().astype(int)
        assert culture.uns["focus_clustering"]["mode"] == GRADIENT
        assert sorted(set(levels)) == list(range(8))
        means = pd.Series(ij[:, 1]).groupby(levels).mean().to_numpy()
        assert np.all(np.diff(means) > 0) or np.all(np.diff(means) < 0)

    def test_msi_clustering_keeps_the_top_features_like_st(self) -> None:
        """Verify msi clustering keeps the top features like st."""
        import scanpy as sc
        from unittest.mock import patch
        from focus.spatial.settings import N_TOP_FEATURES

        xy, ij = lattice(30, 30, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(7)
        X = rng.uniform(0, 1, (len(xy), N_TOP_FEATURES + 500))
        X[ij[:, 0] < 15, :20] += 3
        seen = []
        real_pca = sc.pp.pca

        def record_pca(sub: ad.AnnData, **kw: Any) -> object:
            """Record the selected feature count before delegating to the original Scanpy PCA operation."""
            seen.append(sub.n_vars)
            return real_pca(sub, **kw)

        adata = msi_sample(X, xy)
        with patch("scanpy.pp.pca", side_effect=record_pca):
            label_sample(
                adata,
                np.ones(len(xy), bool),
                "tic",
                "standard",
                sample_profile("tissue"),
            )
        assert seen[0] <= N_TOP_FEATURES + 1
        labels = adata.obs["cluster"].astype(str).to_numpy()
        assert homogeneity_score(ij[:, 0] < 15, labels) > 0.99
