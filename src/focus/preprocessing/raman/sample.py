"""Bind one Raman (SRS) LIF session to its cache key and output OME-TIFF."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import os

from focus.io import ome_write
from focus.io.paths import preprocessed_file
from focus.io.provenance import cached_fingerprint, fingerprint, input_signature
from focus.preprocessing.base import SampleSource
from focus.preprocessing.raman.parameters import RamanRunSettings
from focus.preprocessing.raman.settings import METHOD_VERSION, calibration
from focus.preprocessing.raman.workflow import Provenance, process_lif
from focus.runtime.reporter_context import get_reporter

# Libraries whose behaviour shapes the output pixels or metadata (distribution names).
_SOFTWARE = (
    "numpy",
    "scipy",
    "scikit-image",
    "tifffile",
    "imagecodecs",
    "zarr",
    "ome-types",
    "readlif",
)


def _version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "missing"


def find_lif(directory: str) -> str:
    """The single ``.lif`` session file of a sample directory.

    Raises
    ------
    FileNotFoundError
        If there is none.
    ValueError
        If there are several (one LAS X session per sample).
    """
    found = sorted(
        entry.path
        for entry in os.scandir(directory)
        if entry.is_file() and entry.name.lower().endswith(".lif")
    )
    if not found:
        raise FileNotFoundError(f"No .lif file found in {directory}")
    if len(found) > 1:
        names = ", ".join(os.path.basename(f) for f in found)
        raise ValueError(
            f"Several .lif files in {directory} ({names}): keep one per sample"
        )
    return found[0]


class RamanImage(SampleSource):
    """One Leica SRS session written as a multi-image OME-TIFF (one image per acquisition).

    Input: one ``.lif`` file per sample. Output: every complete SRS acquisition stitched,
    flat-field corrected and placed in the frame of the reference acquisition, reused while its
    fingerprint matches.
    """

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Create the sample output directory and resolve its LIF file."""
        super().__init__(source_path, sample_id, modality_name)
        self.input_path = os.path.join(source_path, sample_id, modality_name)
        self.filename = find_lif(self.input_path)

    def process(
        self, settings: RamanRunSettings, force_recomputing: bool = False
    ) -> str:
        """Preprocess the session unless the output already carries this fingerprint.

        Returns
        -------
        str
            Path to the output OME-TIFF.
        """
        output = preprocessed_file(
            self.source_path, self.sample_id, self.modality_name, "ome.tiff"
        )
        reporter = getattr(self, "_step_reporter", None) or get_reporter()
        signature = input_signature(self.filename, output)
        key = self._fingerprint(settings, signature)
        if not force_recomputing and cached_fingerprint(output) == key:
            reporter.cached()
            return output
        previous = ome_write.previous_output(output)
        process_lif(
            self.filename,
            settings,
            reporter,
            output,
            Provenance(key, signature, previous),
        )
        return output

    def _fingerprint(self, settings: RamanRunSettings, signature: str) -> str:
        """Method, input content, sample, settings, calibration constants and software versions."""
        return fingerprint(
            {
                "method": METHOD_VERSION,
                "input_sha256": json.loads(signature)["sha256"],
                "sample_id": self.sample_id,
                "modality": self.modality_name,
                "settings": settings.fingerprint_settings(),
                "calibration": calibration(),
                "software": {name: _version(name) for name in _SOFTWARE},
            }
        )
