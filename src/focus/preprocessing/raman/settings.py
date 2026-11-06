"""Fixed Raman (SRS) calibration: method version and every constant that shapes the output."""

from __future__ import annotations

from focus.io import ome_write
from focus.preprocessing.raman import flatfield, stitching
from focus.spatial import spectral

# Bump whenever the same input and settings produce a different output image.
METHOD_VERSION = "raman-3"

# Spectral denoising: the rank is chosen on spectra averaged over cellular-scale bins
# (spectral.CELLULAR_BIN_UM), then every pixel is projected onto those components.
RANK_BIN_UM = spectral.CELLULAR_BIN_UM
# Spectra sampled for the decomposition.
DECOMPOSITION_SAMPLES = spectral.DECOMPOSITION_SAMPLES
# Stored values (linear intensities above the detector offset) lie on a grid of 1/4 detector count.
# FOCUS design choice: 4 times finer than the converter step; on P762 the rounding adds less than
# 0.5 % to the variance of the denoised measurement noise, and the float32 tiles compress to about
# 0.75 byte per sample.
QUANTIZATION_COUNTS = 2.0**-2
# Saturated fraction of a channel above which the activity log warns.
SATURATION_WARNING = 0.01

# Low-resolution views (µm per pixel) for placing acquisitions on each other: a rotation search over
# the full circle on the search view, refined on the coarse view, then block matching on the fine
# view.
PLACEMENT_SEARCH_UM = 9.0
PLACEMENT_COARSE_UM = 4.5
PLACEMENT_FINE_UM = 1.2
# Rotation search, degrees: the full circle in steps of the first value on the search view, then
# +-(previous step) in steps of each following value on the coarse view. A slide put back on the
# stage between scans turns by a fraction of a degree to a few degrees (0.64 on P762).
PLACEMENT_ROTATION_STEPS_DEG = (5.0, 1.0, 0.25)
# Block matching on the fine view: block side (px), search radius of each pass (px), the quantile of
# the peak correlation of mismatched block pairs that an accepted block must beat, how many such
# pairs, the RANSAC inlier distance (fine px) and the fewest inlier blocks for a fitted transform.
PLACEMENT_BLOCK_PX = 96
PLACEMENT_BLOCK_SEARCH_PX = (24, 6)
PLACEMENT_BLOCK_QUANTILE = 99.0
PLACEMENT_NULL_PAIRS = 200
PLACEMENT_MAX_BLOCKS = 600
PLACEMENT_RANSAC_PX = 2.0
PLACEMENT_MIN_BLOCKS = 8
# Placements moving an acquisition further than this from its stage position are reported.
PLACEMENT_REPORT_UM = 20.0
# A fitted scale this far (fraction) from the ratio of the recorded pixel sizes, or a median block
# residual above this (µm), is reported.
PLACEMENT_SCALE_WARNING = 0.01
PLACEMENT_RESIDUAL_WARNING_UM = 5.0
# Detection canvas for the tissue outline and the display windows, µm per pixel, and the smoothing
# of the channel mean before Otsu's threshold.
DETECTION_UM = 4.5
TISSUE_SMOOTH_UM = 27.0

# Display: the lipid (CH2 stretch) and protein (CH3 stretch) bands, cm-1, and their colours.
LIPID_CM = 2850.0
PROTEIN_CM = 2930.0
BAND_TOLERANCE_CM = 30.0
LIPID_COLOUR = "00FF00"
PROTEIN_COLOUR = "FF00FF"
OTHER_COLOUR = "FFFFFF"

# Output tiles: zstd without a predictor. Bio-Formats 8.4 (QuPath 0.7) cannot read the TIFF
# floating-point predictor, and horizontal differencing would spread NaN along the rows.
CODEC: ome_write.Codec = ("zstd", {"level": 10}, None)


def calibration() -> dict[str, object]:
    """Every fixed constant, for the preprocessing fingerprint."""
    return {
        "flatfield_smooth_um": flatfield.SMOOTH_UM,
        "flatfield_tissue_tile_percentile": flatfield.TISSUE_TILE_PERCENTILE,
        "flatfield_min_tiles": flatfield.MIN_TILES,
        "offset_block_um": flatfield.OFFSET_BLOCK_UM,
        "offset_min_channels": flatfield.OFFSET_MIN_CHANNELS,
        "offset_min_r2": flatfield.OFFSET_MIN_R2,
        "offset_min_snr": flatfield.OFFSET_MIN_SNR,
        "max_shift_um": stitching.MAX_SHIFT_UM,
        "min_overlap_px": stitching.MIN_OVERLAP_PX,
        "whiten_sigma": stitching.WHITEN_SIGMA,
        "permutation_quantile": stitching.PERMUTATION_QUANTILE,
        "permutation_pairs": stitching.PERMUTATION_PAIRS,
        "max_residual_px": stitching.MAX_RESIDUAL_PX,
        "stage_prior": stitching.STAGE_PRIOR,
        "rank_bin_um": RANK_BIN_UM,
        "decomposition_samples": DECOMPOSITION_SAMPLES,
        "quantization_counts": QUANTIZATION_COUNTS,
        "placement_search_um": PLACEMENT_SEARCH_UM,
        "placement_coarse_um": PLACEMENT_COARSE_UM,
        "placement_fine_um": PLACEMENT_FINE_UM,
        "placement_rotation_steps_deg": list(PLACEMENT_ROTATION_STEPS_DEG),
        "placement_block_px": PLACEMENT_BLOCK_PX,
        "placement_block_search_px": list(PLACEMENT_BLOCK_SEARCH_PX),
        "placement_block_quantile": PLACEMENT_BLOCK_QUANTILE,
        "placement_null_pairs": PLACEMENT_NULL_PAIRS,
        "placement_max_blocks": PLACEMENT_MAX_BLOCKS,
        "placement_ransac_px": PLACEMENT_RANSAC_PX,
        "placement_min_blocks": PLACEMENT_MIN_BLOCKS,
        "detection_um": DETECTION_UM,
        "tissue_smooth_um": TISSUE_SMOOTH_UM,
        "tile": ome_write.TILE,
        "codec": [CODEC[0], CODEC[1], CODEC[2]],
    }
