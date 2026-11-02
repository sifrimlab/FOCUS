"""Serialize non-finite numerical values as null in alignment HTTP responses."""

from typing import Any

import math

from flask.json.provider import DefaultJSONProvider


def _replace_nonfinite(obj: object) -> object:
    """Recursively replace non-finite floats (NaN, ±Infinity) with ``None``.

    JSON has no NaN/Infinity literals, so the result is valid JSON. Only used on
    the rare payload that actually contains a non-finite value (see
    :class:`_ValidJSONProvider`).
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _replace_nonfinite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_replace_nonfinite(v) for v in obj]
    return obj


class _ValidJSONProvider(DefaultJSONProvider):
    """Flask JSON provider that never emits ``NaN``/``Infinity`` tokens.

    Flask's default provider serializes non-finite floats as the bare tokens
    ``NaN``/``Infinity``, which are *not* valid JSON. The browser client's
    ``JSON.parse`` rejects them; axios then returns the unparsed response as a
    raw string, so the SPOT ``/payload`` (which the GUI expects to be an array)
    arrives as a string and crashes the client with ``x.map is not a function``.
    Spot coordinates can legitimately be non-finite (spots without a valid
    spatial position), so every response is guarded. The clean path stays fast:
    strict serialization is attempted first and only falls back to sanitizing
    when a non-finite value is actually present.
    """

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        """Encode JSON using the GUI's existing handling of non-finite values."""
        try:
            return super().dumps(obj, allow_nan=False, **kwargs)
        except ValueError:
            return super().dumps(_replace_nonfinite(obj), **kwargs)
