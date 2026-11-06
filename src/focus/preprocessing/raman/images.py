"""Second pass over one acquisition: assemble every channel, process the spectra, stage the pyramid.

The assembled mosaic is staged as the native detector samples (no correction yet, losslessly), with
one flat-field mosaic beside it. Offset-aware flat-field correction (``(raw - offset) / profile``),
denoising and quantisation then run block by block into the float32 level 0 that is written: linear
intensities. Offset removal and normalisation are recorded for registration, which applies them to
averaged spectra.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

import numpy as np
import zarr
from zarr.codecs import BloscCodec

from focus.io import ome_write
from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman import spectra
from focus.preprocessing.raman.mosaic import Prepared
from focus.spatial.spectral import nan_block_mean as _nan_downscale
from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.preprocessing.raman.placement import Placement
from focus.config.options import RamanNormalization
from focus.runtime.reporting import StepReporter
from focus.spatial import spectral

_BLOCK = 2 * ome_write.TILE


@dataclass
class BuiltImage:
    """A staged image with its OME channels and per-image FOCUS annotations."""

    staged: ome_write.StagedImage
    name: str
    channels: list[ome_write.ChannelInfo]
    annotations: dict[str, object]
    rank: int | None


def channel_infos(prepared: Prepared) -> list[ome_write.ChannelInfo]:
    """One OME channel per wavenumber; the pump wavelength is the excitation wavelength."""
    return [
        ome_write.ChannelInfo(
            name=f"{wn:.1f} cm-1",
            excitation_nm=float(pump),
            acquisition_mode="SpectralImaging",
        )
        for wn, pump in zip(prepared.stack.wavenumbers, prepared.stack.pump_nm)
    ]


def _float_array(path: str, shape: tuple[int, ...]) -> zarr.Array[Any]:
    return zarr.create_array(
        store=path,
        shape=shape,
        chunks=(1, ome_write.TILE, ome_write.TILE),
        dtype=np.float32,
        fill_value=np.nan,
        compressors=BloscCodec(cname="zstd", clevel=3, shuffle="shuffle", typesize=4),
        overwrite=True,
    )


def _assemble_native(prepared: Prepared, channel: int) -> np.ndarray:
    """Native samples of one channel on the mosaic (0 where no tile measured)."""
    assert prepared.registration is not None
    rows, cols = prepared.shape
    h, w = prepared.stack.tile_shape
    out = np.zeros((rows, cols), dtype=prepared.stack.blocks[0].dtype)
    for k, (x, y) in enumerate(prepared.registration.positions):
        np.copyto(
            out[y : y + h, x : x + w],
            prepared.stack.plane(k, channel),
            where=prepared.masks[k],
        )
    return out


def image_annotations(
    prepared: Prepared, placement: Placement, settings: RamanRunSettings
) -> dict[str, object]:
    """Per-image FOCUS annotations known before the spectra are processed."""
    first = prepared.acquisition.first
    assert prepared.flat is not None and prepared.registration is not None
    low, high = prepared.flat.span
    values: dict[str, object] = {
        "focus_acquisition": prepared.acquisition.name,
        "focus_lif_elements": json.dumps(
            [e.path for e in prepared.acquisition.elements]
        ),
        "focus_wavenumbers_cm1": json.dumps(
            [round(float(v), 2) for v in prepared.stack.wavenumbers]
        ),
        "focus_pump_nm": json.dumps(
            [round(float(v), 4) for v in prepared.stack.pump_nm]
        ),
        "focus_stokes_nm": f"{first.stokes_nm:g}",
        "focus_detector_gain": "" if first.gain is None else f"{first.gain:g}",
        "focus_line_average": ""
        if first.line_average is None
        else str(first.line_average),
        "focus_laser_power_w": ""
        if first.laser_power_w is None
        else f"{first.laser_power_w:g}",
        "focus_bit_depth": str(first.bits),
        "focus_flatfield": f"{prepared.flat.source}; illumination {low:.2f} to {high:.2f}"
        + (
            f"; {prepared.flat.offset_fit.describe()}"
            if prepared.flat.offset_fit is not None
            else ""
        ),
        "focus_detector_offset": f"{prepared.flat.offset:.4g}",
        "focus_flatfield_check": prepared.check.describe() if prepared.check else "",
        "focus_tile_registration": prepared.registration.describe(),
        "focus_placement": placement.annotation(),
        "focus_placement_method": placement.describe(),
        "focus_superseded": json.dumps(prepared.acquisition.superseded),
        # The stored pixels are linear; averaged spectra get the offset and normalisation below.
        "focus_offset_removed": "False",
        "focus_normalization": "none",
        "focus_spot_offset": spectral.MIN_OFFSET
        if settings.remove_offset and prepared.stack.channels > 1
        else spectral.NO_OFFSET,
        "focus_spot_normalization": spectral.AREA
        if settings.normalization == RamanNormalization.AREA
        and prepared.stack.channels > 1
        else spectral.NO_NORMALIZATION,
        "focus_quantization_step": f"{spectra.quantization_step(settings, prepared.stack.channels):g}",
    }
    return values


def build(
    prepared: Prepared,
    placement: Placement,
    settings: RamanRunSettings,
    workdir: str,
    prefix: str,
    reporter: StepReporter,
) -> BuiltImage:
    """Assemble, process and stage one acquisition as an OME image."""
    assert prepared.flat is not None and prepared.registration is not None
    stack = prepared.stack
    rows, cols = prepared.shape
    channels = stack.channels
    pixel = float(np.mean(prepared.pixel_um))
    h, w = stack.tile_shape
    positions = prepared.registration.positions
    offset = np.float32(prepared.flat.offset)

    flat_mosaic = np.full((rows, cols), np.nan, dtype=np.float32)
    for k, (x, y) in enumerate(positions):
        np.copyto(
            flat_mosaic[y : y + h, x : x + w],
            prepared.flat.profile,
            where=prepared.masks[k],
        )
    covered = np.isfinite(flat_mosaic)
    covered_count = int(covered.sum())

    native = zarr.open_array(
        os.path.join(workdir, f"{prefix}native.zarr"),
        mode="w",
        shape=(channels, rows, cols),
        chunks=(1, ome_write.TILE, ome_write.TILE),
        dtype=stack.blocks[0].dtype,
        fill_value=0,
    )
    bin_factor = max(1, int(round(tech.RANK_BIN_UM / pixel)))
    binned = []
    saturated_bins = None
    saturated = []
    for c in range(channels):
        plane = _assemble_native(prepared, c)
        native[c] = plane
        clipped = (plane >= stack.saturation) & covered
        saturated.append(float(clipped.sum()) / max(covered_count, 1))
        if channels > 1 and settings.denoise:
            corrected = (plane.astype(np.float32) - offset) / flat_mosaic
            binned.append(_nan_downscale(corrected, bin_factor))
            flags = _nan_downscale(clipped.astype(np.float32), bin_factor) > 0
            saturated_bins = (
                flags if saturated_bins is None else (saturated_bins | flags)
            )
    worst = max(saturated) if saturated else 0.0
    if worst > tech.SATURATION_WARNING:
        reporter.warning(
            f"'{prepared.acquisition.name}': up to {100 * worst:.1f}% of the samples of a channel "
            "are saturated (kept as measured)"
        )

    model = None
    if binned:
        cube = np.stack(binned).reshape(channels, -1).T
        if saturated_bins is not None:
            cube = cube[~saturated_bins.ravel()]
        model = spectra.fit(cube)
        del cube
    del binned

    level0 = _float_array(
        os.path.join(workdir, f"{prefix}0.zarr"), (channels, rows, cols)
    )
    for y in range(0, rows, _BLOCK):
        for x in range(0, cols, _BLOCK):
            raw = np.asarray(native[:, y : y + _BLOCK, x : x + _BLOCK]).astype(
                np.float32
            )
            block = (raw - offset) / flat_mosaic[y : y + _BLOCK, x : x + _BLOCK]
            level0[:, y : y + _BLOCK, x : x + _BLOCK] = spectra.process_block(
                block, model, settings
            )
    ome_write.remove_workdir(os.path.join(workdir, f"{prefix}native.zarr"))
    levels = ome_write.levels_from(
        level0, False, workdir, prefix, spectra.quantization_step(settings, channels)
    )

    values = image_annotations(prepared, placement, settings)
    values["focus_saturated_fraction"] = json.dumps([round(v, 6) for v in saturated])
    if model is not None:
        values["focus_spectral_rank"] = str(model.rank)
        values["focus_spectral_rank_bin_um"] = f"{bin_factor * pixel:.2f}"
    else:
        values["focus_spectral_rank"] = "not applied"
    return BuiltImage(
        staged=ome_write.StagedImage(
            levels, False, np.asarray(prepared.pixel_um, dtype=np.float64)
        ),
        name=prepared.acquisition.name,
        channels=channel_infos(prepared),
        annotations=values,
        rank=None if model is None else model.rank,
    )
