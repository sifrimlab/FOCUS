"""Coordinate scaling and AnnData representation of standalone grid embeddings."""

from typing import cast

import anndata
import numpy as np

from focus.config.options import RegistrationType
from focus.registration.free_form_cache import _PATCH_MODE_FREE_FORM
from focus.config.grid_settings import GridSettings


def level0_centers(
    centers: np.ndarray, dimensions: list[tuple[int, int]], level: int
) -> np.ndarray:
    """Copy coarser centers to float32 and map them into measured level-0 pixels."""
    height0, width0 = dimensions[0]
    height, width = dimensions[level]
    centers = centers.astype(np.float32, copy=True)
    centers[:, 0] *= width0 / width
    centers[:, 1] *= height0 / height
    return centers


def grid_anndata(
    embeddings: np.ndarray,
    centers: np.ndarray,
    sample: str,
    settings: GridSettings,
    level: int,
    dimensions: list[tuple[int, int]] | None,
) -> anndata.AnnData:
    """Construct the existing dense feature container and its per-level metadata."""
    data = anndata.AnnData(
        X=embeddings,
        obsm={"spatial": centers},
        obs={"sample_id": [sample] * embeddings.shape[0]},
    )
    data.uns["registration_type"] = RegistrationType.FEATURE_EXTRACTION
    data.uns["patch_mode"] = _PATCH_MODE_FREE_FORM
    data.uns["patch_size"] = settings.patch_size
    data.uns["background_color"] = settings.background
    if level > 0:
        data.uns["res_level"] = level
        data.uns["downsample_factor"] = (
            cast(list[tuple[int, int]], dimensions)[0][1]
            / cast(list[tuple[int, int]], dimensions)[level][1]
        )
    return data
