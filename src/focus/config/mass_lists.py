"""Parse existing m/z list configuration forms without changing validation or value order."""

import numpy as np
from numpy.typing import NDArray
from focus.config.options import MsiIonMode


def parse_mz_by_mode(
    value: object, setting: str
) -> dict[str, NDArray[np.float64]] | None:
    """
    Read a per-ion-mode m/z list setting (`recalibration_reference`, `internal_standard_mz`).

    Accepts None or an empty string (not set), a mapping such as {"pos": [760.58, 798.54]} with list
    or array values (YAML config, Python API), or the GUI string "pos: 760.58, 798.54; neg: 885.55".
    Returns ascending float64 arrays keyed by ion mode, or None when no mode has a value.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = _string_mass_lists(value, setting)
    if not isinstance(value, dict):
        raise ValueError(
            f"{setting} must map ion modes {MsiIonMode.list()} to lists of m/z values."
        )

    parsed = {}
    for mode, masses in value.items():
        mode = str(mode).strip().lower()
        if mode not in MsiIonMode.list():
            raise ValueError(
                f"{setting}: invalid ion mode '{mode}'. Expected one of {MsiIonMode.list()}."
            )
        try:
            array = np.asarray(masses, dtype=np.float64).ravel()
        except (TypeError, ValueError):
            raise ValueError(
                f"{setting}: the m/z values for '{mode}' must be numbers."
            ) from None
        if not np.all(np.isfinite(array)) or np.any(array <= 0):
            raise ValueError(
                f"{setting}: the m/z values for '{mode}' must be positive numbers."
            )
        if array.size:
            parsed[mode] = np.sort(array)
    return parsed or None


def _string_mass_lists(value: str, setting: str) -> dict[str, list[str]]:
    """Split the existing semicolon-separated GUI form, retaining order and syntax errors."""
    mapping = {}
    for part in value.split(";"):
        if not part.strip():
            continue
        mode, separator, masses = part.partition(":")
        if not separator:
            raise ValueError(
                f"{setting}: expected 'mode: m/z, m/z; mode: m/z', got '{part.strip()}'."
            )
        mapping[mode] = masses.replace(",", " ").split()
    return mapping
