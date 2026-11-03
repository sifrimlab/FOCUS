"""Per-sample annotation persistence, merge ordering, and backed handle cleanup."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import anndata as ad
import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix

from focus.config.types import PipelineConfig
from focus.pipeline.annotations import _run_annotation_transfer
from focus.pipeline.types import ModalityFiles


def annotation_inputs(
    root: Path, same_source: bool
) -> tuple[PipelineConfig, ModalityFiles, ModalityFiles]:
    """Write two small samples and source polygons without changing test input paths."""
    source = "st" if same_source else "annotation"
    config: PipelineConfig = {
        "dataset_path": str(root),
        "reference_modality": "st",
        "spatial_annotations": {"modality_name": source, "file_type": "geojson"},
    }
    files: ModalityFiles = {"st": {}}
    aligned: ModalityFiles = {source: {}}
    polygon = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"classification": {"name": "region"}},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-1, -1], [5, -1], [5, 5], [-1, 5], [-1, -1]]],
                },
            }
        ],
    }
    for sample in ("A", "B"):
        folder = root / sample / source
        folder.mkdir(parents=True)
        (folder / "regions.geojson").write_text(json.dumps(polygon))
        data = ad.AnnData(
            csr_matrix(np.arange(6, dtype=np.float32).reshape(3, 2)),
            obs=pd.DataFrame(
                {"sample_id": [sample] * 3}, index=[f"{sample}_{i}" for i in range(3)]
            ),
        )
        data.obsm["spatial"] = np.array([[0, 0], [2, 2], [10, 10]], dtype=np.float32)
        data.uns["spot_size"] = np.array([1, 1], dtype=np.float32)
        path = root / f"{sample}-reference.h5ad"
        data.write_h5ad(path)
        files["st"][sample] = str(path)
        if not same_source:
            data.obsm[f"{source}_spatial"] = data.obsm["spatial"] + [1, 0]
            mapped = root / f"{sample}-aligned.h5ad"
            data.write_h5ad(mapped)
            aligned[source][sample] = str(mapped)
    return config, files, aligned


@pytest.mark.parametrize("same_source", [False, True])
def test_annotation_stage_contents(tmp_path: Path, same_source: bool) -> None:
    """Retain counts, native coordinates, polygon labels, and ordered merged observations."""
    config, files, aligned = annotation_inputs(tmp_path, same_source)
    outputs = _run_annotation_transfer(config, files, aligned)
    assert list(outputs) == ["A", "B", "merged"]
    for sample in ("A", "B"):
        original, result = (
            ad.read_h5ad(files["st"][sample]),
            ad.read_h5ad(outputs[sample]),
        )
        np.testing.assert_array_equal(
            original.X.toarray(), result.X.toarray(), strict=True
        )
        np.testing.assert_array_equal(
            original.obsm["spatial"], result.obsm["spatial"], strict=True
        )
        np.testing.assert_array_equal(
            original.uns["spot_size"], result.uns["spot_size"], strict=True
        )
        values = result.obs["spatial_annotation"].tolist()
        assert values[:2] == ["region", "region"]
        assert pd.isna(values[2])
        assert "spatial_annotation" not in original.obs
    merged = ad.read_h5ad(outputs["merged"])
    assert list(merged.obs["sample_id"]) == ["A"] * 3 + ["B"] * 3


def test_annotation_failure_closes_backed_inputs(tmp_path: Path) -> None:
    """Close both backed inputs when writing an annotated output raises."""
    config, files, aligned = annotation_inputs(tmp_path, False)
    opened: list[ad.AnnData] = []
    read = ad.read_h5ad

    def record_read(*args: Any, **kwargs: Any) -> ad.AnnData:
        """Record handles at the external AnnData reader boundary."""
        data = read(*args, **kwargs)
        opened.append(data)
        return data

    with (
        patch("focus.pipeline.annotations.anndata.read_h5ad", side_effect=record_read),
        patch(
            "focus.pipeline.annotations.write_h5ad_compat",
            side_effect=RuntimeError("controlled write failure"),
        ),
    ):
        with pytest.raises(RuntimeError, match="controlled write failure"):
            _run_annotation_transfer(config, files, aligned)
    assert len(opened) == 2
    assert all(not data.file.is_open for data in opened)


def test_merged_annotation_keeps_the_merged_reference(tmp_path: Path) -> None:
    """The merged annotated file is the merged reference (panel, spot sizes) plus the labels."""
    config, files, aligned = annotation_inputs(tmp_path, False)
    parts = [ad.read_h5ad(files["st"][sample]) for sample in ("A", "B")]
    parts[1].var_names = ["g0", "g2"]
    parts[0].var_names = ["g0", "g1"]
    merged = ad.concat(parts, join="outer", fill_value=0)
    merged.uns["spot_size"] = {"A": [1.0, 1.0], "B": [1.0, 1.0]}
    merged_path = tmp_path / "merged-reference.h5ad"
    merged.write_h5ad(merged_path)
    files["st"]["merged"] = str(merged_path)
    outputs = _run_annotation_transfer(config, files, aligned)
    result = ad.read_h5ad(outputs["merged"])
    assert list(result.var_names) == ["g0", "g1", "g2"]
    assert dict(result.uns["spot_size"]).keys() == {"A", "B"}
    np.testing.assert_array_equal(result.X.toarray(), merged.X.toarray(), strict=True)
    values = result.obs["spatial_annotation"].tolist()
    assert values[0:2] == ["region", "region"] and values[3:5] == ["region", "region"]
    assert pd.isna(values[2]) and pd.isna(values[5])
    assert "spatial_annotation" not in ad.read_h5ad(merged_path).obs
