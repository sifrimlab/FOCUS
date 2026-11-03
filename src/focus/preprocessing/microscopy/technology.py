"""Image-type presets: how ``image_type`` and ``z_projection`` calibrate microscopy preprocessing.

Every value here is fixed (not a user setting) and enters the preprocessing fingerprint through
:func:`calibration`, so changing one rebuilds the affected outputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from focus.config.options import MicroscopyImageType, MicroscopyZProjection

# Tissue detection runs on a canvas of about 8 µm per pixel, the 1.25x base magnification of
# HistoQC (Janowczyk et al. 2019). The canvas keeps at least 1024 px on its short side, so small
# fields of view are not reduced to a few pixels, and at most 9 MP.
DETECTION_UM_PER_PX = 8.0
DETECTION_MIN_SIDE_PX = 1024
DETECTION_MAX_PIXELS = 3000 * 3000

# Brightfield: median blur of the HSV saturation and Otsu threshold, then morphological closing
# (CLAM segmentTissue: median kernel 7 px, closing 4 px at its segmentation level; Lu et al.
# 2021). FOCUS adaptation: the kernels are expressed in µm at the 8 µm/px canvas.
MEDIAN_BLUR_UM = 7 * DETECTION_UM_PER_PX
CLOSING_UM = 4 * DETECTION_UM_PER_PX

# Fluorescence: Gaussian smoothing of the channel maximum before the triangle threshold
# (Zack et al. 1977). FOCUS design choice: 2 canvas pixels.
FLUORESCENCE_SMOOTH_UM = 2 * DETECTION_UM_PER_PX
# Each channel is scaled to this percentile before the channel maximum is taken.
FLUORESCENCE_CHANNEL_PERCENTILE = 99.9

# Tissue pieces smaller than 0.05 mm^2 (a 250 µm core) are debris. The threshold is capped at a
# tenth of the largest piece, so the largest piece and pieces of comparable size are always kept
# (TMA cores, small biopsies). Without a pixel size, the threshold is 1% of the canvas area.
MIN_TISSUE_AREA_UM2 = 0.05e6
MIN_TISSUE_FRACTION_NO_SIZE = 0.01
LARGEST_FRACTION = 0.1
# Specks of at most this many canvas pixels are removed before hole filling.
SPECK_PIXELS = 50

# Brightfield padding: border-connected regions whose channels are all at most this 8-bit value
# are unscanned background (compression noise of 1 to 5 included).
BLACK_LEVEL = 32
# Polarity probe (last-resort image-type evidence): the border median must be below this fraction
# of the 99th-percentile luma for a dark background.
DARK_BORDER_RATIO = 1 / 3

# Crop margin around the tissue bounding box; 250 px when the image has no pixel size.
CROP_MARGIN_UM = 100.0
CROP_MARGIN_PX = 250

# Display windows: 0.35% of tissue pixels saturated at each end, the ImageJ "Auto" contrast
# default (Schneider et al. 2012). Display only: stored pixels are never rescaled.
DISPLAY_SATURATION_PERCENT = 0.35

# Output pyramid: 512 px tiles, levels halve each side until the image fits in one tile.
TILE = 512
# Lossless codecs read by tifffile and Bio-Formats 8: JPEG 2000 reversible for 8-bit brightfield,
# zstd with a horizontal predictor otherwise. JPEG-XL is smaller but Bio-Formats 8.4 cannot read it.
BRIGHTFIELD_CODEC: tuple[str, dict[str, object]] = ("jpeg2000", {"reversible": True})
DEFAULT_CODEC: tuple[str, dict[str, object]] = ("zstd", {"level": 10})


@dataclass(frozen=True)
class ImageProfile:
    """The calibration of one resolved image type and the requested Z reduction."""

    image_type: str  # brightfield or fluorescence (resolved)
    z_projection: str = MicroscopyZProjection.AUTO

    @property
    def brightfield(self) -> bool:
        """True for transmitted-light acquisitions."""
        return self.image_type == MicroscopyImageType.BRIGHTFIELD

    @property
    def z_rule(self) -> str:
        """Resolve ``auto``: maximum projection for fluorescence, best focus for brightfield."""
        if self.z_projection != MicroscopyZProjection.AUTO:
            return self.z_projection
        if self.brightfield:
            return MicroscopyZProjection.BEST_FOCUS
        return MicroscopyZProjection.MAX

    def fill_value(self, dtype: np.dtype) -> float:
        """Background fill: white (dtype maximum) for brightfield, 0 for fluorescence."""
        if not self.brightfield:
            return 0.0
        dtype = np.dtype(dtype)
        if np.issubdtype(dtype, np.integer):
            return float(np.iinfo(dtype).max)
        return 1.0

    def codec(self, dtype: np.dtype) -> tuple[str, dict[str, object], int | None]:
        """``(compression, compression arguments, predictor)`` for the output tiles."""
        dtype = np.dtype(dtype)
        if self.brightfield and dtype == np.uint8:
            name, args = BRIGHTFIELD_CODEC
            return name, dict(args), None
        name, args = DEFAULT_CODEC
        return name, dict(args), 3 if np.issubdtype(dtype, np.floating) else 2

    @property
    def encoder_eligible(self) -> bool:
        """Whether the H&E-pretrained image encoder (Prov-GigaPath) applies."""
        return self.brightfield


def calibration() -> dict[str, object]:
    """Every fixed constant, for the preprocessing fingerprint."""
    return {
        "detection_um_per_px": DETECTION_UM_PER_PX,
        "detection_min_side_px": DETECTION_MIN_SIDE_PX,
        "detection_max_pixels": DETECTION_MAX_PIXELS,
        "median_blur_um": MEDIAN_BLUR_UM,
        "closing_um": CLOSING_UM,
        "fluorescence_smooth_um": FLUORESCENCE_SMOOTH_UM,
        "fluorescence_channel_percentile": FLUORESCENCE_CHANNEL_PERCENTILE,
        "min_tissue_area_um2": MIN_TISSUE_AREA_UM2,
        "min_tissue_fraction_no_size": MIN_TISSUE_FRACTION_NO_SIZE,
        "largest_fraction": LARGEST_FRACTION,
        "speck_pixels": SPECK_PIXELS,
        "black_level": BLACK_LEVEL,
        "dark_border_ratio": DARK_BORDER_RATIO,
        "crop_margin_um": CROP_MARGIN_UM,
        "crop_margin_px": CROP_MARGIN_PX,
        "display_saturation_percent": DISPLAY_SATURATION_PERCENT,
        "tile": TILE,
        "brightfield_codec": [BRIGHTFIELD_CODEC[0], BRIGHTFIELD_CODEC[1]],
        "default_codec": [DEFAULT_CODEC[0], DEFAULT_CODEC[1]],
    }
