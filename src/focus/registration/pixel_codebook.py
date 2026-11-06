"""Dataset-level pixel phenotype codebooks of an image modality, learned inside anchor footprints.

Pixels are grouped by channel signature (channel names and wavenumbers): images with different
channels never share a codebook. Every sample contributes, from inside its anchor footprints:

- unmixing (Raman): spectra averaged over spot-sized blocks (side the square root of the median
  footprint area, at least half inside the footprints), with the image's spot-level offset removal
  and normalisation, up to ``SAMPLE_PIXELS`` per group over the dataset; they set the rank and the
  endmembers (archetypes), at the scale of the spots they describe;
- Pixie (fluorescence): blurred pixels, drawn the same way.

The samples live in memory only. The codebooks are stored next to the merged registration file with
the fingerprint of their inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from typing import Any, cast

import numpy as np
from scipy.ndimage import gaussian_filter

from focus.io.ome import read_image_type, read_ome_physical_size_um
from focus.registration.anchor import AnchorArrays
from focus.registration.pixel_readout import (
    RamanTarget,
    footprint_crop,
    image_dims,
    image_geometry,
    load_crop,
    placement_scale,
    tiles,
)
from focus.spatial import phenotypes as phenotypes, pixel_clusters, spectral
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import pixel_footprint_membership
from focus.spatial.overlap import anchor_areas

ALGORITHM = "pixel-phenotypes-2"
SAMPLE_PIXELS = 200_000
TILE_PX = 1024
_HALO = int(math.ceil(4 * pixel_clusters.BLUR_SIGMA_PX))
# Label maps of unmixing groups: the offset and normaliser of each pixel come from a copy of the
# tile smoothed with this Gaussian (µm), so the per-pixel noise does not bias the spectral shape.
LABEL_SMOOTH_UM = 2.0
# Unmixing of area-normalised spectra: blocks whose signal above the offset is below this percentile
# of the drawn blocks (nearly empty: glass, holes) are left out of the endmember extraction, since
# their shape is undefined.
SIGNAL_FLOOR_PERCENTILE = 5.0


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


def read_unmixing_tile(
    filename: str,
    target: RamanTarget,
    tile: tuple[int, int, int, int],
    shape: tuple[int, int],
    sigma_px: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Linear ``(tile pixels, C)`` values of one tile and their label-map spectra.

    The label-map spectra get the image's spot-level offset removal and normalisation, with the
    offset and the normaliser of each pixel taken from the tile smoothed by ``sigma_px`` (a halo
    of ``4 sigma`` is read around the tile).
    """
    c0, r0, c1, r1 = tile
    h, w = shape
    halo = int(math.ceil(4 * sigma_px))
    outer = (max(0, c0 - halo), max(0, r0 - halo), min(w, c1 + halo), min(h, r1 + halo))
    planes = load_crop(filename, target.series, outer)
    inner = (
        slice(None),
        slice(r0 - outer[1], r1 - outer[1]),
        slice(c0 - outer[0], c1 - outer[0]),
    )
    values = planes[inner].reshape(planes.shape[0], -1).T
    finite = np.isfinite(planes)
    smooth = np.empty_like(planes)
    weight = gaussian_filter(finite.all(axis=0).astype(np.float32), sigma_px)
    for c, plane in enumerate(planes):
        smooth[c] = gaussian_filter(np.where(finite.all(axis=0), plane, 0.0), sigma_px)
    with np.errstate(invalid="ignore", divide="ignore"):
        smooth /= np.where(weight > 1e-3, weight, np.nan)
    reference = smooth[inner].reshape(planes.shape[0], -1).T
    labels = values.astype(np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        if target.spot_offset == spectral.MIN_OFFSET:
            base = np.min(reference, axis=1, keepdims=True)
            labels = labels - base
            reference = reference - base
        if target.spot_normalization == spectral.AREA:
            level = np.mean(reference, axis=1, keepdims=True)
            labels = labels / np.where(level > 0, level, np.nan)
    return values, labels


@dataclass
class GroupSample:
    """Pixels drawn for one channel group (Pixie), or its processed spot-scale blocks (unmixing)."""

    label: str
    channels: list[str]
    model: str
    pixels: list[np.ndarray] = field(default_factory=list)
    binned: list[np.ndarray] = field(default_factory=list)
    levels: list[np.ndarray] = field(
        default_factory=list
    )  # bin signal above the offset


def image_pixel_um(filename: str, target: RamanTarget) -> float | None:
    """Pixel size (µm) of one image of a file: image 0's times the placement scale."""
    size = read_ome_physical_size_um(filename)
    if size is None:
        return None
    return float(np.mean(size)) * placement_scale(target.placement)


def label_sigma_px(filename: str, target: RamanTarget) -> float:
    """``LABEL_SMOOTH_UM`` in pixels of one image (2 px without a physical pixel size)."""
    pixel = image_pixel_um(filename, target)
    return 2.0 if pixel is None else LABEL_SMOOTH_UM / pixel


def spot_block_px(geom: AnchorGeometry) -> int:
    """Side (pixels) of a square of the median footprint area in one image."""
    areas = anchor_areas(geom)
    areas = areas[np.isfinite(areas) & (areas > 0)]
    return max(1, int(round(float(np.sqrt(np.median(areas)))))) if len(areas) else 1


def _binned_spectra(
    values: np.ndarray,
    inside: np.ndarray,
    tile: tuple[int, int, int, int],
    factor: int,
) -> np.ndarray:
    """``(blocks, C)`` finite block spectra of one tile, for blocks mostly inside the footprints."""
    c0, r0, c1, r1 = tile
    planes = values.T.reshape(values.shape[1], r1 - r0, c1 - c0)
    binned = np.stack([spectral.nan_block_mean(p, factor) for p in planes])
    within = spectral.nan_block_mean(
        inside.reshape(r1 - r0, c1 - c0).astype(np.float32), factor
    )
    spectra = binned.reshape(binned.shape[0], -1).T
    keep = np.isfinite(spectra).all(axis=1) & (within.ravel() >= 0.5)
    return cast(np.ndarray, spectra[keep])


def sample_image(
    filename: str,
    target: RamanTarget,
    geom: AnchorGeometry,
    group: GroupSample,
    budget: int,
    rng: np.random.Generator,
) -> None:
    """Add what one image contributes to ``group`` from inside its footprints.

    Unmixing: processed spot-sized blocks (about ``budget`` of them); Pixie: blurred pixels.
    """
    shape = image_dims(filename, target.series)
    crop = footprint_crop(geom, *shape)
    if crop is None:
        return
    area = max(float(anchor_areas(geom).sum()), 1.0)
    unmixing = group.model == phenotypes.UNMIXING
    factor = spot_block_px(geom) if unmixing else 1
    rate = min(1.0, budget * factor**2 / area)
    for tile in tiles(crop, TILE_PX, factor):
        values = read_tile(filename, target, tile, shape, not unmixing)
        inside = (
            np.diff(pixel_footprint_membership(geom, tile, "ones").tocsc().indptr) > 0
        )
        if unmixing:
            linear = _binned_spectra(values, inside, tile, factor)
            bins = target.spot_spectra(linear)
            ok = np.isfinite(bins).all(axis=1) & (rng.random(len(bins)) < rate)
            group.binned.append(bins[ok])
            if target.spot_normalization == spectral.AREA:
                level = spectral.spot_spectra(
                    linear, target.spot_offset, spectral.NO_NORMALIZATION
                ).mean(axis=1)
                group.levels.append(level[ok])
            continue
        keep = (
            inside & np.isfinite(values).all(axis=1) & (rng.random(len(values)) < rate)
        )
        group.pixels.append(values[keep])


def fit_group(group: GroupSample, rng: np.random.Generator) -> phenotypes.PixelCodebook:
    """Codebook of one sampled group (mean only below ``MIN_CHANNELS`` channels)."""
    if len(group.channels) < phenotypes.MIN_CHANNELS:
        return phenotypes.PixelCodebook(
            group.label, group.channels, phenotypes.MEAN_ONLY
        )
    drawn = group.binned if group.model == phenotypes.UNMIXING else group.pixels
    samples = np.concatenate(drawn) if drawn else np.zeros((0, 0))
    if group.model == phenotypes.UNMIXING and group.levels:
        levels = np.concatenate(group.levels)
        if len(levels) == len(samples) and len(levels):
            samples = samples[levels >= np.percentile(levels, SIGNAL_FLOOR_PERCENTILE)]
    if len(samples) > SAMPLE_PIXELS:
        samples = samples[
            np.sort(rng.choice(len(samples), SAMPLE_PIXELS, replace=False))
        ]
    if len(samples) <= len(group.channels):
        raise ValueError(
            f"'{group.label}': too few measured pixels inside the footprints"
        )
    if group.model == phenotypes.PIXEL_CLUSTERS:
        return phenotypes.fit_clusters(group.label, group.channels, samples)
    return phenotypes.fit_unmixing(group.label, group.channels, samples)


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
