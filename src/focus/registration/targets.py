"""Resolve the preprocessed target data a spot registration reads for each sample."""

import os
from collections.abc import Mapping

import anndata
import h5py
import numpy as np

from focus.spatial.footprint_shape import read_spot_size


def target_sources(target_files: dict[str, str]) -> dict[str, str]:
    """
    Map every sample to the merged preprocessed target file when it exists.

    The merged file carries the cross-sample feature filter, the union feature panel and the
    merged normalization; the per-sample files predate them, so registering those would give each
    sample its own panel and the inner-join merge would drop every feature missing from one
    sample. Without a merged file the per-sample files are used. The ``"merged"`` entry is kept.
    """
    merged = target_files.get("merged")
    if merged is None or not os.path.exists(merged):
        return dict(target_files)
    sources = {sample: merged for sample in target_files if sample != "merged"}
    sources["merged"] = merged
    return sources


def read_target(target_files: dict[str, str], sample_id: str) -> anndata.AnnData:
    """
    Load one sample's target spots: its rows of the merged file, or its own per-sample file.

    Raises
    ------
    ValueError
        When the merged file holds no row with ``obs['sample_id'] == sample_id``.
    """
    path = target_files[sample_id]
    if path != target_files.get("merged"):
        return anndata.read_h5ad(path)
    merged = anndata.read_h5ad(path, backed="r")
    try:
        rows = np.flatnonzero(
            merged.obs["sample_id"].astype(str).to_numpy() == sample_id
        )
        if rows.size == 0:
            raise ValueError(
                f"The merged target file {path} has no spots of sample '{sample_id}'"
            )
        return merged[rows].to_memory()
    finally:
        if merged.file is not None:
            merged.file.close()


def target_normalization(target_files: dict[str, str]) -> str | None:
    """The ``uns['focus_normalization']['method']`` of the merged target file, or None."""
    merged = target_files.get("merged")
    if merged is None or not os.path.exists(merged):
        return None
    with h5py.File(merged, "r") as handle:
        key = "uns/focus_normalization/method"
        if key not in handle:
            return None
        value = handle[key][()]
    return value.decode() if isinstance(value, bytes) else str(value)


def target_spot_size(target: anndata.AnnData, sample_id: str) -> np.ndarray | None:
    """
    ``[sx, sy]`` of one sample's target spots, or None when the file does not carry a valid size.

    A merged target file stores ``uns['spot_size']`` as ``{sample_id: [sx, sy]}``, a per-sample
    file as the bare array.
    """
    value = target.uns.get("spot_size")
    if isinstance(value, Mapping):
        value = value.get(sample_id)
    return read_spot_size(value)
