"""Typed microscopy settings and their serialized fingerprint representation."""

from dataclasses import dataclass
from typing import TypedDict


class MicroscopyImageSettings(TypedDict):
    """Preprocessing keywords after adapter defaults are applied."""

    image_type: str
    z_projection: str
    crop_to_tissue: bool
    remove_background: bool
    pixel_size: float | None
    force_recomputing: bool


@dataclass(frozen=True)
class ImageSettings:
    """Options that change the written image; ``pixel_size`` is the µm/px metadata fallback."""

    image_type: str
    z_projection: str
    crop_to_tissue: bool
    remove_background: bool
    pixel_size: float | None

    def fingerprint_settings(self) -> dict[str, object]:
        """Cache-key values with stable casts."""
        return {
            "image_type": str(self.image_type),
            "z_projection": str(self.z_projection),
            "crop_to_tissue": bool(self.crop_to_tissue),
            "remove_background": bool(self.remove_background),
            "pixel_size": None if self.pixel_size is None else float(self.pixel_size),
        }
