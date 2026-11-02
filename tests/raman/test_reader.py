"""Compare the complete controlled LIF readout and overlap handling with archived outputs."""

import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import numpy as np

from focus.preprocessing.raman import reader
from focus.preprocessing.raman.metadata import RamanMetadata
from focus.preprocessing.raman.sample import RamanImage
from tests.raman.lif_data import LifPlanes, scan_metadata


def test_lif_readout_matches_baseline(tmp_path: Path) -> None:
    """Retain plane order, float32 normalization, overlap removal and physical metadata."""
    sample = RamanImage(str(tmp_path), "sample", "raman")
    planes = LifPlanes()
    with (
        patch.object(reader, "LifFile", return_value=planes),
        patch.object(
            reader, "parse_lif_metadata", return_value=scan_metadata(RamanMetadata)
        ),
    ):
        sample.reader._load_lif("controlled.lif")
    expected = np.load(Path(__file__).with_name("lif_expected.npz"))
    metadata = cast(RamanMetadata, sample.metadata)
    values = {
        "raw": sample.raw,
        "wavenumbers": sample.wavenumbers,
        "coordinates": sample.tiles_coordinates,
        "pixel_size": metadata.pixel_size,
    }
    for name, value in values.items():
        np.testing.assert_array_equal(
            cast(np.ndarray, value), expected[name], strict=True
        )
    contract = json.loads(Path(__file__).with_name("lif_contract.json").read_text())
    assert json.loads(json.dumps(planes.calls)) == contract["calls"]
    assert [list(pair) for pair in sample._spectra_slices] == contract["slices"]
    assert [
        metadata.scan_height,
        metadata.scan_width,
        metadata.tile_number,
        metadata.lambda_steps,
    ] == contract["shape"]
