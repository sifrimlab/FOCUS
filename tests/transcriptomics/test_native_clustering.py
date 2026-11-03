"""ST clustering behavioral scenarios."""

from typing import cast

from typing import Any
from anndata import AnnData
import pytest
from unittest.mock import patch

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.preprocessing.transcriptomics.clustering import (
    clustering_normalization,
    spot_cluster_labels,
)
from focus.preprocessing.transcriptomics.technology import technology
from focus.spatial.settings import DOMAINS, clustering_preset
from tests.spatial.data import lattice
from tests.transcriptomics.data import expression

SPOT = technology("sequencing", "spot")


def grid(n: int) -> np.ndarray:
    """Coordinates of ``n`` spots on a 16 um square lattice, filled row by row."""
    xy, _ = lattice(int(np.ceil(n / 10)), 10, [16, 0], [0, 16])
    return xy[:n]


class TestNativeClustering:
    """Behavioral contracts for ST clustering."""

    def test_known_domains_determinism_and_no_expression_mutation(self) -> None:
        """Verify known domains determinism and no expression mutation."""
        X = expression()
        original = X.copy()
        names = [f"g{i}" for i in range(X.shape[1])]
        labels, eligible, info = spot_cluster_labels(X, names, grid(120), SPOT)
        repeated, _, _ = spot_cluster_labels(X, names, grid(120), SPOT)
        np.testing.assert_array_equal(labels, repeated)
        assert eligible.all()
        assert cast(sp.csr_matrix, (X != original)).nnz == 0
        assert (
            adjusted_rand_score(np.arange(len(labels)) >= len(labels) // 2, labels)
            > 0.9
        )
        assert info["n_input"] == X.shape[0]
        assert info["n_selected_features"] <= 2000
        assert info["mode"] == DOMAINS

    def test_ineligible_spots_stay_in_place_and_have_no_cluster(self) -> None:
        """Verify ineligible spots stay in place and have no cluster."""
        X = sp.vstack(
            [sp.csr_matrix((1, 60)), sp.csr_matrix(np.ones((1, 60))), expression()]
        ).tocsr()
        labels, eligible, _ = spot_cluster_labels(
            X, [f"g{i}" for i in range(60)], grid(122), SPOT, params={"min_counts": 100}
        )
        assert len(labels) == X.shape[0]
        np.testing.assert_array_equal(eligible[:2], [False, False])
        np.testing.assert_array_equal(labels[:2], ["unassigned", "unassigned"])
        labels, _, info = spot_cluster_labels(
            sp.csr_matrix((4, 5)), list("abcde"), grid(4), SPOT
        )
        assert np.all(labels == "unassigned")
        assert info["n_clusters"] == 0

    def test_large_input_is_not_pooled(self) -> None:
        """Verify large input is not pooled."""
        n = 100_003
        X = sp.csr_matrix(np.tile(np.arange(1, 7), (n, 1)).astype("float32"))
        seen = []

        def pca(sub: AnnData, **kwargs: Any) -> None:
            """Record the PCA input size and store a placeholder embedding."""
            seen.append(sub.n_obs)
            sub.obsm["X_pca"] = np.zeros((sub.n_obs, 2))
            sub.uns["pca"] = {"variance_ratio": np.array([0.7, 0.3])}

        def neighbors(sub: AnnData, **kwargs: Any) -> None:
            """Store an empty neighbour graph."""
            sub.obsp["connectivities"] = sp.csr_matrix((sub.n_obs, sub.n_obs))

        def leiden(sub: AnnData, **kwargs: Any) -> None:
            """Store an alternating partition."""
            sub.obs["leiden"] = np.where(np.arange(sub.n_obs) % 2, "a", "b")

        with (
            patch("scanpy.pp.pca", side_effect=pca),
            patch("scanpy.pp.neighbors", side_effect=neighbors),
            patch("scanpy.tl.leiden", side_effect=leiden),
        ):
            labels, _, _ = spot_cluster_labels(
                X,
                list("abcdef"),
                grid(n),
                SPOT,
                params={"min_counts": 1, "min_genes": 1},
            )
        assert seen == [n]
        assert len(labels) == n
        assert labels[0] != labels[1]

    def test_invalid_settings_and_nonraw_counts_fail(self) -> None:
        """Verify invalid settings and nonraw counts fail."""
        for resolution in ["ultra", None, "Standard"]:
            with pytest.raises(ValueError):
                clustering_preset(DOMAINS, cast(str, resolution), "counts")
        with pytest.raises(ValueError):
            clustering_preset("smooth", "standard", "counts")
        with pytest.raises(ValueError, match="raw integer"):
            spot_cluster_labels(
                sp.csr_matrix([[0.5, 1, 2]]), list("abc"), grid(1), SPOT
            )

    def test_labels_use_the_configured_normalization_never_raw_counts(self) -> None:
        """none falls back to the technology's auto method; linear methods gain log1p."""
        imaging = technology("imaging", "cell")
        area = np.full(10, 20.0)
        assert clustering_normalization(SPOT, "none", None) == "log_normalize"
        assert clustering_normalization(SPOT, "auto", None) == "log_normalize"
        assert clustering_normalization(imaging, "none", area) == "area_log_normalize"
        assert clustering_normalization(imaging, "auto", None) == "log_normalize"
        assert clustering_normalization(SPOT, "cp10k_log", None) == "cp10k_log"
        X = expression()
        names = [f"g{i}" for i in range(X.shape[1])]
        for requested, expected in [
            ("none", "log_normalize"),
            ("size_factors", "size_factors + log1p"),
            ("cp10k_log", "cp10k_log"),
        ]:
            labels, _, info = spot_cluster_labels(X, names, grid(120), SPOT, requested)
            assert info["representation"] == expected
            assert adjusted_rand_score(np.arange(120) >= 60, labels) > 0.9, requested
        _, _, info = spot_cluster_labels(
            X, names, grid(120), imaging, "auto", np.full(120, 30.0)
        )
        assert info["representation"] == "area_log_normalize"
        assert info["resolution"] == "sub_cellular"
