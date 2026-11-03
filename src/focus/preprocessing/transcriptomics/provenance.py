"""Sample preprocessing cache keys."""

from __future__ import annotations

import json
from importlib.metadata import version
from typing import TYPE_CHECKING

from focus.io.provenance import fingerprint
from focus.preprocessing.transcriptomics.clustering import METHOD_VERSION
from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.technology import calibration

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic


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
