"""Display outputs of a clustered spot modality, read for the alignment tool.

Preprocessing stores the fine clusters (``obs['cluster_fine']``), their merge order and default
level (``uns['focus_clustering']``), and per-spot feature images (``obsm['focus_feature_images']``)
(see :mod:`focus.spatial.display`). Files written before them have none, and are shown as before.
"""

import os
from dataclasses import dataclass

import anndata
import numpy as np

from focus.alignment.types import ModalityMetadata
from focus.spatial.display import FINE_KEY, IMAGES_KEY


@dataclass
class SpotDisplayData:
    """What the browser needs for the resolution slider and the feature images."""

    fine_labels: np.ndarray  # (n,) str, 'unassigned' for spots without a cluster
    merges: list[list[str]]  # merge order, pairs of fine labels
    default_level: int
    images: np.ndarray  # (n, m) uint8 feature images, m = len(image_labels)
    image_labels: list[str]

    def metadata(self) -> ModalityMetadata:
        """Alignment metadata of the merge tree and of the feature images."""
        out: ModalityMetadata = {
            "cluster_tree": {"merges": self.merges, "default_level": self.default_level}
        }
        if self.image_labels:
            out["feature_images"] = self.image_labels
        return out

    def payload_images(self, order: np.ndarray) -> np.ndarray | None:
        """Feature images of the spots in payload ``order``; None without images (FSP v1)."""
        return self.images[order] if self.image_labels else None


def read_spot_display(filename: str) -> SpotDisplayData | None:
    """The display outputs of a processed spot file, or None when it has none (or is missing)."""
    if not os.path.exists(filename):
        return None
    adata = anndata.read_h5ad(filename, backed="r")
    try:
        info = dict(adata.uns.get("focus_clustering", {}))
        merges = np.asarray(
            info.get("cluster_merges", np.zeros((0, 2))), dtype=np.int64
        )
        if FINE_KEY not in adata.obs or merges.size == 0:
            return None
        labels = [str(v) for v in np.asarray(info.get("image_labels", []), dtype=str)]
        images = (
            np.asarray(adata.obsm[IMAGES_KEY][:, : len(labels)], dtype=np.uint8)
            if IMAGES_KEY in adata.obsm and labels
            else np.zeros((adata.n_obs, 0), dtype=np.uint8)
        )
        return SpotDisplayData(
            np.asarray(adata.obs[FINE_KEY].values, dtype=str),
            [[str(a), str(b)] for a, b in merges.reshape(-1, 2)],
            int(info.get("cluster_default_level", merges.shape[0] + 1)),
            images,
            labels,
        )
    finally:
        adata.file.close()
