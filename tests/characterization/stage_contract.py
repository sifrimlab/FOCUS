"""Controlled stage execution for baseline and refactored pipeline comparisons."""

from __future__ import annotations

import copy
from itertools import product
from types import ModuleType
from unittest.mock import Mock, patch

from focus.config.types import PipelineConfig
from focus.pipeline.runner import _run_stages
from focus.runtime.reporting import StepReporter
from focus.runtime.types import Status


def collect_stage_contract(module: ModuleType) -> list[dict[str, object]]:
    """Record outputs, dispatch arguments, releases, and progress in eight workflows.

    Parameters
    ----------
    module
        Stage implementation whose I/O boundaries will be replaced by fixtures.

    Returns
    -------
    list of dict
        Deterministic records for each enabled/disabled stage combination.
    """
    records: list[dict[str, object]] = []
    for annotations_enabled, alignment, registration in product(
        (False, True), repeat=3
    ):
        config: PipelineConfig = {
            "dataset_path": "/controlled/dataset",
            "reference_modality": "st",
            "perform_alignment": alignment,
            "perform_registration": registration,
            "modalities": [
                {
                    "name": "st",
                    "type": "st",
                    "processing_settings": {"force_recomputing": True},
                    "registration_settings": {},
                },
                {
                    "name": "msi",
                    "type": "msi",
                    "processing_settings": {},
                    "registration_settings": {},
                },
            ],
            "spatial_annotations": {"modality_name": "st", "file_type": "geojson"}
            if annotations_enabled
            else None,
        }
        statuses: list[Status] = []

        def progress(status: Status) -> None:
            """Retain independent snapshots, including ordered activity operations."""
            statuses.append(copy.deepcopy(status))

        with (
            patch("time.time", return_value=100.0),
            patch.multiple(
                module,
                preprocess_modality=Mock(
                    side_effect=[
                        {"b": "st-b", "merged": "st-merged", "a": "st-a"},
                        {"a": "msi-a", "merged": "msi-merged"},
                    ]
                ),
                cached_fingerprint=Mock(return_value=None),
                release_memory=Mock(),
                _run_alignment=Mock(
                    return_value={"msi": {"a": "aligned-a", "merged": "aligned-merged"}}
                ),
                _run_annotation_transfer=Mock(
                    return_value={"a": "annotated-a", "merged": "annotated-merged"}
                ),
                _run_registration=Mock(
                    return_value={
                        "msi": {"a": "registered-a", "merged": "registered-merged"}
                    }
                ),
            ),
            patch.object(
                module,
                "compile_dataset"
                if hasattr(module, "compile_dataset")
                else "_compile_mudata",
                return_value="dataset.h5mu",
            ),
        ):
            output = _run_stages(config, StepReporter(callback=progress))
            records.append(
                {
                    "enabled": [annotations_enabled, alignment, registration],
                    "output": output,
                    "progress": statuses,
                    "releases": [
                        call.kwargs for call in module.release_memory.call_args_list
                    ],
                    "alignment_force": [
                        call.kwargs["force_overrides"]
                        for call in module._run_alignment.call_args_list
                    ],
                    "registration_force": [
                        call.kwargs["force_overrides"]
                        for call in module._run_registration.call_args_list
                    ],
                }
            )
    return records
