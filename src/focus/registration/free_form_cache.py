"""Cache metadata and output names for the standalone image-grid script."""

from __future__ import annotations
from typing import cast
import os
from anndata import AnnData
from focus.config.options import RegistrationType

_PATCH_MODE_FREE_FORM = "free_form"


def _cached_registration_valid(
    cached_adata: AnnData, patch_size: int, background_color: str
) -> bool:
    """True when a cached free-form registration file can be safely reused.

    registration_type alone is not enough to validate the cache: an anchor-based
    feature_extraction run (e.g. after a second modality is added) stamps the same
    registration_type at this same path. The patch_mode marker disambiguates in both
    directions: this script recomputes a cache missing the marker (rejecting an anchor-based
    output), and the real pipeline recomputes a free-form file at this path, since it carries
    no registration fingerprint.
    """
    return cast(
        bool,
        (
            cached_adata.uns.get("registration_type")
            == RegistrationType.FEATURE_EXTRACTION
            and cached_adata.uns.get("patch_mode") == _PATCH_MODE_FREE_FORM
            and cached_adata.uns.get("patch_size") == patch_size
            and cached_adata.uns.get("background_color") == background_color
        ),
    )


def _res_level_path(path: str, level: int) -> str:
    """Insert a ``_res{level}`` marker before the extension for coarser resolution levels.

    Level 0 (the canonical FOCUS full-resolution output) returns ``path`` unchanged so its
    filename stays byte-for-byte compatible with the existing anchor-based registration output.
    Registration output is always the single-token ``.h5ad`` extension, so ``splitext`` is safe.
    """
    if level == 0:
        return path
    root, ext = os.path.splitext(path)
    return f"{root}_res{level}{ext}"


def _level_cache_valid(
    cached_adata: AnnData, level: int, patch_size: int, background_color: str
) -> bool:
    """Like :func:`_cached_registration_valid`, but for coarser levels also requires the stamped
    ``res_level`` to match. Level 0 files carry no ``res_level`` key and use the base check only.
    """
    if not _cached_registration_valid(cached_adata, patch_size, background_color):
        return False
    if level == 0:
        return True
    return cast(bool, cached_adata.uns.get("res_level") == level)
