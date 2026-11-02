"""Compare Raman cleaning with original serial/two-worker outputs, including warm reuse."""

from pathlib import Path
from typing import cast

import numpy as np
import pytest

from focus.preprocessing.raman.sample import RamanImage


@pytest.mark.parametrize("parallel", [False, True])
def test_spectral_cleaning_matches_baseline(tmp_path: Path, parallel: bool) -> None:
    """Retain float32 spectra, scan slices, zero-variance removal and worker serialization."""
    sample = RamanImage(str(tmp_path), "sample", "raman", max_workers=2)
    raw = np.random.RandomState(7).uniform(0, 1, (2, 32, 3, 3)).astype(np.float32)
    raw[0, :, 0, 0] = 0
    original = raw.copy()
    sample._basic_corrected_tiles = raw
    sample._wavenumbers = np.concatenate(
        [np.linspace(500, 800, 16), np.linspace(900, 1200, 16)]
    ).astype(np.float32)
    sample._spectra_slices = [(0, 15), (16, 31)]
    expected = np.load(Path(__file__).with_name("spectra_expected.npy"))
    sample.tiles.process_raw_tiles(parallel=parallel, force_recomputing=True)
    np.testing.assert_array_equal(
        cast(np.ndarray, sample.corrected), expected, strict=True
    )
    np.testing.assert_array_equal(raw, original, strict=True)
    cache = Path(sample.output_path) / "raman_corrected_tiles.npy"
    timestamp = cache.stat().st_mtime_ns
    sample._basic_corrected_tiles = np.zeros_like(raw)
    sample.tiles.process_raw_tiles(parallel=parallel)
    np.testing.assert_array_equal(
        cast(np.ndarray, sample.corrected), expected, strict=True
    )
    assert cache.stat().st_mtime_ns == timestamp
