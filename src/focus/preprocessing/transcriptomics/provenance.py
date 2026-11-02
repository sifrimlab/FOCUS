"""Sample preprocessing cache keys, including original library-version dependencies."""

from __future__ import annotations

from importlib.metadata import version
from typing import TYPE_CHECKING
from focus.io.provenance import fingerprint, file_digest
from focus.preprocessing.transcriptomics.clustering import METHOD_VERSION
from focus.preprocessing.transcriptomics.parameters import StSampleSettings

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic


def sample_fingerprint(
    sample: SpatialTranscriptomic, settings: StSampleSettings
) -> str:
    """Hash the input file, ordered processing settings and scientific-library versions.

    The input path is resolved again at the same point as in the original workflow;
    missing files and digest errors propagate without modifying sample state.
    """
    return fingerprint(
        {
            "method": METHOD_VERSION,
            "input_sha256": file_digest(sample._input_file()),
            "sample_id": sample.sample_id,
            "modality": sample.modality_name,
            "settings": settings,
            "software": {
                name: version(name)
                for name in ("scanpy", "numpy", "scipy", "scikit-learn", "igraph")
            },
        }
    )
