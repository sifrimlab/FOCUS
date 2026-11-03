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
    acquisition_basis: list[list[float]] | None
    channels: list[dict[str, object]]


class AlignedSpot(TypedDict, total=False):
    """Legacy per-spot browser coordinates retained by the confirmation reader."""

    id: int
    pixel_x: float | None
    pixel_y: float | None


class AlignmentResult(TypedDict, total=False):
    """Unchanged fields POSTed by the alignment frontend."""

    transform_matrix: list[float]
    spots: list[AlignedSpot]
    corner_pixels: list[list[float]]


# An image payload is a PIL image, or an ImageView that renders its channels on request.
AlignmentPayload: TypeAlias = (
    bytes | list[dict[str, object]] | Image.Image | object | None
)
PreparedModality: TypeAlias = tuple[
    ModalityMetadata, AlignmentPayload, np.ndarray | None, np.ndarray
]
