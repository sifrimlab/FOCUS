"""Existing alignment metadata, native payloads, and browser confirmation shapes."""

from typing import TypeAlias, TypedDict

import numpy as np
from PIL import Image


class ModalityMetadata(TypedDict, total=False):
    """Metadata accompanying an image or a native spot payload."""

    modality_type: str
    modality_name: str
    image_shape: list[int]
    spot_size: list[float]
    foreground_only: bool
    cluster_labels: list[str]
    color_map: dict[str, str]
    spot_encoding: str
    spot_count: int
    spot_basis: list[list[float]] | None
    spot_shape: str
    acquisition_basis: list[list[float]] | None
    channels: list[dict[str, object]]
    # Spot modalities clustered with a merge tree: classes are the fine clusters; a resolution of L
    # clusters applies the first (n_fine - L) merges, pairs of fine labels.
    cluster_tree: dict[str, object]
    # Display labels of the feature images appended to an FSP version 2 payload.
    feature_images: list[str]


class AlignedSpot(TypedDict, total=False):
    """Legacy per-spot browser coordinates retained by the confirmation reader."""

    id: int
    pixel_x: float | None
    pixel_y: float | None


class LandmarkPairs(TypedDict):
    """Landmarks: ``src`` on the moving layer (native), ``dst`` on the fixed layer (display)."""

    src: list[list[float]]
    dst: list[list[float]]


class ExclusionPolygons(TypedDict, total=False):
    """Excluded regions, each polygon in its own layer's frame (moving native / fixed display)."""

    moving: list[list[list[float]]]
    fixed: list[list[list[float]]]


class AlignmentResult(TypedDict, total=False):
    """Fields POSTed by the alignment frontend; the landmark fields are optional."""

    transform_matrix: list[float]
    spots: list[AlignedSpot]
    corner_pixels: list[list[float]]
    landmarks: LandmarkPairs
    smoothing: float
    exclusions: ExclusionPolygons


# An image payload is a PIL image, or an ImageView that renders its channels on request.
AlignmentPayload: TypeAlias = (
    bytes | list[dict[str, object]] | Image.Image | object | None
)
PreparedModality: TypeAlias = tuple[
    ModalityMetadata, AlignmentPayload, np.ndarray | None, np.ndarray
]
