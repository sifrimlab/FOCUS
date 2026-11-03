"""Prepare modality metadata, image previews and spot payloads for alignment."""

from __future__ import annotations
from focus.alignment.types import ModalityMetadata, PreparedModality
from focus.alignment.preview import _generate_cluster_colors
from focus.alignment.preview import _image_to_rgb_uint8
from focus.alignment.settings import _IMAGE_MODALITIES
from focus.alignment.settings import _SPOT_MODALITIES
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.alignment.coordinator import DirectMappingAligner

import os
import anndata
import numpy as np
from PIL import Image


from focus.alignment.image_view import ImageView, load_view

from focus.alignment.spot_payload import pack_spots, acquisition_basis
from focus.spot_colors import cluster_colors, label_order


class AlignmentInputs:
    """Prepare display metadata and payloads while retaining full native coordinates."""

    def __init__(self, aligner: DirectMappingAligner) -> None:
        """Bind the component to its existing aligner state."""
        self.aligner = aligner

    # --- Data Loading ---

    def _load_ome_tiff(
        self, filename: str
    ) -> tuple[np.ndarray | ImageView, tuple[int, int], tuple[int, int]]:
        """
        Load the display level of an OME-TIFF plus the (H, W) of that level and of level 0.

        The display level is the largest stored level within the display caps
        (``image_view.display_level``). A FOCUS image carrying its display settings is
        returned as an :class:`ImageView` (rendered with the stored per-channel windows and
        colours); any other file is converted to RGB uint8 with the legacy min-max rule.
        (H, W) come from the OME axes, so channel-first and channel-last layouts scale alike.
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"File not found: {filename}")
        view, data, axes, low, full = load_view(filename)
        if view is not None:
            return view, low, full
        return _image_to_rgb_uint8(data, axes), low, full

    def _load_anndata_spots(
        self, filename: str
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, str]]:
        """
        Load spatial data from an AnnData file for GUI display.

        Returns
        -------
        tuple of (coordinates, spot_size, foreground_mask, cluster_labels, color_map)
                coordinates: (N, 2) float32 spatial coordinates
                spot_size: (2,) float32 spot dimensions [x, y]
                cluster_labels: (N,) str cluster labels
                color_map: dict mapping cluster label → hex color
        """
        if not os.path.exists(filename):
            raise FileNotFoundError(f"File not found: {filename}")

        adata = anndata.read_h5ad(filename, backed="r")

        if "spatial" not in adata.obsm:
            raise ValueError(f"AnnData file missing .obsm['spatial']: {filename}")
        coordinates = np.asarray(adata.obsm["spatial"], dtype=np.float32)

        # Spot size (matching preprocessing convention)
        if "spot_size" in adata.uns:
            spot_size = np.asarray(adata.uns["spot_size"], dtype=np.float32).flatten()
            if spot_size.size == 1:
                spot_size = np.array(
                    [float(spot_size[0]), float(spot_size[0])], dtype=np.float32
                )
        else:
            spot_size = np.array([1.0, 1.0], dtype=np.float32)

        # Foreground mask
        if "foreground" in adata.obs:
            foreground_mask = np.asarray(adata.obs["foreground"].values, dtype=bool)
        else:
            foreground_mask = np.ones(adata.n_obs, dtype=bool)

        # Cluster labels (preprocessing writes 'cluster'; 'leiden'/'clusters' kept as
        # fallbacks so files processed by older versions still display).
        if "cluster" in adata.obs:
            cluster_labels = np.asarray(adata.obs["cluster"].values, dtype=str)
        elif "leiden" in adata.obs:
            cluster_labels = np.asarray(adata.obs["leiden"].values, dtype=str)
        elif "clusters" in adata.obs:
            cluster_labels = np.asarray(adata.obs["clusters"].values, dtype=str)
        else:
            cluster_labels = np.zeros(adata.n_obs, dtype=str)

        # Generate color map for the cluster labels
        color_map = _generate_cluster_colors(cluster_labels)
        self.aligner._spot_basis = acquisition_basis(adata)
        adata.file.close()
        del adata
        return coordinates, spot_size, foreground_mask, cluster_labels, color_map

    # --- GUI Data Preparation ---

    def _prepare_image_data(
        self, filename: str, modality_name: str
    ) -> PreparedModality:
        """Prepare image modality data for the GUI.

        Returns (metadata, payload, full_coordinates, scale_factors).
        full_coordinates is None for IMAGE modalities.
        """
        image, (H_low, W_low), (H_full, W_full) = self._load_ome_tiff(filename)
        metadata: ModalityMetadata = {
            "modality_type": "IMAGE",
            "modality_name": modality_name,
            "image_shape": [int(H_low), int(W_low)],
        }
        payload: ImageView | Image.Image
        if isinstance(image, ImageView):
            payload = image
            if image.metadata():
                metadata["channels"] = image.metadata()
        else:
            payload = Image.fromarray(image)
        # Display (lowest level) -> full-resolution scale, per axis [y_scale, x_scale].
        scale_factors = np.array([H_full / H_low, W_full / W_low])
        return metadata, payload, None, scale_factors

    def _prepare_spot_data(self, filename: str, modality_name: str) -> PreparedModality:
        """Native centers and footprints; compact transport never pools labels or geometry."""
        if self.aligner._exact_spots:
            return self._prepare_exact_spot_data(filename, modality_name)
        coordinates, spot_size, foreground, labels, colors = self._load_anndata_spots(
            filename
        )
        unique = label_order(labels)
        indices = {label: i for i, label in enumerate(unique)}
        classes = np.fromiter(
            (indices[str(label)] for label in labels),
            dtype=np.uint32,
            count=len(labels),
        )
        metadata: ModalityMetadata = {
            "modality_type": "SPOT",
            "modality_name": modality_name,
            "spot_size": spot_size.tolist(),
            "color_map": colors,
            "cluster_labels": unique,
            "spot_encoding": "FSP1",
            "spot_count": len(coordinates),
        }
        basis = getattr(self.aligner, "_spot_basis", None)
        if basis is not None:
            metadata["spot_basis"] = basis
        valid = np.flatnonzero(np.isfinite(coordinates).all(axis=1))
        if not len(valid):
            raise ValueError(f"No finite display coordinates in {filename}")
        # Reorder delivery for mesh culling, preserving every original center and row ID.
        xy = coordinates[valid]
        tiles = np.floor(
            (xy - xy.min(axis=0)) / (np.maximum(spot_size, 1e-6) * 32)
        ).astype(np.int64)
        order = valid[np.lexsort((tiles[:, 1], tiles[:, 0]))]
        metadata["spot_count"] = len(order)
        return (
            metadata,
            pack_spots(coordinates[order], classes[order], foreground[order], order),
            coordinates,
            np.array([1.0, 1.0]),
        )

    def _prepare_exact_spot_data(
        self, filename: str, modality_name: str
    ) -> PreparedModality:
        """Microgrid: foreground spots only, at native positions, in a single class."""
        coordinates, spot_size, foreground, _, _ = self._load_anndata_spots(filename)
        foreground_only = bool(foreground.any())
        indices = (
            np.flatnonzero(foreground)
            if foreground_only
            else np.arange(len(coordinates))
        )
        colors = cluster_colors(["0"])
        metadata: ModalityMetadata = {
            "modality_type": "SPOT",
            "modality_name": modality_name,
            "spot_size": spot_size.tolist(),
            "foreground_only": foreground_only,
            "cluster_labels": ["0"],
            "color_map": colors,
            "spot_encoding": "FSP1",
            "spot_count": len(indices),
        }
        payload = pack_spots(
            coordinates[indices],
            np.zeros(len(indices), dtype=np.uint32),
            foreground[indices],
            indices,
        )
        return metadata, payload, coordinates, np.array([1.0, 1.0])

    def _prepare_modality_data(
        self, filename: str, modality_name: str, modality_type: str
    ) -> PreparedModality:
        """Dispatch to the appropriate loader based on modality type.

        Always returns a 4-tuple (metadata, payload, full_coordinates, scale_factors).
        full_coordinates is None for IMAGE modalities.
        """
        if modality_type in _IMAGE_MODALITIES:
            return self._prepare_image_data(filename, modality_name)
        elif modality_type in _SPOT_MODALITIES:
            return self._prepare_spot_data(filename, modality_name)
        else:
            raise ValueError(f"Unsupported modality type: {modality_type}")
