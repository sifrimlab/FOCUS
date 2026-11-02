"""Controlled Leica planes and scan metadata for original/refactored reader comparisons."""

from collections.abc import Callable
from typing import TypeVar

import numpy as np

MetadataT = TypeVar("MetadataT")
PlaneCall = tuple[int, tuple[int, int], int, tuple[tuple[int, int], ...]]


class LifPlanes:
    """Expose deterministic tiled planes and record every requested scan dimension."""

    def __init__(self) -> None:
        """Initialize the ordered list of plane-read requests."""
        self.calls: list[PlaneCall] = []

    def get_image(self, index: int) -> "ScanPlanes":
        """Return the requested scan without loading a real Leica file."""
        return ScanPlanes(index, self.calls)


class ScanPlanes:
    """Provide a readlif-compatible plane operation for one synthetic scan."""

    def __init__(self, index: int, calls: list[PlaneCall]) -> None:
        """Retain the scan identifier and shared request history."""
        self.index, self.calls = index, calls

    def get_plane(
        self, display_dims: tuple[int, int], c: int, requested_dims: dict[int, int]
    ) -> np.ndarray:
        """Return a uint8 (3, 3) plane encoding scan, tile and spectral indices."""
        self.calls.append(
            (self.index, display_dims, c, tuple(sorted(requested_dims.items())))
        )
        value = self.index * 80 + requested_dims[10] * 10 + requested_dims[9]
        return np.full((3, 3), value, dtype=np.uint8)


def scan_metadata(factory: Callable[[], MetadataT]) -> dict[str, MetadataT]:
    """Build two overlapping eight-channel scans with two tiles and known µm coordinates."""
    result: dict[str, MetadataT] = {}
    for index, name in enumerate(("first", "second")):
        metadata = factory()
        fields: dict[str, object] = {
            "name": name,
            "index": index,
            "tile_number": 2,
            "lambda_steps": 8,
            "scan_width": 3,
            "scan_height": 3,
            "lambda_begin": 700.0 + 50 * index,
            "lambda_end": 800.0 + 50 * index,
            "lambda_stokes": 1064.0,
            "tiles_coordinates": np.array([[0, 0], [1, 2]], dtype=np.float32) + index,
            "pixel_size": np.array([0.5, 0.75], dtype=np.float32),
        }
        for field, value in fields.items():
            setattr(metadata, field, value)
        result[name] = metadata
    return result
