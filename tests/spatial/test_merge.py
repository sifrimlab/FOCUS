"""Merge behavioral scenarios."""

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.preprocessing.transcriptomics.clustering import spot_cluster_labels
from focus.preprocessing.transcriptomics.technology import technology
from focus.spatial.settings import _MERGE_VALLEY_RATIO
from focus.spatial.clusters import (
    _valley_ratio,
    discriminant_axis,
    merge_unsupported_clusters,
    within_precision,
)
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

    def test_an_outlier_does_not_hide_a_valley(self) -> None:
        """A few extreme units leave the density range, so a real valley stays visible (audit 2.2)."""
        rng = np.random.default_rng(14)
        two = np.concatenate([rng.normal(0, 1, 2000), rng.normal(5, 1, 2000)])
        first = np.arange(4000) < 2000
        clean = _valley_ratio(two, first)
        assert clean < 0.3
        outlying = two.copy()
        outlying[0] = 1e4
        # On the min-max range the outlier squeezed both modes into a few bins (ratio near 1).
        assert abs(_valley_ratio(outlying, first) - clean) < 0.05

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

    def test_overclustered_populations_merge_back_to_the_populations(self) -> None:
        """Many fragments of four separated populations merge into exactly the four."""
        from sklearn.cluster import KMeans

        rng = np.random.default_rng(13)
        centres = np.array([[0, 0, 0], [6, 0, 0], [0, 6, 0], [4, 4, 5]])
        points = np.vstack([rng.normal(c, 1, (1500, 3)) for c in centres])
        truth = np.repeat(np.arange(4), 1500)
        cut = KMeans(24, n_init=1, random_state=0).fit_predict(points)
        labels, info = merge_unsupported_clusters(points, cut, knn(points))
        # A few tail points of one population sit in a fragment of its neighbour.
        assert len(set(labels)) == 4
        assert adjusted_rand_score(truth, labels) > 0.99
        assert info["n_merges"] == 20
        assert np.all(info["merge_ratios"] >= _MERGE_VALLEY_RATIO)

    def test_discriminant_axis_weighs_noisy_dimensions_down(self) -> None:
        """Two groups apart along a quiet dimension are separated along it, not a noisy one."""
        rng = np.random.default_rng(15)
        points = np.column_stack([rng.normal(0, 5, 4000), rng.normal(0, 0.5, 4000)])
        points[2000:, 1] += 3
        points[2000:, 0] += 3  # same shift, but buried in the noisy dimension
        codes = (np.arange(4000) >= 2000).astype(int)
        axis = discriminant_axis(
            within_precision(points, codes),
            points[codes == 1].mean(axis=0) - points[codes == 0].mean(axis=0),
        )
        assert axis is not None and abs(axis[1]) > 0.99
        assert _valley_ratio(points @ axis, codes == 0) < 0.2

    def test_st_merges_the_split_of_a_continuous_field(self) -> None:
        """Leiden cuts the field into extra clusters; the merge returns the three domains."""
        xy, domain, X = domains_under_a_field()
        genes = [f"g{i}" for i in range(X.shape[1])]
        labels, eligible, info = spot_cluster_labels(
            sp.csr_matrix(X), genes, xy, technology("sequencing", "spot")
        )
        assert info["n_leiden_clusters"] > info["n_clusters"] == 3
        assert adjusted_rand_score(domain[eligible], labels[eligible]) > 0.85
        assert info["parameters"]["merge_valley_ratio"] == _MERGE_VALLEY_RATIO
