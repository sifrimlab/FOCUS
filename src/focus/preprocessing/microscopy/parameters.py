"""Internal microscopy settings and the existing serialized fingerprint representation."""

from dataclasses import dataclass
from typing import TypedDict


class MicroscopyImageSettings(TypedDict):
    """Existing preprocessing keywords after adapter defaults are applied."""

    color_enhancement: bool
    remove_background: bool
    crop_to_tissue: bool
    background_color: str
    min_object_coverage: float
    force_recomputing: bool
    clip_percentile: int
    crop_margin: int
    gamma: float
    contrast_saturation: float


@dataclass(frozen=True)
class ImageSettings:
    """Options for enhancement, tissue masking and cropping; distances are source pixels."""

    color_enhancement: bool
    remove_background: bool
    crop_to_tissue: bool
    background_color: str
    min_object_coverage: float
    clip_percentile: int
    crop_margin: int
    gamma: float
    contrast_saturation: float

    def fingerprint_settings(self) -> dict[str, object]:
        """Return the original cache-key values, casts and key order."""
        return {
            "color_enhancement": bool(self.color_enhancement),
            "remove_background": bool(self.remove_background),
            "crop_to_tissue": bool(self.crop_to_tissue),
            "background_color": str(self.background_color),
            "min_object_coverage": float(self.min_object_coverage),
            "clip_percentile": float(self.clip_percentile),
            "crop_margin": float(self.crop_margin),
            "gamma": float(self.gamma),
            "contrast_saturation": float(self.contrast_saturation),
        }
