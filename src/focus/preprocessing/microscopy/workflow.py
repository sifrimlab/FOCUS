"""Ordered microscopy image preparation and output-pyramid coordination."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
import numpy as np

import focus.preprocessing.microscopy.color as color
import focus.preprocessing.microscopy.settings as _settings
from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.metadata import METHOD_VERSION
from focus.io.provenance import PREPROCESSING_FINGERPRINT_KEY, CROP_OFFSET_KEY
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.sample import MicroscopyImage


def process_image(
    sample: MicroscopyImage,
    settings: ImageSettings,
    reporter: StepReporter,
    output_ome_tiff: str,
    key: str,
    previous: tuple[str, list[str | int] | None] | None,
) -> None:
    """Load a float image, apply requested preparation and write its OME-TIFF pyramid.

    Parameters
    ----------
    sample : MicroscopyImage
        Supplies image I/O and bound tissue, crop and pyramid components.
    settings : ImageSettings
        Existing image-processing options and source-pixel crop margin.
    reporter : StepReporter
        Receives the five-stage progress events in their original order.
    output_ome_tiff : str
        Destination path; writing replaces the processed image.
    key : str
        Preprocessing fingerprint to serialize with the image.
    previous : tuple or None
        Previous pixel digest and superseded signature, retained until writing.
    """
    # 1. Load. Skipped optional steps (2-4) are not announced.
    reporter.step(1, 5, "Loading image")
    reporter.detail(os.path.basename(sample.filename))
    image, input_dtype = sample.reader._load_image(sample.filename)

    image = enhance_image(image, settings, reporter)
    image, crop_offset = mask_and_crop(sample, image, settings, reporter)
    save_image(
        sample,
        image,
        input_dtype,
        crop_offset,
        reporter,
        output_ome_tiff,
        key,
        previous,
    )


def enhance_image(
    image: np.ndarray, settings: ImageSettings, reporter: StepReporter
) -> np.ndarray:
    """Return enhanced (H, W, C) float32 pixels, preserving gamma/contrast order."""
    # 2. Color enhancement
    if settings.color_enhancement:
        reporter.step(2, 5, "Enhancing colors")
        reporter.detail(
            f"gamma={settings.gamma}, saturation={settings.contrast_saturation}"
        )
        image = color.gamma_correction(image, gamma=settings.gamma)
        image = color.enhance_contrast(
            image,
            saturated_pixels=settings.contrast_saturation,
            max_stat_pixels=_settings._DETECTION_MAX_PIXELS,
        )

    # Ensure float32 after enhancement
    if image.dtype != np.float32:
        image = image.astype(np.float32)

    return image


def mask_and_crop(
    sample: MicroscopyImage,
    image: np.ndarray,
    settings: ImageSettings,
    reporter: StepReporter,
) -> tuple[np.ndarray, tuple[int, int]]:
    """Mask/crop (H, W, C) pixels with one detection pass; return image and (x, y) offset."""
    # 3. Background removal + 4. Crop share one low-resolution tissue-detection pass
    if settings.remove_background or settings.crop_to_tissue:
        if settings.remove_background:
            reporter.step(3, 5, "Removing background")
        mask, scale = sample.tissue._compute_tissue_mask(
            image,
            min_object_coverage=settings.min_object_coverage,
            clip_percentile=settings.clip_percentile,
        )
        if settings.remove_background:
            image = sample.crop._remove_background(
                image, mask, scale, background_color=settings.background_color
            )

        if settings.crop_to_tissue:
            reporter.step(4, 5, "Cropping to tissue")
            reporter.detail(f"margin={settings.crop_margin}px")
            image, crop_offset = sample.crop._crop_image(
                image, mask, scale, margin=settings.crop_margin
            )
    if not settings.crop_to_tissue:
        crop_offset = (0, 0)

    return image, crop_offset


def save_image(
    sample: MicroscopyImage,
    image: np.ndarray,
    input_dtype: np.dtype,
    crop_offset: tuple[int, int],
    reporter: StepReporter,
    output_ome_tiff: str,
    key: str,
    previous: tuple[str, list[str | int] | None] | None,
) -> None:
    """Write the processed image with original dtype, physical units and provenance."""
    # 5. Save
    n_levels = sample.pyramid._compute_pyramid_levels(*image.shape[:2])
    reporter.step(5, 5, "Saving OME-TIFF")
    reporter.detail(
        f"{n_levels} pyramid levels, cap={_settings._MAX_PYRAMID_PIXELS} px"
    )
    pixel_size, size_source = sample.reader._source_pixel_size_um(sample.filename)
    if pixel_size is None:
        reporter.detail("The source file has no pixel size")
    else:
        reporter.detail(
            f"Pixel size {pixel_size[0]:.4g} x {pixel_size[1]:.4g} µm ({size_source})"
        )
    sample.pyramid._save_image_pyramid(
        image,
        output_ome_tiff,
        input_dtype,
        pixel_size_um=pixel_size,
        annotations={
            PREPROCESSING_FINGERPRINT_KEY: key,
            CROP_OFFSET_KEY: f"{crop_offset[0]},{crop_offset[1]}",
            "focus_method": METHOD_VERSION,
            "focus_pixel_size_source": size_source,
        },
        previous=previous,
    )
