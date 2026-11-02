"""Compilation coverage and shared-axis contracts from the original implementation."""

from pathlib import Path

import anndata as ad
import mudata
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from focus.config.types import PipelineConfig
from focus.pipeline.compilation import compile_dataset


def compilation_inputs(
    tmp_path: Path,
    covered: int,
) -> tuple[pd.DataFrame, ad.AnnData, Path, Path]:
    """Write three paired observations, with a controlled prefix of nonzero target rows."""
    obs = pd.DataFrame(
        {
            "sample_id": pd.Categorical(["s"] * 3),
            "spatial_annotation": pd.Categorical(["a", "b", "c"]),
        },
        index=["s_0", "s_1", "s_2"],
    )
    anchor = ad.AnnData(
        csr_matrix(np.arange(6, dtype=np.float32).reshape(3, 2)), obs=obs
    )
    anchor.obsm["spatial"] = np.arange(6, dtype=np.float32).reshape(3, 2)
    anchor.uns["spot_size"] = np.array([2, 3], dtype=np.float32)
    target = ad.AnnData(
        csr_matrix(
            np.array([1] * covered + [0] * (3 - covered), dtype=np.float32)[:, None]
        ),
        obs=obs.copy(),
    )
    anchor_path, target_path = tmp_path / "anchor.h5ad", tmp_path / "target.h5ad"
    anchor.write_h5ad(anchor_path)
    target.write_h5ad(target_path)
    return obs, anchor, anchor_path, target_path


@pytest.mark.parametrize("covered", [0, 2, 3])
def test_compilation_filters_all_modalities(tmp_path: Path, covered: int) -> None:
    """Keep paired rows, annotations, and spatial axes; skip empty coverage.

    Parameters
    ----------
    tmp_path
        Temporary dataset directory; inputs and outputs are confined to it.
    covered
        Number of nonzero target rows among three anchor observations.
    """
    obs, anchor, anchor_path, target_path = compilation_inputs(tmp_path, covered)
    config: PipelineConfig = {
        "dataset_path": str(tmp_path),
        "reference_modality": "st",
        "modalities": [{"name": "st", "type": "st"}, {"name": "msi", "type": "msi"}],
    }
    path = compile_dataset(
        config,
        {"st": {"merged": str(anchor_path)}},
        {"msi": {"merged": str(target_path)}},
    )
    if covered == 0:
        assert path is None
        return
    assert path is not None
    result = mudata.read_h5mu(path)
    assert result.n_obs == covered
    for modality in result.mod.values():
        assert modality.n_obs == covered
        assert list(modality.obs_names) == list(obs.index[:covered])
    np.testing.assert_array_equal(
        result.obsm["spatial"], anchor.obsm["spatial"][:covered], strict=True
    )
    np.testing.assert_array_equal(
        result.uns["spot_size"], anchor.uns["spot_size"], strict=True
    )
    assert list(result.obs["spatial_annotation"]) == list(
        obs["spatial_annotation"][:covered]
    )
    np.testing.assert_array_equal(
        result.mod["st"].X.toarray(), anchor.X.toarray()[:covered], strict=True
    )
    assert list(result.mod["st"].var_names) == ["st:0", "st:1"]
