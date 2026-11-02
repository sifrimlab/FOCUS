"""Raman metadata values with the original defaults, coercion, and validation."""

from functools import partial

import numpy as np

from focus.preprocessing.raman.fields import (
    MetadataField,
    array,
    integer,
    positive_float,
    text,
)


class RamanMetadata:
    """Store the metadata of one Raman image or tile collection.

    Fields remain ``None`` until assigned. Wavelength fields accept positive
    Python floats or integers; coordinate arrays are copied to float32.
    """

    name: MetadataField[str] = MetadataField(partial(text, label="Name"))
    index: MetadataField[int] = MetadataField(
        partial(integer, label="Index", minimum=0, requirement="a non-negative integer")
    )
    lambda_steps: MetadataField[int] = MetadataField(
        partial(
            integer, label="Lambda steps", minimum=1, requirement="a positive integer"
        )
    )
    lambda_begin: MetadataField[float] = MetadataField(
        partial(positive_float, label="Lambda begin")
    )
    lambda_end: MetadataField[float] = MetadataField(
        partial(positive_float, label="Lambda end")
    )
    scan_height: MetadataField[int] = MetadataField(
        partial(
            integer,
            label="Scan height",
            minimum=1,
            requirement="a positive number",
            coerce=True,
        )
    )
    scan_width: MetadataField[int] = MetadataField(
        partial(
            integer,
            label="Scan width",
            minimum=1,
            requirement="a positive number",
            coerce=True,
        )
    )
    laser_type: MetadataField[str] = MetadataField(partial(text, label="Laser type"))
    lambda_stokes: MetadataField[float] = MetadataField(
        partial(positive_float, label="Lambda stokes")
    )
    tile_number: MetadataField[int] = MetadataField(
        partial(
            integer, label="Tile number", minimum=1, requirement="a positive integer"
        )
    )
    tiles_coordinates: MetadataField[np.ndarray] = MetadataField(
        partial(
            array,
            label="Tiles coordinates",
            ndim=2,
            axis=1,
            description="a 2D array with shape (N, 2)",
        )
    )
    pixel_size: MetadataField[np.ndarray] = MetadataField(
        partial(
            array,
            label="Pixel size",
            ndim=1,
            axis=0,
            description="a 1D array with shape (2,)",
        )
    )

    def __init__(self) -> None:
        """Initialize the same backing fields, in their original order."""
        self._name: str | None = None
        self._index: int | None = None
        self._lambda_steps: int | None = None
        self._lambda_begin: float | None = None
        self._lambda_end: float | None = None
        self._scan_height: int | None = None
        self._scan_width: int | None = None
        self._laser_type: str | None = None
        self._lambda_stokes: float | None = None
        self._tile_number: int | None = None
        self._tiles_coordinates: np.ndarray | None = None
        self._pixel_size: np.ndarray | None = None
