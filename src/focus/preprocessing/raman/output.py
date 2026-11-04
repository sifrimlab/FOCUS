"""OME description, display rendering and tissue outline of a Raman output."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.filters import threshold_otsu

from focus.io import ome_write
from focus.preprocessing.microscopy import display, tissue
from focus.preprocessing.raman import settings as tech
from focus.preprocessing.raman.images import BuiltImage


def colours(wavenumbers: Sequence[float]) -> tuple[list[str], list[bool]]:
    """Display colour and default visibility per channel.

    The channel nearest the lipid CH2 stretch (2850 cm-1) is green and the one nearest the protein
    CH3 stretch (2930 cm-1) magenta, both shown; other channels are grey and hidden. Without either
    band the middle channel is shown in grey.
    """
    values = np.asarray(wavenumbers, dtype=np.float64)
    out = [tech.OTHER_COLOUR] * len(values)
    shown = [False] * len(values)
    for band, colour in (
        (tech.LIPID_CM, tech.LIPID_COLOUR),
        (tech.PROTEIN_CM, tech.PROTEIN_COLOUR),
    ):
        distance = np.abs(values - band)
        index = int(np.argmin(distance))
        if distance[index] <= tech.BAND_TOLERANCE_CM and not shown[index]:
            out[index] = colour
            shown[index] = True
    if not any(shown):
        shown[len(values) // 2] = True
    return out, shown


def _detection_level(image: BuiltImage) -> int:
    """Stored level whose pixel size is nearest the detection canvas resolution."""
    pixel = float(np.mean(np.asarray(image.staged.pixel_size)))
    width0 = image.staged.levels[0].shape[2]
    sizes = [pixel * width0 / level.shape[2] for level in image.staged.levels]
    return int(np.argmin([abs(np.log(s / tech.DETECTION_UM)) for s in sizes]))


def tissue_mask(
    canvas: np.ndarray, measured: np.ndarray, um_per_px: float
) -> np.ndarray:
    """Tissue on a ``(H, W, C)`` detection canvas: Otsu's threshold on the smoothed channel mean.

    SRS glass has its own non-resonant level, so the measured pixels form two populations (glass,
    tissue); Otsu's threshold (Otsu 1979) separates them. The mask is then cleaned like the
    microscopy tissue mask (closing, specks, holes, debris area).
    """
    if not measured.any():
        return np.zeros(measured.shape, dtype=bool)
    mean = np.where(measured, np.nan_to_num(canvas).mean(axis=-1), 0.0).astype(
        np.float32
    )
    floor = float(np.percentile(mean[measured], 1))
    smooth = gaussian_filter(
        np.where(measured, mean, floor), tech.TISSUE_SMOOTH_UM / um_per_px
    )
    cut = float(threshold_otsu(smooth[measured]))  # type: ignore[no-untyped-call]
    raw = (smooth > cut) & measured
    mask: np.ndarray = tissue.clean_mask(raw, um_per_px, True) & measured
    return mask


def display_and_tissue(
    image: BuiltImage, wavenumbers: Sequence[float]
) -> tuple[dict[str, object], list[np.ndarray], float]:
    """``focus_display`` of image 0, its tissue outlines (level-0 pixels) and tissue fraction."""
    index = _detection_level(image)
    level = image.staged.levels[index]
    canvas = np.moveaxis(np.asarray(level[:]), 0, -1).astype(np.float32)
    measured = np.asarray(np.isfinite(canvas).all(axis=-1))
    factor = image.staged.levels[0].shape[2] / level.shape[2]
    um = float(np.mean(np.asarray(image.staged.pixel_size))) * factor
    mask = tissue_mask(canvas, measured, um)
    canvas = np.nan_to_num(canvas, nan=0.0)
    contours = tissue.contours_level0(mask, factor, (0, 0))
    windows = display.windows(canvas, mask, np.dtype(np.float32), False)
    colour, shown = colours(wavenumbers)
    settings: dict[str, object] = {
        "mode": "composite",
        "channels": [
            {
                "name": channel.name,
                "color": c,
                "window": [round(lo, 4), round(hi, 4)],
                "visible": v,
            }
            for channel, c, v, (lo, hi) in zip(image.channels, colour, shown, windows)
        ],
    }
    return settings, contours, float(mask[measured].mean()) if measured.any() else 0.0


def ome_images(
    images: Sequence[BuiltImage],
    contours: list[np.ndarray],
    display_settings: dict[str, object],
) -> list[ome_write.OmeImage]:
    """OME description of every image; image 0 carries the tissue outline and display colours."""
    rendered = cast(list[dict[str, str]], display_settings["channels"])
    shown = [ch["color"] for ch in rendered]
    out = []
    for index, image in enumerate(images):
        level0 = image.staged.levels[0]
        out.append(
            ome_write.OmeImage(
                name=image.name,
                shape_yx=(int(level0.shape[1]), int(level0.shape[2])),
                dtype=np.dtype(np.float32),
                rgb=False,
                channels=image.channels,
                colours=shown
                if index == 0
                else [tech.OTHER_COLOUR] * len(image.channels),
                pixel_size=image.staged.pixel_size,
                significant_bits=None,
                objective=(None, None),
                contours=contours if index == 0 else [],
                annotations=image.annotations,
            )
        )
    return out


def inventory_annotation(decisions: Sequence[object]) -> str:
    """JSON ``{element: reason}`` of the elements that were not processed."""
    return json.dumps(
        {d.path: d.reason for d in decisions if not d.kept},  # type: ignore[attr-defined]
        separators=(",", ":"),
    )
