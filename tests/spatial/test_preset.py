"""Preset behavioral scenarios."""

from typing import Any
import anndata as ad
from focus.config.types import PipelineConfig
from typing import cast

import pytest
import copy
import tempfile
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score, homogeneity_score

from focus.config.options import SpotResolution
from focus.spatial.settings import clustering_preset
from focus.spatial.units import plan_spatial_units
from focus.spatial.clustering import compute_cluster_labels
from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.config.validation import parse_config
from tests.spatial.data import lattice


class TestPreset:
    """Behavioral contracts for preset."""

    def test_unknown_resolution_is_rejected(self) -> None:
        """Verify unknown resolution is rejected."""
        with pytest.raises(ValueError):
            clustering_preset("ultra")
        with pytest.raises(ValueError):
            spot_cluster_labels(
                sp.csr_matrix(np.ones((20, 3))), list("abc"), np.zeros((20, 2)), "ultra"
            )
        with tempfile.TemporaryDirectory() as root:
            Path(root, "s1", "ST").mkdir(parents=True)
            config: PipelineConfig = {
                "dataset_path": root,
                "reference_modality": "ST",
                "perform_alignment": False,
                "perform_registration": False,
                "modalities": [
                    {
                        "name": "ST",
                        "type": "st",
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
            sp.csr_matrix(X), genes, xy, SpotResolution.SUB_CELLULAR
        )
        units = plan_spatial_units(xy, counts=X.sum(axis=1), min_median_counts=1000)
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
            sp.csr_matrix(X), genes, xy, SpotResolution.STANDARD
        )
        assert standard["units"]["method"] == "native"

    def test_msi_raster_is_clustered_on_blocks_with_smoothing(self) -> None:
        """Verify msi raster is clustered on blocks with smoothing."""
        xy, ij = lattice(100, 100, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(6)
        X = rng.uniform(0, 1, (len(xy), 30))
        X[ij[:, 0] < 50, :10] += 3
        X /= X.sum(axis=1, keepdims=True)
        labels = compute_cluster_labels(
            sp.csr_matrix(X),
            leiden_resolution=0.5,
            normalize_target_sum=None,
            coordinates=xy,
            smoothing_rings=1,
            cap=5_000,
        )
        units = plan_spatial_units(xy, cap=5_000)
        assert units.info["k"] == 2
        for unit in range(0, units.n_units, 7):
            assert len(set(labels[units.bin_ids == unit])) == 1
        assert adjusted_rand_score(ij[:, 0] < 50, labels) > 0.9

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

        with patch("scanpy.pp.pca", side_effect=record_pca):
            labels = compute_cluster_labels(
                X, leiden_resolution=0.5, normalize_target_sum=None, coordinates=xy
            )
        assert seen[0] <= N_TOP_FEATURES + 1
        # No label mixes the two domains (the rows along their edge, whose neighbourhood straddles
        # it, may form their own cluster).
        assert homogeneity_score(ij[:, 0] < 15, labels) > 0.99

    def test_msi_clustering_ignores_the_intensity_scale_and_a_few_intense_ions(
        self,
    ) -> None:
        """Verify msi clustering ignores the intensity scale and a few intense ions."""
        xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(8)
        X = rng.poisson(20, (len(xy), 200)).astype(np.float64)
        X[ij[:, 0] < 20, 10:60] *= 2.0  # composition: half the features double
        X[:, :3] = rng.uniform(
            1e4, 1e5, (len(xy), 3)
        )  # three intense ions varying at random
        labels = compute_cluster_labels(
            sp.csr_matrix(X),
            leiden_resolution=0.5,
            normalize_target_sum=None,
            coordinates=xy,
            smoothing_rings=1,
        )
        assert homogeneity_score(ij[:, 0] < 20, labels) > 0.99
        # The halves are found whatever the scale; the noise-driven sub-clusters within them may differ.
        scaled = compute_cluster_labels(
            sp.csr_matrix(X * 1e-4),
            leiden_resolution=0.5,
            normalize_target_sum=None,
            coordinates=xy,
            smoothing_rings=1,
        )
        assert homogeneity_score(ij[:, 0] < 20, scaled) > 0.99
