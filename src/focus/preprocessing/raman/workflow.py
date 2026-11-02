"""Per-sample Raman execution and successful-run intermediate-cache cleanup."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from focus.io.paths import preprocessed_file
from focus.runtime.reporting import StepReporter
from focus.preprocessing.raman.parameters import RamanRunSettings

if TYPE_CHECKING:
    from focus.preprocessing.raman.sample import RamanImage


def process_sample(
    sample: RamanImage, settings: RamanRunSettings, reporter: StepReporter
) -> str:
    """Run correction, segmentation, spectral cleaning and stitching, or reuse its TIFF.

    Parameters
    ----------
    sample : RamanImage
        Sample whose loaded arrays and intermediate caches are mutated by processing.
    settings : RamanRunSettings
        Existing correction and smoothing parameters, without validation or coercion.
    reporter : StepReporter
        Receives the cache event. Exceptions propagate to the dataset's warning handler.

    Returns
    -------
    str
        Output OME-TIFF path.
    """
    output_expected = preprocessed_file(
        sample.source_path, sample.sample_id, sample.modality_name, "ome.tiff"
    )
    if settings.force_recomputing or not os.path.exists(output_expected):
        sample.load_source()
        sample.correction.basic_correct(force_recomputing=settings.force_recomputing)
        sample.correction.remove_background(
            force_recomputing=settings.force_recomputing,
            bg_min_area_fraction=settings.bg_min_area_fraction,
            otsu_threshold_factor=settings.otsu_threshold_factor,
            min_object_size=settings.min_object_size,
        )
        sample.tiles.process_raw_tiles(
            parallel=True,
            force_recomputing=settings.force_recomputing,
            savgol_window=settings.savgol_window,
            savgol_polyorder=settings.savgol_polyorder,
        )
        output_file = sample.stitcher.ashlar_stitch(
            force_recomputing=settings.force_recomputing
        )
    else:
        reporter.cached()
        output_file = output_expected
    return output_file


def clean_intermediates(sample: RamanImage) -> None:
    """Delete existing correction arrays after a sample has produced an output path."""
    for cache_name in [
        "basic_corrected_tiles.npy",
        "raman_corrected_tiles.npy",
        "segmented_tiles.npy",
    ]:
        cache_path = os.path.join(sample.output_path, cache_name)
        if os.path.exists(cache_path):
            os.remove(cache_path)
