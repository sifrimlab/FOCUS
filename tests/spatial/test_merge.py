"""Merge behavioral scenarios."""

from typing import Any
from focus.spatial.types import MergeInfo

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.config.options import SpotResolution
from focus.spatial.settings import _MERGE_VALLEY_RATIO
from focus.spatial.clusters import _valley_ratio, merge_unsupported_clusters
from focus.spatial.clustering import compute_cluster_labels
from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from tests.spatial.data import same_partition, knn, domains_under_a_field


class TestMerge:
    """Behavioral contracts for merge."""

    def test_valley_ratio_separates_modes_from_cut_continua(self) -> None:
        """Verify valley ratio separates modes from cut continua."""
        rng = np.random.default_rng(10)
        gaussian = rng.normal(size=4000)
        assert _valley_ratio(gaussian, gaussian < 0) > 0.9
        flat = rng.uniform(0, 1, 4000)
        assert _valley_ratio(flat, flat < 0.5) > _MERGE_VALLEY_RATIO
        two = np.concatenate([rng.normal(0, 1, 2000), rng.normal(4, 1, 2000)])
        assert _valley_ratio(two, np.arange(4000) < 2000) < 0.4
        small = np.concatenate([rng.normal(0, 1, 4000), rng.normal(6, 1, 80)])
        assert _valley_ratio(small, np.arange(4080) < 4000) < 0.3

    def test_cut_blob_merges_and_a_separate_blob_stays(self) -> None:
        """Verify cut blob merges and a separate blob stays."""
        rng = np.random.default_rng(11)
        blob = rng.normal(size=(3000, 5))
        other = rng.normal(size=(1000, 5)) + [8, 0, 0, 0, 0]
        points = np.vstack([blob, other])
        cut = np.concatenate([np.where(blob[:, 1] < 0, "a", "b"), np.full(1000, "c")])
        labels, info = merge_unsupported_clusters(points, cut, knn(points))
        truth = np.arange(4000) >= 3000
        assert same_partition(labels, truth)
        assert labels[0] == "0"  # the larger group comes first
        assert (info["n_leiden_clusters"], info["n_merges"]) == (3, 1)
        again, _ = merge_unsupported_clusters(points, cut, knn(points))
        np.testing.assert_array_equal(labels, again)

    def test_chain_of_bands_merges_through_the_union(self) -> None:
        """Verify chain of bands merges through the union."""
        rng = np.random.default_rng(12)
        points = np.column_stack(
            [rng.uniform(0, 6, 6000), rng.normal(0, 0.5, (6000, 4))]
        )
        bands = (points[:, 0] // 2).astype(int)
        labels, info = merge_unsupported_clusters(points, bands, knn(points))
        assert set(labels) == {"0"}
        assert info["n_merges"] == 2

    def test_no_merge_joins_input_clusters_separated_by_a_valley(self) -> None:
        """Verify no merge joins input clusters separated by a valley."""
        from sklearn.cluster import KMeans

        rng = np.random.default_rng(13)
        centres = np.array([[0, 0, 0], [3, 0, 0], [0, 3.5, 0], [2, 2, 3]])
        points = np.vstack([rng.normal(c, [1, 1, 1], (1500, 3)) for c in centres])
        cut = KMeans(12, n_init=1, random_state=0).fit_predict(points)
        graph = knn(points)
        labels, info = merge_unsupported_clusters(points, cut, graph)
        assert info["n_merges"] > 0
        onehot = sp.csr_matrix((np.ones(len(cut)), (np.arange(len(cut)), cut)))
        adjacent = (onehot.T @ graph @ onehot).toarray() > 0
        for i in range(12):
            for j in range(i + 1, 12):
                if not adjacent[i, j] or labels[cut == i][0] != labels[cut == j][0]:
                    continue
                a, b = points[cut == i], points[cut == j]
                axis = b.mean(axis=0) - a.mean(axis=0)
                projection = np.concatenate([a, b]) @ (axis / np.linalg.norm(axis))
                assert (
                    _valley_ratio(projection, np.arange(len(projection)) < len(a))
                    >= _MERGE_VALLEY_RATIO
                )

    def test_st_and_msi_merge_the_split_of_a_continuous_field(self) -> None:
        """Verify st and msi merge the split of a continuous field."""
        from unittest.mock import patch
        import focus.spatial.clusters as _focus_spatial_clusters

        xy, domain, X = domains_under_a_field()
        genes = [f"g{i}" for i in range(X.shape[1])]
        labels, eligible, info = spot_cluster_labels(
            sp.csr_matrix(X), genes, xy, SpotResolution.STANDARD
        )
        assert info["n_leiden_clusters"] > info["n_clusters"]
        assert info["n_clusters"] == 3
        assert adjusted_rand_score(domain[eligible], labels[eligible]) > 0.95
        assert info["parameters"]["merge_valley_ratio"] == _MERGE_VALLEY_RATIO
        seen = []
        merge = _focus_spatial_clusters.merge_unsupported_clusters

        def record_merge(*args: Any) -> tuple[np.ndarray, MergeInfo]:
            """Record the spatial clustering inputs while delegating to the original merge operation."""
            seen.append(merge(*args))
            return seen[-1]

        with patch(
            "focus.spatial.clustering.merge_unsupported_clusters",
            side_effect=record_merge,
        ):
            msi = compute_cluster_labels(
                sp.csr_matrix(X),
                leiden_resolution=0.5,
                normalize_target_sum=None,
                coordinates=xy,
                smoothing_rings=1,
            )
        assert seen[0][1]["n_merges"] > 0
        assert len(set(msi)) == 3
        assert adjusted_rand_score(domain, msi) > 0.95
