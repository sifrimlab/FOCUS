"""MSI settings validation: removed settings and normalizations, presets, tolerance."""

import pytest

from focus.config.compatibility import validate_msi_settings
from focus.config.options import MsiSampleType
from focus.preprocessing.msi.technology import calibration, sample_profile


@pytest.mark.parametrize(
    ("setting", "reason"),
    [
        ("detect_background", "sample_type"),
        ("frequency_threshold", "S/N filter"),
        ("min_intensity_threshold", "S/N filter"),
    ],
)
def test_removed_settings_raise_with_their_reason(setting: str, reason: str) -> None:
    """A configuration still holding a removed setting fails and says why."""
    with pytest.raises(ValueError, match=f"'{setting}'.*{reason}"):
        validate_msi_settings("MSI", {setting: True})


@pytest.mark.parametrize(
    ("value", "replacement"),
    [("tic_mean_scaled", "tic"), ("log", "none"), ("clr", "none")],
)
def test_removed_normalizations_name_their_replacement(
    value: str, replacement: str
) -> None:
    """A removed normalization fails and names the value to use instead."""
    with pytest.raises(ValueError, match=f"use '{replacement}'"):
        validate_msi_settings("MSI", {"intensity_normalization": value})


def test_presets_and_tolerance_are_checked() -> None:
    """Unknown sample types and non-positive tolerances fail; floats are accepted."""
    with pytest.raises(ValueError, match="sample_type"):
        validate_msi_settings("MSI", {"sample_type": "section"})
    with pytest.raises(ValueError, match="mass_tolerance"):
        validate_msi_settings("MSI", {"mass_tolerance": 0})
    validate_msi_settings(
        "MSI",
        {
            "mass_tolerance": 7.5,
            "sample_type": "tissue_noisy",
            "intensity_normalization": "rms",
        },
    )


def test_every_sample_type_has_a_profile_in_the_fingerprint() -> None:
    """Every preset resolves to a calibration, and all of them enter the fingerprint."""
    profiles = calibration()["profiles"]
    for sample_type in MsiSampleType.list():
        assert sample_profile(sample_type).sample_type == sample_type
        assert sample_type in profiles  # type: ignore[operator]
    with pytest.raises(ValueError):
        sample_profile("unknown")
