"""Required-key and scalar-type checks, preserving original error messages."""

from collections.abc import Mapping


# --- Validation helpers ---


def _require_key(
    d: Mapping[str, object],
    key: str,
    expected_type: type[object],
    context: str = "config",
) -> None:
    """Check that key exists in dict and has the expected type."""
    if key not in d:
        raise KeyError(f"Missing required key '{key}' in {context}.")
    if not isinstance(d[key], expected_type):
        raise TypeError(
            f"'{key}' in {context} must be {expected_type.__name__}, got {type(d[key]).__name__}."
        )


def _check_type(
    d: Mapping[str, object],
    key: str,
    expected_type: type[object],
    context: str = "config",
) -> None:
    """Check type for a key that is known to exist."""
    if not isinstance(d[key], expected_type):
        raise TypeError(
            f"'{key}' in {context} must be {expected_type.__name__}, got {type(d[key]).__name__}."
        )
