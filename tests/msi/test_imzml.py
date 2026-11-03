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


def test_arrays_are_identified_by_their_cv_terms(tmp_path: Path) -> None:
    """Parameter groups with exporter-specific ids are recognised by 'm/z array' and 'intensity array'."""
    path = write(tmp_path, stage=True)
    text = (
        path.read_text()
        .replace(
            'id="mzArray"><cvParam',
            'id="g_mz"><cvParam name="m/z array" value=""/><cvParam',
        )
        .replace(
            'id="intensities"><cvParam',
            'id="g_int"><cvParam name="intensity array" value=""/><cvParam',
        )
        .replace('ref="mzArray"', 'ref="g_mz"')
        .replace('ref="intensities"', 'ref="g_int"')
    )
    path.write_text(text)
    metadata = parse(path)
    assert metadata["mz_dtype"] is np.float64
    assert metadata["intensities_binary_metadata"].shape == (_LINES * _COLUMNS, 3)


def test_decimal_pixel_size_is_kept_fractional(tmp_path: Path) -> None:
    """A pixel size written as '12.5' is read as 12.5 µm, not truncated."""
    path = write(tmp_path, stage=False)
    text = (
        path.read_text()
        .replace('"pixel size x" value="20"', '"pixel size x" value="12.5"')
        .replace('"pixel size y" value="40"', '"pixel size y" value="12.5"')
    )
    path.write_text(text)
    np.testing.assert_allclose(parse(path)["raster_size"], [12.5, 12.5])


def test_missing_pixel_size_is_inferred_from_stage_positions(tmp_path: Path) -> None:
    """Without a declared pixel size the stage pitch of each axis is used."""
    path = write(tmp_path, stage=True)
    text = path.read_text()
    for axis in ("x", "y"):
        start = text.index(f'<cvParam name="pixel size {axis}"')
        text = text[:start] + text[text.index("/>", start) + 2 :]
    path.write_text(text)
    np.testing.assert_allclose(parse(path)["raster_size"], [20.0, 40.0], rtol=1e-5)


def test_missing_pixel_size_without_stage_positions_raises(tmp_path: Path) -> None:
    """Neither a pixel size nor stage positions: the µm scale is unknown, so preprocessing stops."""
    path = write(tmp_path, stage=False)
    text = path.read_text()
    for axis in ("x", "y"):
        start = text.index(f'<cvParam name="pixel size {axis}"')
        text = text[:start] + text[text.index("/>", start) + 2 :]
    path.write_text(text)
    with pytest.raises(ValueError, match="pixel size"):
        parse(path)


def test_integer_intensities_and_zero_mz_peaks_are_read(tmp_path: Path) -> None:
    """Integer intensity arrays become float32; peaks at m/z 0 are dropped."""
    from focus.config.options import MsiIonMode
    from focus.preprocessing.msi.sample import MsiSample

    coords = np.array([(0, k) for k in range(4)], np.int32)
    mz = [np.array([0.0, 500.0, 600.0]) for _ in coords]
    intensity = [np.array([3, 7, 9]) for _ in coords]
    write_imzml(
        str(tmp_path / "S" / "MSI" / "pos"),
        mz,
        intensity,
        coords,
        intensity_dtype="<i4",
    )
    sample = MsiSample(str(tmp_path), "S", "MSI", ion_mode=MsiIonMode.POSITIVE)
    sample.initialize_sample()
    mz_vectors, intensity_vectors = sample.load_payload()
    for values, peaks in zip(intensity_vectors["pos"], mz_vectors["pos"]):
        assert values.dtype == np.float32
        np.testing.assert_array_equal(peaks, [500.0, 600.0])
        np.testing.assert_array_equal(values, [7.0, 9.0])
