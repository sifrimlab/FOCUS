"""Persist registered AnnData containers using the existing atomic writer."""

import os

import anndata

from focus.io.anndata import write_h5ad_atomic

_H5AD_COMPRESSION = "gzip"


def write_registered(adata: anndata.AnnData, path: str) -> None:
    """Write a registration AnnData using the established compression settings."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_h5ad_atomic(adata, path, compression=_H5AD_COMPRESSION)
