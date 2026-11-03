"""imzML coordinate fallback, encoding checks and the pixel-grid fingerprint marker."""

from pathlib import Path
import types

import numpy as np
import pytest

from focus.io.provenance import file_digest
from focus.preprocessing.msi.coordinates import MsiCoordinates
from focus.preprocessing.msi.imzml import ImzmlReader
from focus.preprocessing.msi.provenance import _imzml_identity
from tests.msi.data import write_imzml

_LINES, _COLUMNS = 3, 10


def write(directory: Path, stage: bool) -> Path:
    """Write a 3-line, 10-column raster with a 20 x 40 µm pixel, with or without stage positions."""
    coords = np.array(
        [(line, col) for line in range(_LINES) for col in range(_COLUMNS)], np.int32
    )
    mz = [np.array([500.0, 600.0]) for _ in coords]
    intensity = [np.array([1.0, 2.0], np.float32) for _ in coords]
    positions = [(col * 20.0, line * 40.0) for line, col in coords] if stage else None
    write_imzml(str(directory), mz, intensity, coords, pixel_size=20, stage=positions)
    path = directory / "data.imzML"
    text = path.read_text().replace(
        '<cvParam name="pixel size y" value="20"/>',
        '<cvParam name="pixel size y" value="40"/>',
    )
    path.write_text(text)
    return path


def parse(path: Path) -> dict:  # type: ignore[type-arg]
    """Parse `path` with a bare sample namespace."""
    sample = types.SimpleNamespace()
    sample.coordinates = MsiCoordinates(sample)  # type: ignore[arg-type]
    return ImzmlReader(sample)._parse_imzml(str(path))  # type: ignore[arg-type,return-value]


def test_pixel_grid_coordinates_follow_the_stage_axes(tmp_path: Path) -> None:
    """Without stage positions, x runs along the scan line and each axis takes its own pixel size."""
    grid = parse(write(tmp_path / "grid", stage=False))["physical_coordinates"]
    stage = parse(write(tmp_path / "stage", stage=True))["physical_coordinates"]
    np.testing.assert_allclose(grid - grid.min(0), stage - stage.min(0), atol=1e-3)
    assert len(np.unique(grid[:, 0].round(3))) == _COLUMNS
    assert np.ptp(grid[:, 1]) == pytest.approx((_LINES - 1) * 40.0)


@pytest.mark.parametrize(
    ("term", "message"),
    [
        ("zlib compression", "compressed binary arrays"),
        ("profile spectrum", "profile spectra"),
    ],
)
def test_unsupported_encodings_raise(tmp_path: Path, term: str, message: str) -> None:
    """Compressed arrays and profile spectra are refused instead of decoded as peaks."""
    path = write(tmp_path, stage=True)
    text = path.read_text().replace(
        '<cvParam name="32-bit float" value=""/>',
        f'<cvParam name="32-bit float" value=""/><cvParam name="{term}" value=""/>',
    )
    path.write_text(text)
    with pytest.raises(ValueError, match=message):
        parse(path)


def test_declared_centroid_uncompressed_data_is_accepted(tmp_path: Path) -> None:
    """The terms of a standard centroid export pass the check."""
    path = write(tmp_path, stage=True)
    text = path.read_text().replace(
        '<cvParam name="32-bit float" value=""/>',
        '<cvParam name="32-bit float" value=""/><cvParam name="no compression" value=""/>'
        '<cvParam name="centroid spectrum" value=""/>',
    )
    path.write_text(text)
    assert parse(path)["physical_coordinates"].shape == (_LINES * _COLUMNS, 2)


def test_identity_marks_pixel_grid_files_only(tmp_path: Path) -> None:
    """The digest equals file_digest; only files without stage positions are marked."""
    for stage in (False, True):
        path = write(tmp_path / str(stage), stage=stage)
        digest, pixel_grid = _imzml_identity(str(path))
        assert digest == file_digest(path)
        assert pixel_grid is not stage
