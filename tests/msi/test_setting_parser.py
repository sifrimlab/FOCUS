"""SettingParser behavioral scenarios."""

import pytest

import numpy as np

from focus.config.options import MsiIonMode
from focus.config.mass_lists import parse_mz_by_mode


class TestSettingParser:
    """Behavioral contracts for setting parser."""

    def test_forms(self) -> None:
        """Verify forms."""
        assert parse_mz_by_mode(None, "x") is None
        assert parse_mz_by_mode("  ", "x") is None
        parsed = parse_mz_by_mode("pos: 798.54, 760.58; neg: 885.55", "x")
        assert parsed is not None
        np.testing.assert_array_equal(parsed["pos"], [760.58, 798.54])
        np.testing.assert_array_equal(parsed["neg"], [885.55])
        parsed = parse_mz_by_mode(
            {"POS": [760.58], MsiIonMode.NEGATIVE: np.array([885.55])}, "x"
        )
        assert parsed is not None
        assert sorted(parsed) == ["neg", "pos"]

    def test_invalid(self) -> None:
        """Verify invalid."""
        for value in (
            "pos 760.58",
            {"both": [1.0]},
            {"pos": ["abc"]},
            {"pos": [-1.0]},
            [760.58],
        ):
            with pytest.raises(ValueError):
                parse_mz_by_mode(value, "x")
