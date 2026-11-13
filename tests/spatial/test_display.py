"""Merge tree, spatially coherent features and the display outputs of the clustering."""

from __future__ import annotations

import os
from typing import Any
import struct
import tempfile

import anndata as ad
import numpy as np
import scipy.sparse as sp
from scipy.stats import spearmanr

from focus.alignment.spot_display import read_spot_display
from focus.alignment.spot_payload import pack_spots
from focus.spatial.clustering import cluster_spots
from focus.spatial.clusters import cut_tree, merge_tree
from focus.spatial.display import IMAGES_KEY, SpotDisplay, spot_display, store_display
from focus.spatial.features import SpatialFeatures, rank_spatial_features
from focus.spatial.settings import (
    DOMAINS,
    FEATURES,
    N_IMAGE_FEATURES,
    clustering_preset,
)
from focus.spatial.units import plan_spatial_units
from tests.spatial.data import lattice
from tests.spatial.test_clustering import log_median


class TestMergeTree:
    """Ward merges of touching clusters, through the valley partition."""

    def test_tree_passes_through_the_partition_and_prefers_contact(self) -> None:
        """Merges stay inside partition groups first, and join touching clusters before others."""
        rng = np.random.default_rng(0)
        centres = np.array([[0.0, 0], [0.5, 0], [10, 0], [10.5, 0]])
        codes = np.repeat(np.arange(4), 50)
        embedding = centres[codes] + rng.normal(0, 0.1, (200, 2))
        contact = np.array(
            [[0, 1, 0, 0], [1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0]], dtype=float
        )
        partition = np.array([0, 0, 1, 1])
        merges = merge_tree(embedding, codes, contact, partition)
        assert len(merges) == 3
        assert {frozenset(m) for m in merges[:2]} == {
            frozenset((0, 1)),
            frozenset((2, 3)),
        }
        np.testing.assert_array_equal(
            cut_tree(np.arange(4), merges, 2) == cut_tree(np.arange(4), merges, 2)[0],
            [True, True, False, False],
        )
        assert len(set(cut_tree(np.arange(4), merges, 1))) == 1
        # Without the partition, the two close but non-touching clusters 0 and 3 still wait.
        far = merge_tree(embedding, codes, np.zeros((4, 4)) + contact)
        assert frozenset(far[0]) in {frozenset((0, 1)), frozenset((2, 3))}


def _maps(n: int = 60) -> tuple[np.ndarray, np.ndarray, sp.csr_matrix]:
    """Raster with a coherent gland pattern, a defective scan band, and noise features."""
    xy, ij = lattice(n, n, [10, 0], [0, 10], origin=(5, 5))
    rng = np.random.default_rng(1)
    glands = ((ij[:, 0] // 6 + ij[:, 1] // 6) % 2 == 0).astype(float)
    band = ((ij[:, 0] >= 30) & (ij[:, 0] < 33)).astype(float)
    columns = [3 + 2 * glands + rng.normal(0, 0.3, len(xy)) for _ in range(5)]
    columns += [3 + 3 * band + rng.normal(0, 0.3, len(xy)) for _ in range(5)]
    columns += [3 + rng.normal(0, 0.3, len(xy)) for _ in range(30)]
    X = np.clip(np.stack(columns, axis=1), 0, None)
    return xy, ij, sp.csr_matrix(X)


class TestSpatialFeatures:
    """Coherent features are kept; noise and scan-band artifacts are not."""

    def test_ranking_keeps_the_pattern_and_drops_the_band_and_noise(self) -> None:
        """The gland features rank first, the band features are excluded as line artifacts."""
        xy, _, X = _maps()
        units = plan_spatial_units(xy)
        ok = np.ones(units.n_units, dtype=bool)
        ranking = rank_spatial_features(X, units, ok, n_images=3)
        assert set(ranking.columns[:5].tolist()) == set(range(5))
        assert not set(ranking.columns.tolist()) & set(range(5, 10))
        assert ranking.coherence[0] > 0.5
        # The images are mutually dissimilar: one per pattern, here the glands only.
        assert ranking.image_columns.tolist()[:1] and ranking.image_columns[0] < 5
        assert len(ranking.image_columns) == 1


def _quadrants() -> tuple[np.ndarray, np.ndarray, ad.AnnData, dict[str, Any]]:
    """Four quadrant domains clustered and stored: (xy, labels, adata, info)."""
    xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
    rng = np.random.default_rng(3)
    means = np.full((len(xy), 60), 5.0)
    quadrant = (ij[:, 0] >= 20).astype(int) * 2 + (ij[:, 1] >= 20).astype(int)
    for q in range(4):
        means[quadrant == q, 10 * q : 10 * q + 10] += 6
    X = rng.poisson(means).astype(np.float32)
    labels, _, info = cluster_spots(
        X,
        xy,
        clustering_preset(DOMAINS, "standard", FEATURES),
        log_median,
        feature_names=np.array([f"f{i}" for i in range(X.shape[1])]),
        params={"min_cluster_units": 50},
    )
    adata = ad.AnnData(np.zeros((len(xy), 1), dtype=np.float32))
    adata.obsm["spatial"] = xy.astype(np.float32)
    store_display(adata, labels, info, lambda name: f"label {name}")
    adata.obs["cluster"] = labels
    return xy, labels, adata, dict(info)


class TestDisplayOutputs:
    """cluster_spots returns the tree and images; store_display writes uniform keys."""

    def test_default_level_is_a_cut_of_the_tree(self) -> None:
        """obs['cluster'] is the merge tree cut at the default level, images have fixed width."""
        xy, labels, adata, info = _quadrants()
        assert info["feature_selection"] == "spatial"
        k, level = info["n_fine_clusters"], info["cluster_default_level"]
        assert k >= level == info["n_clusters"]
        assert "display" not in info
        assert adata.obsm[IMAGES_KEY].shape == (len(xy), N_IMAGE_FEATURES)
        assert list(info["image_labels"]) == [
            f"label {n}" for n in info["image_features"]
        ]
        codes = adata.obs["cluster_fine"].astype(str).to_numpy().astype(int)
        cut = cut_tree(codes, [tuple(m) for m in info["cluster_merges"]], level)
        np.testing.assert_array_equal(
            cut[:, None] == cut[None, :], labels[:, None] == labels[None, :]
        )

    def test_stored_outputs_reach_the_alignment_payload(self) -> None:
        """The alignment reader finds the tree and images; FSP v2 carries the images."""
        xy, _, adata, info = _quadrants()
        adata.uns["focus_clustering"] = info
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sample.h5ad")
            adata.write_h5ad(path)
            display = read_spot_display(path)
        assert display is not None
        assert display.default_level == info["cluster_default_level"]
        assert len(display.merges) == info["n_fine_clusters"] - 1
        assert display.images.shape == (len(xy), len(info["image_labels"]))
        packed = pack_spots(
            xy, np.zeros(len(xy)), np.ones(len(xy)), None, display.images
        )
        magic, n, version, m = struct.unpack("<4sIII", packed[:16])
        assert (magic, n, version, m) == (b"FSP1", len(xy), 2, display.images.shape[1])
        assert len(packed) == 16 + n * (17 + m)
        assert len(pack_spots(xy, np.zeros(len(xy)), np.ones(len(xy)))) == 16 + n * 17

    def test_excluded_spots_keep_their_own_feature_values(self) -> None:
        """Feature images ignore the clustering mask: spots left out are still coloured."""
        xy, ij = lattice(40, 40, [20, 0], [0, 20], origin=(10, 10))
        rng = np.random.default_rng(5)
        means = np.full((len(xy), 60), 5.0)
        quadrant = (ij[:, 0] >= 20).astype(int) * 2 + (ij[:, 1] >= 20).astype(int)
        for q in range(4):
            means[quadrant == q, 10 * q : 10 * q + 10] += 6
        X = rng.poisson(means).astype(np.float32)
        include = ij[:, 0] < 30  # a strip of tissue the foreground detection "ate"
        labels, eligible, info = cluster_spots(
            X,
            xy,
            clustering_preset(DOMAINS, "standard", FEATURES),
            log_median,
            feature_names=np.array([f"f{i}" for i in range(X.shape[1])]),
            include=include,
            params={"min_cluster_units": 50},
        )
        assert np.all(labels[~include] == "unassigned")
        display = info["display"]
        assert isinstance(display, SpotDisplay)
        assert display.images.shape[1] >= 1
        assert (display.images > 0).all(), "every spot, excluded or not, has a value"
        units = plan_spatial_units(xy)
        expected = log_median(sp.csr_matrix(X), units, np.arange(len(xy)))[0].toarray()
        for column, name in enumerate(display.image_names):
            truth = expected[:, int(name[1:])]
            rho = spearmanr(display.images[~include, column], truth[~include]).statistic
            assert rho > 0.95, name

    def test_block_units_keep_values_only_for_kept_units(self) -> None:
        """Without per-spot values (aggregated blocks), spots outside the kept units stay at 0."""
        xy = lattice(10, 10, [10, 0], [0, 10], origin=(5, 5))[0]
        n = len(xy)
        kept = np.arange(50)
        eligible = np.arange(n) < 50
        unit_X = sp.csr_matrix(np.linspace(0, 1, 50 * 3).reshape(50, 3))
        ranking = SpatialFeatures(
            np.array([0, 1, 2]), np.ones(3), np.array([1, 2], dtype=np.intp)
        )
        display = spot_display(
            np.array(["0"] * 50),
            unit_X,
            ranking,
            np.array(["a", "b", "c"]),
            (eligible, kept, np.arange(n)),
        )
        assert (display.images[eligible] > 0).all()
        assert (display.images[~eligible] == 0).all()
