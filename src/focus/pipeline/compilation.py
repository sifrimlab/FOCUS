"""Coordinate reference loading, coverage filtering and final MuData assembly."""

from focus.pipeline.compilation_anchor import anchor_path, load_anchor

from focus.pipeline.types import ModalityFiles

from focus.runtime.resources import release_memory
from focus.pipeline.assembly import (
    load_registered,
    filter_anchor,
    prepare_axes,
    write_multimodal,
)
from focus.config.types import PipelineConfig


from focus.config.keys import ConfigParameters
from focus.runtime.reporting import StepReporter
from focus.runtime.reporter_context import get_reporter


def compile_dataset(
    config: PipelineConfig,
    modality_files: ModalityFiles,
    registered_files: ModalityFiles,
    annotation_files: dict[str, str] | None = None,
    reporter: StepReporter | None = None,
) -> str | None:
    """
    Compile a final MuData object with paired observations across modalities.

    Returns the path to the saved MuData file, or None if compilation was skipped.
    """
    reporter = reporter or get_reporter()
    dataset_path = config[ConfigParameters.DATASET_PATH]
    modalities = config[ConfigParameters.MODALITIES]
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]
    path = anchor_path(config, modality_files, annotation_files, reporter)
    if path is None:
        return None
    reporter.step(1, 3, "Loading registered modalities")
    anchor = load_anchor(path, ref_name)
    load_registered(
        modalities,
        registered_files,
        ref_name,
        anchor,
        reporter,
    )
    if not filter_anchor(anchor, ref_name, reporter):
        return None

    if len(anchor.modalities) < 2:
        reporter.warning(
            "Only one modality is available: the multimodal dataset is not compiled"
        )
        return None
    prepare_axes(anchor.modalities, ref_name)
    output_path = write_multimodal(anchor, ref_name, dataset_path, reporter)
    anchor.modalities = {}
    release_memory(gpu=False)
    return output_path
