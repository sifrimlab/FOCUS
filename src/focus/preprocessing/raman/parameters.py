"""Typed Raman settings and their serialized fingerprint representation."""

from dataclasses import dataclass
from typing import TypedDict


class RamanSettings(TypedDict):
    """Preprocessing keywords after adapter defaults are applied."""

    denoise: bool
    remove_offset: bool
    normalization: str
    force_recomputing: bool


@dataclass(frozen=True)
class RamanRunSettings:
    """Options that change the written spectra."""

    denoise: bool
    remove_offset: bool
    normalization: str

    def fingerprint_settings(self) -> dict[str, object]:
        """Cache-key values with stable casts."""
        return {
            "denoise": bool(self.denoise),
            "remove_offset": bool(self.remove_offset),
            "normalization": str(self.normalization),
        }
