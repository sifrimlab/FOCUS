"""Parameters retained verbatim from the original sample implementation."""

import numpy as np


# Default processing parameters (all configurable via process_image)
_CROP_MARGIN_PX = 250
_CLIP_PERCENTILE = 99
_GAMMA = 0.45
_CONTRAST_SATURATION = 0.35
_MAX_PYRAMID_PIXELS = (
    3000 * 3000
)  # pixel cap for the smallest pyramid level (GUI rendering)
_DETECTION_MAX_PIXELS = (
    3000 * 3000
)  # pixel cap for the tissue-detection proxy (independent of the pyramid cap)

# Tissue-detection tuning, fixed relative to the ~3000px detection canvas (see _DETECTION_MAX_PIXELS)
# rather than the source image's native resolution - detection always runs on a canvas of at most
# this size, so a single dataset-independent value is appropriate (not user-configurable).
_DETECTION_BLUR_KERNEL_SIZE = 25  # ~0.8% of the ~3000px canvas width; odd
# Speck-noise filter: remove_small_objects(max_size=...) drops every component of at most this
# area (inclusive); min_object_coverage does the real area filtering.
_DETECTION_MAX_OBJECT_SIZE = 50
# The min_object_coverage threshold is capped at this fraction of the largest detected object, so
# the largest object and pieces of comparable size are kept on slides with small tissue (TMA cores,
# biopsies). Sections larger than 10% of the canvas keep the plain min_object_coverage threshold.
_DETECTION_MIN_LARGEST_FRACTION = 0.1
# Brightfield padding: border-connected regions whose channels are all at most this 8-bit value
# are background. Gamma 0.45 and the contrast stretch lift 8-bit padding noise of 1 to 5 to about
# 25 before detection.
_DETECTION_BLACK_LEVEL = 32
# Fluorescence polarity: the border median must be below this fraction of the 99th-percentile luma.
_DETECTION_DARK_BORDER_RATIO = 1 / 3

# Maps the resolved on-disk storage dtype to the OME-XML Pixels "type" attribute.
# ome_types expects the OME PixelType spelling ("float", not "float32").
_OME_PIXEL_TYPE = {
    np.dtype(np.uint8): "uint8",
    np.dtype(np.uint16): "uint16",
    np.dtype(np.float32): "float",
}
