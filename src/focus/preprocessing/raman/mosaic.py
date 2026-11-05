"""First pass over one acquisition: flat-field, tile registration and low-resolution views."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from focus.preprocessing.raman import flatfield, stitching
from focus.preprocessing.raman.inventory import Acquisition
from focus.preprocessing.raman.reader import TileStack
from focus.spatial.spectral import nan_block_mean as _nan_downscale


@dataclass
class Prepared:
    """Everything known about an acquisition before its channels are assembled.

    ``origin_um`` is the stage position (µm) of mosaic pixel (0, 0); ``views`` maps a downscale
    factor to the channel-mean mosaic at that factor (NaN where no tile measured).
    """

    acquisition: Acquisition
    stack: TileStack
    pixel_um: tuple[float, float]
    tile_means: np.ndarray
    flat: flatfield.FlatField | None
    check: flatfield.OverlapCheck | None
    registration: stitching.Registration | None
    masks: list[np.ndarray]
    origin_um: np.ndarray
    views: dict[int, np.ndarray]

    @property
    def shape(self) -> tuple[int, int]:
        """``(rows, columns)`` of the mosaic."""
        assert self.registration is not None
        return stitching.mosaic_shape(
            self.registration.positions, self.stack.tile_shape
        )

    @property
    def mean_wavenumber(self) -> float:
        """Centre of the acquisition's spectral range, cm-1."""
        return float(np.mean(self.stack.wavenumbers))


def read_tile_means(acquisition: Acquisition, stack: TileStack) -> Prepared:
    """Channel-mean tiles (saturated samples ignored), before any correction."""
    first = acquisition.first
    means = np.stack(
        [
            flatfield.channel_mean(stack.tile(t), stack.saturation)
            for t in range(stack.tiles)
        ]
    )
    return Prepared(
        acquisition=acquisition,
        stack=stack,
        pixel_um=first.pixel_um,
        tile_means=means,
        flat=None,
        check=None,
        registration=None,
        masks=[],
        origin_um=np.zeros(2),
        views={},
    )


def own_flatfield(prepared: Prepared) -> flatfield.FlatField | None:
    """The acquisition's own flat-field, or None when it has too few tiles to estimate one."""
    if prepared.stack.tiles < flatfield.MIN_TILES:
        return None
    profile = flatfield.estimate(prepared.tile_means, float(prepared.pixel_um[0]))
    return flatfield.FlatField(profile, "estimated from this acquisition")


def register_and_view(
    prepared: Prepared, flat: flatfield.FlatField, factors: tuple[int, ...]
) -> None:
    """Register the tiles on flat-field corrected channel means, then build the low-res views."""
    prepared.flat = flat
    corrected = prepared.tile_means / flat.profile
    stage_px = stitching.stage_pixels(prepared.acquisition.stage_um, prepared.pixel_um)
    registration = stitching.register(
        lambda i: corrected[i],
        stage_px,
        prepared.stack.tile_shape,
        float(np.mean(prepared.pixel_um)),
    )
    prepared.registration = registration
    prepared.check = flatfield.overlap_check(
        prepared.tile_means,
        registration.positions.astype(np.float64),
        registration.pairs,
        flat.profile,
    )
    prepared.masks = stitching.ownership(
        registration.positions, prepared.stack.tile_shape
    )
    # Stage position of mosaic pixel (0, 0): the mean over tiles, so the scale difference between
    # the stage and the scanner averages out.
    prepared.origin_um = (
        prepared.acquisition.stage_um
        - registration.positions * np.asarray(prepared.pixel_um, dtype=np.float64)
    ).mean(axis=0)
    mosaic = stitching.assemble_channel(
        lambda k: corrected[k],
        registration.positions,
        prepared.masks,
        prepared.stack.tile_shape,
    )
    prepared.views = {f: _nan_downscale(mosaic, f) for f in factors}
