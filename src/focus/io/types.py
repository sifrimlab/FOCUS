"""Structural interfaces for image levels returned by external TIFF readers."""

from typing import Protocol

import numpy as np


class PyramidLevel(Protocol):
    """The spatial metadata and pixel access shared by supported TIFF layouts."""

    @property
    def shape(self) -> tuple[int, ...]:
        """Return array dimensions in the level's stored axis order."""
        ...

    @property
    def axes(self) -> str | None:
        """Return stored axis labels when the reader supplies them."""
        ...

    def asarray(self) -> np.ndarray:
        """Read this stored level without resampling."""
        ...
