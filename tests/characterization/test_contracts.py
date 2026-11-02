"""Contracts recorded from the pre-refactor working tree."""

from focus.config.mass_lists import parse_mz_by_mode

from focus.config.types import PipelineConfig
from focus.runtime.types import Status
from typing import cast

import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from focus.gui.server import MainGUI
from focus.gui.schema import _build_schema
from focus.annotations.transfer import transfer_annotations
from focus.config.options import MsiIntensityNormalization
from focus.pipeline.invalidation import _compute_effective_force_flags
from focus.preprocessing.msi.normalization import (
    normalize_intensities,
)
from focus.io.provenance import array_digest, fingerprint
from focus.runtime.reporting import StepReporter
from focus.config.validation import parse_config


def collect_contracts() -> dict[str, object]:
    """Collect deterministic configuration, numeric, GUI, and reporting contracts."""
    errors: list[list[str]] = []
    result: dict[str, object] = {"schema": _build_schema(), "errors": errors}
    for invalid_config in (
        None,
        {},
        {"dataset_path": 2},
        {"dataset_path": "x", "modalities": []},
    ):
        try:
            parse_config(invalid_config)
        except (TypeError, KeyError, ValueError) as error:
            errors.append([type(error).__name__, str(error)])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "sample").mkdir()
        config: PipelineConfig = {
            "dataset_path": directory,
            "reference_modality": "rna",
            "modalities": [{"name": "rna", "type": "st", "processing_settings": {}}],
        }
        validated = parse_config(config, require_raw_inputs=False)
        result["same_config_object"] = validated is config
        result["force_flags"] = _compute_effective_force_flags(validated, {"rna"})
        recorded_config = copy.deepcopy(validated)
        recorded_config["dataset_path"] = "<dataset>"
        result["config"] = recorded_config
        annotation = root / "regions.geojson"
        annotation.write_text(
            json.dumps(
                {
                    "features": [
                        {
                            "properties": {"name": "outer"},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[0, 0], [8, 0], [8, 8], [0, 8], [0, 0]]
                                ],
                            },
                        },
                        {
                            "properties": {"classification": {"name": "inner"}},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[2, 2], [5, 2], [5, 5], [2, 5], [2, 2]]
                                ],
                            },
                        },
                    ]
                }
            )
        )
        result["annotations"] = transfer_annotations(
            np.array([[0, 0], [1, 1], [3, 3], [9, 9]]),
            np.array(["sample"] * 4),
            {"sample": str(annotation)},
        ).tolist()
    raw = np.array([[0, 2, 4], [0, 0, 0], [8, 2, 0]], dtype=np.float32)
    normalization: dict[str, object] = {}
    result["normalization"] = normalization
    for method in MsiIntensityNormalization.list():
        if method == MsiIntensityNormalization.INTERNAL_STANDARD:
            continue
        value = normalize_intensities(raw.copy(), method)
        normalization[method] = {
            "values": value.tolist(),
            "dtype": value.dtype.str,
            "digest": array_digest(value),
        }
    result["mz_setting"] = {
        key: value.tolist()
        for key, value in cast(
            dict[str, np.ndarray],
            parse_mz_by_mode("neg: 885.5, 700; pos: 500", "reference"),
        ).items()
    }
    result["fingerprint"] = fingerprint({"b": [2, 3], "a": 1})
    events: list[Status] = []
    with (
        patch("focus.runtime.reporting.time.monotonic", return_value=10.0),
        patch("focus.runtime.reporting.time.time", return_value=10.0),
    ):
        reporter = StepReporter(callback=events.append)
        with reporter.stage("preprocessing", 1, 1):
            with reporter.modality("rna", 1, 1):
                reporter.step(1, 1, "Read")
                reporter.detail("input")
                reporter.cached()
        reporter.finish()
    result["events"] = events
    client = MainGUI().app.test_client()
    http: dict[str, object] = {}
    result["http"] = http
    for method, path in (
        ("GET", "/api/config"),
        ("GET", "/api/samples"),
        ("POST", "/api/cancel"),
        ("GET", "/api/schema"),
    ):
        response = client.open(path, method=method)
        http[method + " " + path] = [
            response.status_code,
            response.get_json(),
        ]
    return cast(dict[str, object], json.loads(json.dumps(result)))


def test_recorded_contracts() -> None:
    """Preserve the outputs recorded before source extraction."""
    expected = json.loads(Path(__file__).with_name("contracts.json").read_text())
    assert collect_contracts() == expected


def test_builtin_modality_dispatch() -> None:
    """Importing the dispatcher registers every built-in modality adapter."""
    from focus.preprocessing.dispatcher import preprocess_modality
    from focus.preprocessing.registry import _MODALITY_REGISTRY

    assert callable(preprocess_modality)
    assert set(_MODALITY_REGISTRY) == {"microscopy_image", "msi", "raman", "st"}
