"""Content classification of profile and centroid spectra, and the rule that reads the header."""

from pathlib import Path
from typing import Any

import numpy as np
import pytest

import focus.preprocessing.msi.representation as representation_module
from focus.config.keys import MsiMetadata
from focus.preprocessing.msi.representation import (
    MIXED,
    PEAKS,
    PROFILE,
    UNRESOLVED,
    needs_centroiding,
    probe_representation,
)
from focus.preprocessing.msi.sample import MsiSample
from tests.msi.data import synthetic_sample
from tests.msi.profile_data import (
    COORDS,
    profile_spectrum,
    sample_for,
    write_profile,
    write_profile_imzml,
)


class Recorder:
    """Collects the warnings a step reports."""

    def __init__(self) -> None:
        """Start with no messages."""
        self.warnings: list[str] = []

    def warning(self, message: str) -> None:
        """Record a warning."""
        self.warnings.append(message)


@pytest.fixture
def reporter(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """Replace the reporter of the representation check."""
    recorder = Recorder()
    monkeypatch.setattr(representation_module, "get_reporter", lambda: recorder)
    return recorder


def probe(sample: MsiSample) -> representation_module.Representation:
    """Parse the sample's imzML and probe its spectra."""
    from focus.io.discovery import find_imzml_pair

    path = str(sample.input_paths["pos"])
    imzml, ibd = find_imzml_pair(path)  # type: ignore[misc]
    meta = sample.reader._parse_imzml(imzml)
    return probe_representation(
        ibd,
        meta[MsiMetadata.MZ_DTYPE],
        meta[MsiMetadata.INTENSITIES_DTYPE],
        meta[MsiMetadata.MZ_BINARY_METADATA],
        meta[MsiMetadata.INTENSITIES_BINARY_METADATA],
    )


@pytest.mark.parametrize(
    "options",
    [
        {},  # zero-trimmed islands
        {"trim": False},  # dense profile with baseline and noise
        {"continuous": True, "trim": False},  # one shared m/z axis
        {"declared": "centroid"},
        {"declared": None},
    ],
)
def test_resolved_profile_is_detected_whatever_the_header(
    tmp_path: Path, reporter: Recorder, options: dict[str, Any]
) -> None:
    """Content decides: a resolved profile is centroided; a centroid header only adds a warning."""
    sample = sample_for(tmp_path, write_profile(tmp_path / "in", **options))
    found = probe(sample)
    assert found.kind == PROFILE
    assert found.points_per_peak == pytest.approx(6.0, abs=0.7)
    declared = options.get("declared", "profile") or ""
    assert needs_centroiding(found, declared, "data.imzML")
    assert bool(reporter.warnings) == (declared == "centroid")


def test_undersampled_declared_profile_is_refused(tmp_path: Path) -> None:
    """Too few points per peak cannot be picked reliably."""
    sample = sample_for(tmp_path, write_profile(tmp_path / "in", points=1.8))
    found = probe(sample)
    assert found.kind == UNRESOLVED
    with pytest.raises(ValueError, match=r"1\.\d points per peak .*at least 3"):
        needs_centroiding(found, "profile", "data.imzML")
    # Without the profile declaration the file is read as it was before.
    assert not needs_centroiding(found, "", "data.imzML")


def test_coarse_grid_declared_profile_is_refused(tmp_path: Path) -> None:
    """A regular grid coarser than 0.1 Da is a list of ions, not a profile."""
    sample = sample_for(
        tmp_path, write_profile(tmp_path / "in", points=6.0, step=0.25, trim=False)
    )
    found = probe(sample)
    assert found.kind == UNRESOLVED
    with pytest.raises(ValueError, match="grid step"):
        needs_centroiding(found, "profile", "data.imzML")


def test_centroid_files_are_left_alone(tmp_path: Path, reporter: Recorder) -> None:
    """Peak lists, including an evenly spaced ladder of isotopes, are read as before."""
    mz_list, intensity_list, coords = synthetic_sample(n_lines=3, n_cols=10)
    ladder = [500.0 + 1.00335 * np.arange(30) for _ in range(coords.shape[0])]
    flat = [np.ones(m.size, np.float32) for m in ladder]
    cases = {
        "peaks": (mz_list, intensity_list, "centroid"),
        "ladder": (ladder, flat, "centroid"),
        "undeclared": (mz_list, intensity_list, None),
    }
    for name, (spectra, intensities, declared) in cases.items():
        directory = tmp_path / name / "in"
        write_profile_imzml(directory, spectra, intensities, coords, declared)
        found = probe(sample_for(tmp_path / name, directory))
        assert found.kind in (PEAKS, UNRESOLVED)
        assert not needs_centroiding(found, declared or "", "data.imzML")
    assert not reporter.warnings


def test_declared_profile_holding_peak_lists_is_read_as_centroids(
    tmp_path: Path, reporter: Recorder
) -> None:
    """A profile header over irregular m/z values only warns."""
    mz_list, intensity_list, coords = synthetic_sample(n_lines=3, n_cols=10)
    write_profile_imzml(tmp_path / "in", mz_list, intensity_list, coords)
    found = probe(sample_for(tmp_path, tmp_path / "in"))
    assert found.kind == PEAKS
    assert not needs_centroiding(found, "profile", "data.imzML")
    assert len(reporter.warnings) == 1
    assert "regular grid" in reporter.warnings[0]


def test_mixed_representations_are_refused(tmp_path: Path) -> None:
    """Half sampled profiles, half peak lists."""
    rng = np.random.default_rng(0)
    mz_list, intensity_list = [], []
    for k in range(COORDS.shape[0]):
        if k % 2:
            mz_list.append(np.sort(rng.uniform(400, 800, 40)))
            intensity_list.append(rng.uniform(1, 100, 40).astype(np.float32))
        else:
            mz, y = profile_spectrum(
                np.array([500.0, 600.0]),
                np.array([1e4, 1e4]),
                6,
                low=400.0,
                high=800.0,
                baseline=0.0,
                noise=0.0,
                trim=True,
            )
            mz_list.append(mz)
            intensity_list.append(y)
    write_profile_imzml(tmp_path / "mixed", mz_list, intensity_list, COORDS, None)
    found = probe(sample_for(tmp_path, tmp_path / "mixed"))
    assert found.kind == MIXED
    with pytest.raises(ValueError, match="mix"):
        needs_centroiding(found, "", "data.imzML")
