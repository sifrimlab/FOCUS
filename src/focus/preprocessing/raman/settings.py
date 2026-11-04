"""Fixed Raman (SRS) calibration: method version and every constant that shapes the output."""

from __future__ import annotations

from focus.io import ome_write
from focus.preprocessing.raman import flatfield, stitching

# Bump whenever the same input and settings produce a different output image.
METHOD_VERSION = "raman-2"

# Spectral denoising: the rank is chosen on spectra averaged over bins of this size (µm), the
# cellular scale at which compositional components rise above the per-pixel noise, then every pixel
# is projected onto those components. FOCUS design choice, calibrated on P762 (rank 0 to 1 per pixel,
# 2 to 3 at 4.5 µm).
RANK_BIN_UM = 4.5
# Spectra sampled for the decomposition.
DECOMPOSITION_SAMPLES = 300_000
# Stored values lie on a grid of this step: 1/4 of a detector count for intensities, 2^-8 for
# area-normalised spectra. FOCUS design choice: 4 times finer than the converter step; on P762 the
# rounding adds less than 0.5 % to the variance of the denoised measurement noise, and the float32
# tiles compress to about 0.75 byte per sample.
QUANTIZATION_COUNTS = 2.0**-2
QUANTIZATION_AREA = 2.0**-8
# Saturated fraction of a channel above which the activity log warns.
SATURATION_WARNING = 0.01

# Low-resolution views (µm per pixel) for placing acquisitions on each other: a coarse search
# without limit, then a refinement around it.
PLACEMENT_COARSE_UM = 4.5
PLACEMENT_FINE_UM = 1.2
# Placements moving an acquisition further than this from its stage position are reported.
PLACEMENT_REPORT_UM = 20.0
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
        "quantization_area": QUANTIZATION_AREA,
        "placement_coarse_um": PLACEMENT_COARSE_UM,
        "placement_fine_um": PLACEMENT_FINE_UM,
        "detection_um": DETECTION_UM,
        "tissue_smooth_um": TISSUE_SMOOTH_UM,
        "tile": ome_write.TILE,
        "codec": [CODEC[0], CODEC[1], CODEC[2]],
    }
