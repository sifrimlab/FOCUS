"""MuData writing with nullable-string conversion for every contained modality."""

from typing import Any
from os import PathLike
from mudata import MuData


from focus.io.anndata import _compat_adata


def write_h5mu_compat(mdata: MuData, path: str | PathLike[str], **kwargs: Any) -> None:
    """Write MuData to h5mu, converting nullable string arrays to object dtype.

    Converts pd.StringDtype in obs, var, obsm, and uns (recursively) for the
    top-level MuData and every modality AnnData so the file is readable by
    anndata/mudata < 0.11 and downstream tools that do not support the newer
    nullable-string encoding.
    """
    for adata in [mdata] + list(mdata.mod.values()):
        _compat_adata(adata)
    mdata.write(path, **kwargs)
