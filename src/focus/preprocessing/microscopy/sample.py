"""Bind one microscopy source to its cache key and output pyramid."""

from __future__ import annotations

import json
from importlib.metadata import PackageNotFoundError, version
import os

from focus.io.paths import preprocessed_file
from focus.io.provenance import cached_fingerprint, fingerprint, input_signature
from focus.preprocessing.base import SampleSource
from focus.preprocessing.microscopy import pyramid
from focus.preprocessing.microscopy.metadata import METHOD_VERSION
from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.source import find_source_file, open_source
from focus.preprocessing.microscopy.technology import calibration
from focus.preprocessing.microscopy.workflow import Provenance, process_image
from focus.runtime.reporter_context import get_reporter

# Libraries whose behaviour shapes the output pixels or metadata (distribution names).
_SOFTWARE = (
    "numpy",
    "scipy",
    "scikit-image",
    "tifffile",
    "imagecodecs",
    "zarr",
    "dask",
    "ome-types",
)
_OPENCV = ("opencv-python", "opencv-python-headless", "opencv-contrib-python")
_READERS = {".czi": "pylibCZIrw", ".nd2": "nd2"}


def _version(*names: str) -> str:
    """Installed version of the first distribution found among ``names``."""
    for name in names:
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "missing"


class MicroscopyImage(SampleSource):
    """One microscopy acquisition written as a lossless, tiled OME-TIFF pyramid.

    Input: one TIFF-family (OME-TIFF, qpTIFF, SVS, NDPI, SCN, BIF), CZI or ND2 file per sample.
    Output: the native pixels (dtype and every channel), the acquisition metadata, the tissue
    outline and display windows, reused while its fingerprint matches.
    """

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Create the sample output directory, then resolve its single source image.

        Raises
        ------
        FileNotFoundError
            If the sample modality directory contains no supported image.
        ValueError
            If it contains several."""
        super().__init__(source_path, sample_id, modality_name)
        self.filename: str = find_source_file(
            os.path.join(source_path, sample_id, modality_name)
        )

    def process_image(
        self, settings: ImageSettings, force_recomputing: bool = False
    ) -> str:
        """Preprocess the image unless the output already carries this fingerprint.

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
        reporter.step(1, 5, "Reading image")
        previous = pyramid.previous_output(output)
        with open_source(self.filename) as source:
            process_image(
                source, settings, reporter, output, Provenance(key, signature, previous)
            )
        return output

    def _fingerprint(self, settings: ImageSettings, signature: str) -> str:
        """Method, input content, sample, settings, calibration constants and software versions."""
        software = {name: _version(name) for name in _SOFTWARE}
        software["opencv"] = _version(*_OPENCV)
        reader = _READERS.get(os.path.splitext(self.filename)[1].lower())
        if reader:
            software[reader] = _version(reader)
        return fingerprint(
            {
                "method": METHOD_VERSION,
                "input_sha256": json.loads(signature)["sha256"],
                "sample_id": self.sample_id,
                "modality": self.modality_name,
                "settings": settings.fingerprint_settings(),
                "calibration": calibration(),
                "software": software,
            }
        )
