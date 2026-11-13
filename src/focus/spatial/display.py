"""Display outputs of the alignment clustering: the cluster merge tree and per-spot feature images.

The alignment tool shows a spot modality either as clusters, at a resolution the operator sets at
runtime, or as images of single features (ions, genes). Both are computed with the clustering:

- **Merge tree.** Leiden overclusters the units (`leiden_clusters`); :func:`merge_tree` orders the
  Ward merges of spatially touching clusters down to one group. The fine clusters are stored per
  spot (``obs['cluster_fine']``) with the merge order (``uns['focus_clustering']
  ['cluster_merges']``); a resolution of ``L`` clusters is the first ``k - L`` merges. The density
  valley test (:func:`merge_unsupported_clusters`) sets the default number of clusters, and
  ``obs['cluster']`` holds the tree cut at that level.
- **Feature images.** ``obsm['focus_feature_images']`` (uint8, one column per image, 0 where a spot
  is not eligible) with the feature names in ``uns['focus_clustering']['image_features']`` and
  their display labels in ``['image_labels']``.

Every sample stores the same number of image columns (zeros when fewer are found), so per-sample
files concatenate.
"""

from collections.abc import Callable
from dataclasses import dataclass

import anndata as ad
import numpy as np
import pandas as pd
import scipy.sparse as sp

from focus.spatial.clusters import cut_tree, merge_tree
from focus.spatial.features import SpatialFeatures, image_values
from focus.spatial.settings import N_IMAGE_FEATURES
from focus.spatial.smoothing import smoothing_operator
from focus.spatial.types import ClusterInfo
from focus.spatial.units import SpatialUnits
from focus.spot_colors import label_order

FINE_KEY = "cluster_fine"
IMAGES_KEY = "focus_feature_images"


@dataclass
class SpotDisplay:
    """Per-spot display arrays of one sample (kept in memory until :func:`store_display`)."""

    fine_labels: np.ndarray  # (n_spots,) fine cluster label or 'unassigned'
    images: np.ndarray  # (n_spots, N_IMAGE_FEATURES) uint8
    image_names: np.ndarray  # (m,) feature names of the images, m <= N_IMAGE_FEATURES


def _size_ranks(codes: np.ndarray) -> np.ndarray:
    """Codes relabelled 0.. by decreasing group size (ties by first code)."""
    _, inverse = np.unique(codes, return_inverse=True)
    sizes = np.bincount(inverse)
    rank = np.empty(sizes.size, dtype=np.int64)
    rank[np.argsort(-sizes, kind="stable")] = np.arange(sizes.size)
    return rank[inverse]


def cluster_tree(
    fine: np.ndarray,
    default: np.ndarray,
    embedding: np.ndarray,
    units: SpatialUnits,
    unit_ok: np.ndarray,
    info: ClusterInfo,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Fine codes of the kept units (by decreasing size) and their labels at the default level.

    ``fine``: Leiden labels; ``default``: labels after the density valley merges, whose number of
    groups sets the default level. Records the merge order and both levels in ``info``.
    """
    codes = _size_ranks(np.asarray(fine))
    k = int(codes.max()) + 1
    operator = smoothing_operator(units, 1, unit_ok).tocoo()
    off = operator.row != operator.col
    contact = np.zeros((k, k))
    np.add.at(contact, (codes[operator.row[off]], codes[operator.col[off]]), 1.0)
    # The valley partition of every fine cluster (each lies wholly in one valley group), so the
    # tree passes through it and its level reproduces obs['cluster'] exactly.
    _, valley = np.unique(np.asarray(default), return_inverse=True)
    partition = np.zeros(k, dtype=np.int64)
    partition[codes] = valley
    merges = merge_tree(embedding, codes, contact, partition)
    level = int(valley.max()) + 1
    info["cluster_merges"] = np.asarray(merges, dtype=np.int64).reshape(-1, 2)
    info["cluster_default_level"] = level
    info["n_fine_clusters"] = k
    return codes, _size_ranks(cut_tree(codes, merges, level))


def spot_values(
    unit_values: np.ndarray,
    eligible: np.ndarray,
    kept: np.ndarray,
    bin_ids: np.ndarray,
    fill: object,
) -> np.ndarray:
    """Per-spot copy of ``unit_values`` (one row per kept unit); ``fill`` where not eligible."""
    shape = (len(bin_ids),) + unit_values.shape[1:]
    out = np.full(
        shape, fill, dtype=object if isinstance(fill, str) else unit_values.dtype
    )
    out[eligible] = unit_values[np.searchsorted(kept, bin_ids[eligible])]
    return out


def levels(
    sub: ad.AnnData,
    labels: np.ndarray,
    domains: bool,
    units: SpatialUnits,
    unit_ok: np.ndarray,
    info: ClusterInfo,
) -> tuple[np.ndarray, np.ndarray]:
    """(fine labels, default labels) of the kept units; the merge tree in ``domains`` mode."""
    if not domains or "leiden" not in sub.obs:
        return labels, labels
    codes, default = cluster_tree(
        sub.obs["leiden"].to_numpy(),
        labels,
        sub.obsm["X_spatial"],
        units,
        unit_ok,
        info,
    )
    return codes.astype(str), default.astype(str)


def spot_display(
    fine_units: np.ndarray,
    X: sp.csr_matrix,
    ranking: SpatialFeatures | None,
    feature_names: np.ndarray,
    spots: tuple[np.ndarray, np.ndarray, np.ndarray],
    everywhere: Callable[[], sp.csr_matrix] | None = None,
) -> SpotDisplay:
    """
    Per-spot fine labels and feature images.

    ``spots`` is (eligible spots, kept units, unit of every spot). The labels exist only for the
    eligible spots. The feature images do not depend on the clustering mask: where ``everywhere``
    (the represented values of *every* spot) is available, each spot shows its own value, so
    tissue that the foreground detection removed is still coloured. Otherwise (clustering on
    aggregated blocks, or a representation that cannot cover the excluded spots) images hold the
    values of the kept units and 0, "no value", elsewhere.
    """
    eligible, kept, bin_ids = spots
    columns = np.array([], dtype=np.intp) if ranking is None else ranking.image_columns
    full = _everywhere(everywhere) if len(columns) else None
    if full is not None:
        images = image_values(full, columns)
    else:
        images = spot_values(image_values(X, columns), eligible, kept, bin_ids, 0)
    return SpotDisplay(
        spot_values(
            np.asarray(fine_units, dtype=object), eligible, kept, bin_ids, "unassigned"
        ),
        images,
        np.asarray(feature_names[columns], dtype=str),
    )


def _everywhere(
    everywhere: Callable[[], sp.csr_matrix] | None,
) -> sp.csr_matrix | None:
    """The represented values of every spot, or None when they cannot be built."""
    if everywhere is None:
        return None
    try:
        return everywhere()
    except ValueError:
        # e.g. area normalization without a finite area for the excluded spots
        return None


def padded_images(images: np.ndarray) -> np.ndarray:
    """(n, N_IMAGE_FEATURES) uint8: ``images`` with zero columns appended (or truncated)."""
    out = np.zeros((images.shape[0], N_IMAGE_FEATURES), dtype=np.uint8)
    m = min(images.shape[1], N_IMAGE_FEATURES)
    out[:, :m] = images[:, :m]
    return out


def store_display(
    adata: ad.AnnData,
    labels: np.ndarray,
    info: ClusterInfo,
    label_of: Callable[[str], str] = str,
) -> None:
    """
    Write the display outputs of a clustering into ``adata`` and drop them from ``info``.

    Samples without a merge tree or images (foreground or gradient modes, too few units) store
    their labels as the fine clusters and empty images, so every sample has the same keys.
    """
    display = info.pop("display", None)
    fine = labels
    images = np.zeros((adata.n_obs, N_IMAGE_FEATURES), dtype=np.uint8)
    names = np.array([], dtype=str)
    if isinstance(display, SpotDisplay):
        fine = display.fine_labels
        images = padded_images(display.images)
        names = np.asarray(display.image_names, dtype=str)
    fine = np.asarray(fine, dtype=object).astype(str)
    adata.obs[FINE_KEY] = pd.Categorical(fine, categories=label_order(fine))
    adata.obsm[IMAGES_KEY] = images
    info["image_features"] = names
    info["image_labels"] = np.asarray([label_of(name) for name in names], dtype=str)


def all_spot_matrix(
    raw: sp.csr_matrix,
    represent: Callable[
        [sp.csr_matrix, SpatialUnits, np.ndarray], tuple[sp.csr_matrix, str]
    ],
    units: SpatialUnits,
) -> Callable[[], sp.csr_matrix] | None:
    """Represented values of every spot, for native units (a unit is a spot); else None."""
    if units.bin_ids is not None:
        return None
    every = np.arange(raw.shape[0])
    return lambda: represent(raw, units, every)[0]
