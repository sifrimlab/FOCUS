"""Sample preprocessing cache keys and the memoized input digest."""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import TYPE_CHECKING

from focus.io.provenance import (
    cached_fingerprint,
    file_digest,
    fingerprint,
    stat_signature,
)
from focus.preprocessing.transcriptomics.clustering import METHOD_VERSION
from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.technology import calibration

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic

# uns key of the output that stores the input's stat signature and SHA-256 (JSON).
INPUT_SIGNATURE_KEY = "focus_input_signature"


def input_signature(path: str, output_file: str) -> str:
    """
    JSON ``{"stat": ..., "sha256": ...}`` of the input file.

    The SHA-256 stored in ``output_file`` is reused while the input's path, size and modification
    time are unchanged, so an unchanged input is not re-hashed on every run.
    """
    stat = stat_signature(path)
    stored = cached_fingerprint(output_file, key=INPUT_SIGNATURE_KEY)
    if stored is not None:
        try:
            previous = json.loads(stored)
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict) and previous.get("stat") == stat:
            return stored
    return json.dumps({"stat": stat, "sha256": file_digest(path)})


def sample_fingerprint(
    sample: SpatialTranscriptomic, settings: StSampleSettings, signature: str
) -> str:
    """Hash the input content, settings, calibration constants and scientific-library versions."""
    return fingerprint(
        {
            "method": METHOD_VERSION,
            "input_sha256": json.loads(signature)["sha256"],
            "sample_id": sample.sample_id,
            "modality": sample.modality_name,
            "settings": settings,
            "calibration": calibration(),
            "software": {
                name: version(name)
                for name in ("scanpy", "numpy", "scipy", "scikit-learn", "igraph")
            },
        }
    )
