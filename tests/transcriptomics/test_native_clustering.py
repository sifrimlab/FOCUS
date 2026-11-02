"""NativeClustering behavioral scenarios."""

from typing import cast

from typing import Any
from anndata import AnnData
import pytest
from unittest.mock import patch

import numpy as np
import scipy.sparse as sp
from sklearn.metrics import adjusted_rand_score

from focus.preprocessing.transcriptomics.clustering import native_cluster_labels
from focus.spatial.settings import clustering_preset
from tests.transcriptomics.data import expression


class TestNativeClustering:
    """Behavioral contracts for native clustering."""

    def test_known_domains_determinism_and_no_expression_mutation(self) -> None:
        """Verify known domains determinism and no expression mutation."""
        X = expression()
        original = X.copy()
        names = [f"g{i}" for i in range(X.shape[1])]
        labels, eligible, info = native_cluster_labels(X, names)
        repeated, _, _ = native_cluster_labels(X, names)
        np.testing.assert_array_equal(labels, repeated)
        assert eligible.all()
        assert cast(sp.csr_matrix, (X != original)).nnz == 0
        assert (
            adjusted_rand_score(np.arange(len(labels)) >= len(labels) // 2, labels)
            > 0.9
        )
        assert info["n_input"] == X.shape[0]
        assert info["n_selected_genes"] <= 2000

    def test_ineligible_spots_stay_in_place_and_have_no_cluster(self) -> None:
        """Verify ineligible spots stay in place and have no cluster."""
        X = sp.vstack(
            [sp.csr_matrix((1, 60)), sp.csr_matrix(np.ones((1, 60))), expression()]
        ).tocsr()
        labels, eligible, _ = native_cluster_labels(
            X, [f"g{i}" for i in range(60)], {"min_counts": 100}
        )
        assert len(labels) == X.shape[0]
        np.testing.assert_array_equal(eligible[:2], [False, False])
        np.testing.assert_array_equal(labels[:2], ["unassigned", "unassigned"])
        labels, _, info = native_cluster_labels(sp.csr_matrix((4, 5)), list("abcde"))
        assert np.all(labels == "unassigned")
        assert info["n_clusters"] == 0

    def test_large_input_is_not_pooled(self) -> None:
        """Verify large input is not pooled."""
        n = 100_003
        X = sp.csr_matrix(np.tile(np.arange(1, 7), (n, 1)).astype("float32"))
        seen = []

        def pca(sub: AnnData, **kwargs: Any) -> None:
            """Record PCA input/count-normalization diagnostics and delegate to the original implementation."""
            seen.append(sub.n_obs)
            sub.obsm["X_pca"] = np.zeros((sub.n_obs, 2))
            sub.uns["pca"] = {"variance_ratio": np.array([0.7, 0.3])}

        def neighbors(sub: AnnData, **kwargs: Any) -> None:
            """Record the neighbour-graph arguments and delegate to the original Scanpy operation."""
            sub.obsp["connectivities"] = sp.csr_matrix((sub.n_obs, sub.n_obs))

        def leiden(sub: AnnData, **kwargs: Any) -> None:
            """Record the Leiden arguments and delegate to the original Scanpy partitioning operation."""
            sub.obs["cluster"] = np.where(np.arange(sub.n_obs) % 2, "a", "b")

        with (
            patch(
                "scanpy.pp.highly_variable_genes",
                side_effect=lambda sub, **kw: sub.var.__setitem__(
                    "highly_variable", True
                ),
            ),
            patch("scanpy.pp.pca", side_effect=pca),
            patch("scanpy.pp.neighbors", side_effect=neighbors),
            patch("scanpy.tl.leiden", side_effect=leiden),
        ):
            labels, _, _ = native_cluster_labels(
                X, list("abcdef"), {"min_counts": 1, "min_genes": 1}
            )
        assert seen == [n]
        assert len(labels) == n
        assert labels[0] != labels[1]

    def test_invalid_settings_and_nonraw_counts_fail(self) -> None:
        """Verify invalid settings and nonraw counts fail."""
        for resolution in ["ultra", None, "Standard"]:
            with pytest.raises(ValueError):
                clustering_preset(cast(str, resolution))
        with pytest.raises(ValueError, match="raw integer"):
            native_cluster_labels(sp.csr_matrix([[0.5, 1, 2]]), list("abc"))
