"""Ordered microscopy preparation: read, resolve the image type, detect tissue, crop, write."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os

import dask.array as da
import numpy as np

from focus.io import ome_write
from focus.preprocessing.microscopy import crop, display, tissue
from focus.preprocessing.microscopy.metadata import _valid_pixel_size
from focus.preprocessing.microscopy.output import focus_annotations, write_staged
from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.profile import resolve_image_type
from focus.preprocessing.microscopy.projection import (
    detection_factor,
    plane_canvas,
    project,
    resolve_planes,
)
from focus.preprocessing.microscopy.source import SourceImage
from focus.preprocessing.microscopy.technology import ImageProfile
from focus.runtime.reporting import StepReporter

# A metadata pixel size and the configured fallback that differ by more than this are reported.
_PIXEL_SIZE_TOLERANCE = 0.05


@dataclass
class Analysis:
    """Everything decided on the low-resolution canvas before level 0 is written."""

    profile: ImageProfile
    evidence: str
    z_index: int | None
    z_note: str
    pixel_size: np.ndarray | None
    size_source: str
    factor: float
    mask: np.ndarray
    windows: list[tuple[float, float]]


@dataclass(frozen=True)
class Provenance:
    """Identity of the output: fingerprint, input signature and the output it replaces."""

    key: str
    signature: str
    previous: tuple[str, list[str | int] | None] | None


def pixel_size(
    source: SourceImage, fallback: float | None, reporter: StepReporter
) -> tuple[np.ndarray | None, str]:
    """Metadata pixel size, else the configured fallback; a disagreement over 5% is reported."""
    size = source.pixel_size
    if size is not None:
        if (
            fallback
            and abs(float(np.mean(size)) - fallback) > _PIXEL_SIZE_TOLERANCE * fallback
        ):
            reporter.warning(
                f"The metadata pixel size {float(np.mean(size)):.4g} µm differs from pixel_size "
                f"{fallback:.4g} µm: the metadata value is used"
            )
        return size, source.pixel_size_source
    if fallback:
        return _valid_pixel_size([fallback, fallback]), "configuration"
    reporter.warning(
        "The source file has no pixel size: tissue areas and margins use pixel fallbacks and the "
        "encoder cannot choose its 0.5 µm/px level; set pixel_size in the configuration"
    )
    return None, "none"


def report_source(source: SourceImage, reporter: StepReporter) -> None:
    """Step 1 detail lines: format, size, channels, sample type and the reader's notes."""
    h, w = source.shape_yx
    t, z, c = (int(source.data.shape[i]) for i in (0, 1, 4))
    layout = "RGB" if source.rgb else f"{c} channel{'s' if c > 1 else ''}"
    reporter.detail(
        f"{os.path.basename(source.path)} ({source.format}): {w} x {h} px, {layout}, "
        f"{np.dtype(source.dtype).name}"
        + (f", Z={z}" if z > 1 else "")
        + (f", T={t}" if t > 1 else "")
    )
    if not source.rgb:
        reporter.detail("Channels: " + ", ".join(ch.name for ch in source.channels))
    for line in source.notes:
        reporter.detail(line)
    for line in source.warnings:
        reporter.warning(line)


def analyse(
    source: SourceImage, settings: ImageSettings, reporter: StepReporter
) -> Analysis:
    """Steps 2 and 3: image type, Z plane, detection canvas, tissue mask and display windows."""
    reporter.step(2, 5, "Resolving image type")
    size, size_source = pixel_size(source, settings.pixel_size, reporter)
    pixel_um = math.sqrt(float(size[0] * size[1])) if size is not None else None
    image_type, evidence, inferred = resolve_image_type(
        source, settings.image_type, pixel_um
    )
    reporter.detail(f"{image_type} ({evidence})")
    if inferred:
        reporter.warning(
            f"The image type was inferred from the background ({evidence}): set image_type to "
            "brightfield or fluorescence to make it explicit"
        )
    profile = ImageProfile(image_type, settings.z_projection)
    z_index, z_note = resolve_planes(source, profile.z_rule)
    if z_note:
        reporter.detail(z_note)

    reporter.step(3, 5, "Detecting tissue")
    factor = detection_factor(source.shape_yx, pixel_um)
    canvas = plane_canvas(
        source, factor, lambda level: project(level, profile.z_rule, z_index)
    )
    canvas_um = pixel_um * factor if pixel_um else None
    mask = tissue.detect_tissue(canvas, source.dtype, profile.brightfield, canvas_um)
    reporter.detail(f"Tissue covers {100 * mask.mean():.1f}% of the image")
    rgb_identity = source.rgb and profile.brightfield
    windows = display.windows(canvas, mask, source.dtype, rgb_identity)
    return Analysis(
        profile, evidence, z_index, z_note, size, size_source, factor, mask, windows
    )


def region(
    source: SourceImage,
    analysis: Analysis,
    settings: ImageSettings,
    reporter: StepReporter,
) -> tuple[da.Array, tuple[int, int], str]:
    """Step 4 (when requested): the level-0 plane to write, its crop offset and the fill used."""
    plane = project(source.data, analysis.profile.z_rule, analysis.z_index)
    offset = (0, 0)
    fill = "none"
    if not (settings.crop_to_tissue or settings.remove_background):
        return plane, offset, fill
    reporter.step(
        4, 5, "Cropping to tissue" if settings.crop_to_tissue else "Removing background"
    )
    pixel_um = (
        float(np.mean(analysis.pixel_size)) if analysis.pixel_size is not None else None
    )
    if settings.crop_to_tissue:
        margin = crop.margin_px(pixel_um)
        x0, y0, x1, y1 = crop.crop_box(
            analysis.mask, analysis.factor, source.shape_yx, margin
        )
        plane = plane[y0:y1, x0:x1]
        offset = (x0, y0)
        reporter.detail(
            f"Crop {x1 - x0} x {y1 - y0} px at ({x0}, {y0}), margin {margin} px"
        )
    if settings.remove_background:
        value = analysis.profile.fill_value(source.dtype)
        plane = crop.fill_background(
            plane, analysis.mask, analysis.factor, offset, value
        )
        fill = f"{value:g}"
        reporter.detail(f"Background outside the tissue set to {fill}")
    return plane, offset, fill


def process_image(
    source: SourceImage,
    settings: ImageSettings,
    reporter: StepReporter,
    output: str,
    provenance: Provenance,
) -> None:
    """Analyse the source, then stage, digest and write its pyramid atomically."""
    ome_write.ome_pixel_type(
        source.dtype
    )  # fail before any work on a type OME cannot store
    report_source(source, reporter)
    analysis = analyse(source, settings, reporter)
    plane, offset, fill = region(source, analysis, settings, reporter)
    shown = display.display_settings(
        source.channels, analysis.windows, source.rgb and analysis.profile.brightfield
    )
    values = focus_annotations(analysis, provenance, offset, fill, source, shown)
    reporter.step(5, 5, "Writing OME-TIFF")
    workdir = os.path.join(
        os.path.dirname(output), f".{os.getpid()}.stage.{os.path.basename(output)}"
    )
    try:
        levels = ome_write.stage_levels(plane, source.rgb, workdir)
        write_staged(
            source, analysis, levels, values, provenance, offset, output, reporter
        )
    finally:
        ome_write.remove_workdir(workdir)
