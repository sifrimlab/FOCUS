"""AnnData writing and on-disk concatenation with nullable-string compatibility."""

from typing import Any
from os import PathLike
from mudata import MuData

import os
from pathlib import Path

import anndata
import pandas as pd


def concat_on_disk_compat(*args: Any, **kwargs: Any) -> None:
    """Concatenate AnnData files and normalize strings for compatible persistence."""
    anndata.settings.allow_write_nullable_strings = True
    # Newer anndata versions call .is_dir() on input paths and require Path objects, not strings.
    arguments = list(args)
    if arguments:
        inp = arguments[0]
        if isinstance(inp, dict):
            arguments[0] = {k: Path(v) for k, v in inp.items()}
        elif isinstance(inp, (list, tuple)):
            arguments[0] = [Path(p) for p in inp]
    if len(arguments) >= 2 and isinstance(arguments[1], str):
        arguments[1] = Path(arguments[1])
    anndata.experimental.concat_on_disk(*arguments, **kwargs)


def _compat_strings(obj: object) -> object:
    """Recursively convert pd.StringDtype fields to object dtype in-place."""
    if isinstance(obj, pd.DataFrame):
        for col in obj.columns:
            if isinstance(obj[col].dtype, pd.StringDtype):
                obj[col] = obj[col].astype(object)
        if isinstance(obj.index.dtype, pd.StringDtype):
            obj.index = obj.index.astype(object)
    elif isinstance(obj, pd.Series):
        if isinstance(obj.dtype, pd.StringDtype):
            return obj.astype(object)
    elif isinstance(obj, dict):
        for key in list(obj.keys()):
            obj[key] = _compat_strings(obj[key])
    return obj


def _compat_adata(adata: anndata.AnnData | MuData) -> None:
    """Convert all nullable string fields in an AnnData (or MuData) to object dtype."""
    for df in (adata.obs, adata.var):
        _compat_strings(df)
    for key in list(adata.obsm.keys()):
        adata.obsm[key] = _compat_strings(adata.obsm[key])
    _compat_strings(adata.uns)


def write_h5ad_compat(
    adata: anndata.AnnData, path: str | PathLike[str], **kwargs: Any
) -> None:
    """Write AnnData to h5ad, converting nullable string arrays to object dtype.

    Converts pd.StringDtype in obs, var, obsm, and uns (recursively) before writing
    so that the file is readable by anndata < 0.11 and downstream tools that do not
    support the newer nullable-string encoding.
    """
    _compat_adata(adata)
    adata.write_h5ad(path, **kwargs)


def read_merged_sample_ids(path: str) -> set[str] | None:
    """Return the set of unique obs['sample_id'] values from a merged h5ad file.

    Reads only the obs DataFrame (backed mode) so the full data matrix is never loaded.
    Returns None if the file cannot be opened or lacks the obs['sample_id'] column.
    """
    try:
        import anndata as _ad

        adata = _ad.read_h5ad(path, backed="r")
        ids = set(adata.obs["sample_id"].unique())
        adata.file.close()
        return ids
    except Exception:
        return None


def write_h5ad_atomic(
    adata: anndata.AnnData, path: str | PathLike[str], **kwargs: Any
) -> None:
    """``write_h5ad_compat`` to a temporary sibling, then rename over ``path``.

    A crash mid-write never leaves a truncated file that a later run could mistake for a cache.
    """
    head, tail = os.path.split(path)
    tmp = os.path.join(head, f".{os.getpid()}.tmp.{tail}")
    try:
        write_h5ad_compat(adata, tmp, **kwargs)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
