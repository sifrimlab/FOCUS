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

# Maps the resolved on-disk storage dtype to the OME-XML Pixels "type" attribute.
# ome_types expects the OME PixelType spelling ("float", not "float32").
_OME_PIXEL_TYPE = {
    np.dtype(np.uint8): "uint8",
    np.dtype(np.uint16): "uint16",
    np.dtype(np.float32): "float",
}
