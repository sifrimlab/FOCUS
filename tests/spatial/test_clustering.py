"""Shared alignment clustering (cluster_spots) behavioral scenarios."""

from typing import Any

import numpy as np
import scipy.sparse as sp
from scipy.stats import spearmanr
from sklearn.metrics import adjusted_rand_score
from unittest.mock import patch

from focus.config.options import SpotResolution
from focus.spatial.clustering import cluster_spots
from focus.spatial.settings import (
    DOMAINS,
    FEATURES,
    FOREGROUND,
    GRADIENT,
    clustering_preset,
)
from focus.spatial.units import SpatialUnits
from tests.spatial.data import lattice


def log_median(
    summed: sp.csr_matrix, units: SpatialUnits, kept: np.ndarray
) -> tuple[sp.csr_matrix, str]:
    """Rows divided by their nonzero median, then log1p (an MSI-like representation)."""
    rows = sp.csr_matrix(summed, dtype=np.float32, copy=True)
    for i in range(rows.shape[0]):
        values = rows.data[rows.indptr[i] : rows.indptr[i + 1]]
        if values.size:
            values /= np.median(values)
    np.log1p(rows.data, out=rows.data)
    return rows, "median + log1p"


def two_domains(seed: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A 60 x 60 raster with two column domains in 80 Poisson features."""
    xy, ij = lattice(60, 60, [20, 0], [0, 20], origin=(10, 10))
    truth = (ij[:, 1] >= 30).astype(int)
    means = np.full((len(xy), 80), 5.0)
    means[truth == 0, :15] += 6
    means[truth == 1, 15:30] += 6
    return xy, truth, np.random.default_rng(seed).poisson(means).astype(np.float32)


def preset(mode: str, resolution: str = SpotResolution.STANDARD) -> Any:
    """Clustering preset of ``mode`` with the detected-features signal."""
    return clustering_preset(mode, resolution, FEATURES)


class TestClustering:
    """Behavioral contracts for the shared clustering."""

    def test_domains_are_found_deterministically(self) -> None:
        """Two domains come back from the represented rows, the same on a second run."""
        xy, truth, X = two_domains()
        names = np.array([f"f{i}" for i in range(X.shape[1])])
        labels, eligible, info = cluster_spots(
            X, xy, preset(DOMAINS), log_median, feature_names=names
        )
        again, _, _ = cluster_spots(
            X, xy, preset(DOMAINS), log_median, feature_names=names
        )
        np.testing.assert_array_equal(labels, again)
        assert eligible.all()
        assert adjusted_rand_score(truth, labels) > 0.95
        assert info["n_leiden_clusters"] >= info["n_clusters"] == 2
        assert (
            info["status"] == "complete" and info["representation"] == "median + log1p"
        )
        assert info["units"]["method"] == "native"

    def test_pca_sees_the_representation_never_the_raw_rows(self) -> None:
        """Only the matrix returned by the modality's representation reaches the PCA."""
        import scanpy as sc

        xy, _, X = two_domains()
        seen: list[np.ndarray] = []
        real = sc.pp.pca

        def record(sub: Any, **kw: Any) -> object:
            """Record the PCA input before the real PCA."""
            seen.append(sp.csr_matrix(sub.X).toarray())
            return real(sub, **kw)

        def marked(
            summed: sp.csr_matrix, units: SpatialUnits, kept: np.ndarray
        ) -> tuple[sp.csr_matrix, str]:
            """log_median, recorded."""
            rows, label = log_median(summed, units, kept)
            seen.append(rows.toarray())
            return rows, label

        with patch("scanpy.pp.pca", side_effect=record):
            cluster_spots(
                X, xy, preset(DOMAINS), marked, feature_names=np.arange(80).astype(str)
            )
        np.testing.assert_allclose(seen[1], seen[0], rtol=1e-6)
        assert not np.allclose(seen[1], X)

    def test_excluded_spots_are_unassigned_and_never_summed(self) -> None:
        """Background spots take no part, also inside aggregated blocks."""
        xy, truth, X = two_domains()
        include = np.ones(len(xy), dtype=bool)
        include[::7] = False
        X[~include] = 1000  # background that would dominate any block it entered
        labels, eligible, info = cluster_spots(
            X,
            xy,
            preset(DOMAINS),
            log_median,
            feature_names=np.arange(80).astype(str),
            include=include,
            params={"max_units": 1_000},
        )
        assert info["units"]["method"] == "lattice"
        assert np.all(labels[~include] == "unassigned") and not eligible[~include].any()
        assert adjusted_rand_score(truth[include], labels[include]) > 0.9

    def test_foreground_mode_labels_the_foreground_without_clustering(self) -> None:
        """Microgrid: foreground '0', background 'unassigned', no PCA."""
        xy, _, X = two_domains()
        include = np.zeros(len(xy), dtype=bool)
        include[::50] = True
        with patch("scanpy.pp.pca", side_effect=AssertionError("no clustering")):
            labels, eligible, info = cluster_spots(
                X,
                xy,
                preset(FOREGROUND, SpotResolution.SUB_CELLULAR),
                log_median,
                feature_names=np.arange(80).astype(str),
                include=include,
            )
        np.testing.assert_array_equal(labels[include], "0")
        np.testing.assert_array_equal(labels[~include], "unassigned")
        np.testing.assert_array_equal(eligible, include)
        assert info["status"] == "foreground_only" and info["n_clusters"] == 1
        assert preset(FOREGROUND, SpotResolution.SUB_CELLULAR)["signal"] is None

    def test_gradient_mode_gives_ordered_levels(self) -> None:
        """A smooth gradient becomes ordered quantile levels that follow it."""
        xy, ij = lattice(60, 60, [20, 0], [0, 20], origin=(10, 10))
        means = np.full((len(xy), 60), 8.0)
        means[:, :20] *= np.exp(1.5 * ij[:, 1:2] / 59)
        X = np.random.default_rng(3).poisson(means).astype(np.float32)
        labels, _, info = cluster_spots(
            X, xy, preset(GRADIENT), log_median, feature_names=np.arange(60).astype(str)
        )
        levels = labels.astype(int)
        assert info["n_clusters"] == 8 and info["mode"] == GRADIENT
        assert set(levels) == set(range(8))
        assert abs(spearmanr(levels, ij[:, 1]).statistic) > 0.9
        flipped, _, _ = cluster_spots(
            X[::-1],
            xy,
            preset(GRADIENT),
            log_median,
            feature_names=np.arange(60).astype(str),
        )
        # The level of a spectrum depends on its content, not on the row order or the PC sign.
        assert np.mean(flipped.astype(int)[::-1] == levels) > 0.99

    def test_sub_cellular_grows_blocks_until_enough_features_are_detected(self) -> None:
        """Sparse pixels are summed into blocks; every pixel of a block shares its label."""
        xy, ij = lattice(120, 120, [5, 0], [0, 5], origin=(10, 10))
        truth = (ij[:, 1] >= 60).astype(int)
        means = np.full((len(xy), 300), 0.03)
        means[truth == 0, :40] += 0.06
        means[truth == 1, 40:80] += 0.06
        X = np.random.default_rng(4).poisson(means).astype(np.float32)
        labels, eligible, info = cluster_spots(
            X,
            xy,
            preset(DOMAINS, SpotResolution.SUB_CELLULAR),
            log_median,
            feature_names=np.arange(300).astype(str),
        )
        assert info["units"]["method"] == "lattice"
        assert info["units"]["driver"] == "signal"
        assert info["units"]["k"] > 1
        assert info["parameters"]["signal"] == FEATURES
        assert adjusted_rand_score(truth[eligible], labels[eligible]) > 0.9
