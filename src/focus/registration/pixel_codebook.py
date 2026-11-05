"""Dataset-level pixel phenotype codebooks of an image modality, learned inside anchor footprints.

Pixels are grouped by channel signature (channel names and wavenumbers): images with different
channels never share a codebook. Every sample contributes pixels whose centres lie inside its
anchor footprints, drawn at random up to ``SAMPLE_PIXELS`` per group over the dataset, and, for
unmixing, spectra averaged over cellular-scale bins for the rank. The samples live in memory only.
The codebooks are stored next to the merged registration file with the fingerprint of their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from typing import Any, cast

import numpy as np

from focus.io.ome import read_image_type, read_ome_physical_size_um
from focus.registration.anchor import AnchorArrays
from focus.registration.pixel_readout import (
    RamanTarget,
    footprint_crop,
    image_dims,
    image_geometry,
    load_crop,
    tiles,
)
from focus.spatial import phenotypes as phenotypes, pixel_clusters, spectral
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import pixel_footprint_membership
from focus.spatial.overlap import anchor_areas

ALGORITHM = "pixel-phenotypes-1"
SAMPLE_PIXELS = 200_000
TILE_PX = 1024
_HALO = int(math.ceil(4 * pixel_clusters.BLUR_SIGMA_PX))


def image_model(filename: str) -> str:
    """Phenotype model of a preprocessed image file: Pixie for fluorescence, else unmixing."""
    image_type = read_image_type(filename)
    if image_type == "brightfield":
        raise ValueError(
            "pixel_composition describes multichannel fluorescence or hyperspectral images; "
            "use feature_extraction for brightfield images"
        )
    return (
        phenotypes.PIXEL_CLUSTERS
        if image_type == "fluorescence"
        else phenotypes.UNMIXING
    )


def group_key(target: RamanTarget) -> str:
    """Channel signature of an image."""
    return json.dumps([target.channels, target.wavenumbers])


def group_label(target: RamanTarget) -> str:
    """Readable name of an image's channel group."""
    n = len(target.channels)
    if target.wavenumbers:
        lo, hi = target.wavenumbers[0], target.wavenumbers[-1]
        return f"{lo:g}-{hi:g} cm-1, {n} channels"
    return f"{n} channels"


def read_tile(
    filename: str,
    target: RamanTarget,
    tile: tuple[int, int, int, int],
    shape: tuple[int, int],
    blur: bool,
) -> np.ndarray:
    """``(tile pixels, C)`` values of one tile, smoothed with a halo when ``blur``."""
    c0, r0, c1, r1 = tile
    if not blur:
        planes = load_crop(filename, target.series, tile)
        return planes.reshape(planes.shape[0], -1).T
    h, w = shape
    outer = (
        max(0, c0 - _HALO),
        max(0, r0 - _HALO),
        min(w, c1 + _HALO),
        min(h, r1 + _HALO),
    )
    planes = pixel_clusters.blur(load_crop(filename, target.series, outer))
    inner = planes[:, r0 - outer[1] : r1 - outer[1], c0 - outer[0] : c1 - outer[0]]
    return inner.reshape(inner.shape[0], -1).T


@dataclass
class GroupSample:
    """Pixels drawn for one channel group, and its cellular-scale bin spectra."""

    label: str
    channels: list[str]
    model: str
    pixels: list[np.ndarray] = field(default_factory=list)
    binned: list[np.ndarray] = field(default_factory=list)


def _bin_factor(filename: str, target: RamanTarget) -> int:
    """Pixels per cellular-scale bin side in one image (1 without a physical pixel size)."""
    size = read_ome_physical_size_um(filename)
    if size is None:
        return 1
    pixel = float(np.mean(size)) * float(np.mean(target.placement[2:]))
    return max(1, int(round(spectral.CELLULAR_BIN_UM / pixel)))


def _binned_spectra(
    values: np.ndarray, tile: tuple[int, int, int, int], factor: int
) -> np.ndarray:
    """``(bins, C)`` finite cellular-scale bin spectra of one tile's ``(pixels, C)`` values."""
    c0, r0, c1, r1 = tile
    planes = values.T.reshape(values.shape[1], r1 - r0, c1 - c0)
    binned = np.stack([spectral.nan_block_mean(p, factor) for p in planes])
    spectra = binned.reshape(binned.shape[0], -1).T
    return cast(np.ndarray, spectra[np.isfinite(spectra).all(axis=1)])


def sample_image(
    filename: str,
    target: RamanTarget,
    geom: AnchorGeometry,
    group: GroupSample,
    budget: int,
    rng: np.random.Generator,
) -> None:
    """Add the pixels of one image drawn inside its footprints (and its bin spectra) to ``group``."""
    shape = image_dims(filename, target.series)
    crop = footprint_crop(geom, *shape)
    if crop is None:
        return
    rate = min(1.0, budget / max(float(anchor_areas(geom).sum()), 1.0))
    factor = _bin_factor(filename, target) if group.model == phenotypes.UNMIXING else 0
    crop_area = (crop[2] - crop[0]) * (crop[3] - crop[1])
    bin_rate = min(1.0, budget * max(factor, 1) ** 2 / max(crop_area, 1))
    for tile in tiles(crop, TILE_PX, max(factor, 1)):
        values = read_tile(
            filename, target, tile, shape, group.model == phenotypes.PIXEL_CLUSTERS
        )
        inside = (
            np.diff(pixel_footprint_membership(geom, tile, "ones").tocsc().indptr) > 0
        )
        keep = (
            inside & np.isfinite(values).all(axis=1) & (rng.random(len(values)) < rate)
        )
        group.pixels.append(values[keep])
        if factor:
            spectra = _binned_spectra(values, tile, factor)
            group.binned.append(spectra[rng.random(len(spectra)) < bin_rate])


def fit_group(group: GroupSample, rng: np.random.Generator) -> phenotypes.PixelCodebook:
    """Codebook of one sampled group (mean only below ``MIN_CHANNELS`` channels)."""
    if len(group.channels) < phenotypes.MIN_CHANNELS:
        return phenotypes.PixelCodebook(
            group.label, group.channels, phenotypes.MEAN_ONLY
        )
    pixels = np.concatenate(group.pixels) if group.pixels else np.zeros((0, 0))
    if len(pixels) > SAMPLE_PIXELS:
        pixels = pixels[np.sort(rng.choice(len(pixels), SAMPLE_PIXELS, replace=False))]
    if len(pixels) <= len(group.channels):
        raise ValueError(
            f"'{group.label}': too few measured pixels inside the footprints"
        )
    if group.model == phenotypes.PIXEL_CLUSTERS:
        return phenotypes.fit_clusters(group.label, group.channels, pixels)
    return phenotypes.fit_unmixing(
        group.label, group.channels, pixels, np.concatenate(group.binned)
    )


def codebook_file(merged_registration: str) -> str:
    """Path of the codebook stored next to a merged registration file."""
    root = merged_registration.removesuffix(".h5ad")
    return f"{root}_pixel_phenotypes.npz"


def save_codebooks(
    path: str, key: str, codebooks: dict[str, phenotypes.PixelCodebook]
) -> None:
    """Write the codebooks of every group and their input fingerprint."""
    arrays: dict[str, np.ndarray] = {}
    meta = []
    for index, (group, book) in enumerate(codebooks.items()):
        arrays.update(phenotypes.to_arrays(book, f"{index}_"))
        meta.append(
            {
                "key": group,
                "group": book.group,
                "channels": book.channels,
                "model": book.model,
                "criterion": book.criterion,
            }
        )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps({"fingerprint": key, "groups": meta})
    stored: dict[str, Any] = {"meta": np.array(payload), **arrays}
    np.savez(path, **stored)


def load_codebooks(path: str, key: str) -> dict[str, phenotypes.PixelCodebook] | None:
    """The stored codebooks when their fingerprint is ``key``, else None."""
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=False) as stored:
        arrays = {name: stored[name] for name in stored.files}
    payload = json.loads(str(arrays.pop("meta")))
    if payload.get("fingerprint") != key:
        return None
    return {
        str(meta["key"]): phenotypes.from_arrays(meta, arrays, f"{index}_")
        for index, meta in enumerate(payload["groups"])
    }


def sample_file(
    filename: str,
    targets: list[RamanTarget],
    anchor: AnchorArrays,
    geom: AnchorGeometry,
    groups: dict[str, GroupSample],
    budget: int,
    rng: np.random.Generator,
) -> None:
    """Draw the pixels of every image of one sample into its channel group."""
    model = image_model(filename)
    for target in targets:
        key = group_key(target)
        if key not in groups:
            groups[key] = GroupSample(group_label(target), list(target.channels), model)
        if len(target.channels) >= phenotypes.MIN_CHANNELS:
            image_geom = image_geometry(anchor, geom, target)
            sample_image(filename, target, image_geom, groups[key], budget, rng)
