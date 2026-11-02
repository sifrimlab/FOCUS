"""Fixed MSI processing thresholds, method version and progress-step declarations."""

import numpy as np


import logging


logger = logging.getLogger("focus.preprocessing.lipidomics")


# MSI dataset preprocessing steps, in order; 1, 2, 4 and 7 run per sample.
_DATASET_STEPS = (
    "Loading MSI data",
    "Selecting tissue spots",
    "Selecting recalibration references",
    "Collecting per-sample m/z peaks",
    "Building global m/z backbone",
    "Annotating features",
    "Interpolating intensities",
    "Merging samples",
)


def _step(index: int) -> tuple[int, int, str]:
    """(index, total, name) of MSI dataset step ``index`` for StepReporter."""
    return index, len(_DATASET_STEPS), _DATASET_STEPS[index - 1]


# Part of the preprocessing fingerprint: bump whenever the same input and settings produce a
# different output, so cached results are rebuilt.
METHOD_VERSION = "msi-12"


# --- m/z recalibration (docs/scientific/msi_methods.md §4) ---

# Line-level calibrant search half-width: this multiple of the mass tolerance, but at least
# _RECAL_MIN_SEARCH_PPM. It must exceed the largest expected drift.
_RECAL_SEARCH_FACTOR = 5

_RECAL_MIN_SEARCH_PPM = 50

# Matched (spectrum, reference) pairs a scan line needs before its own estimate is used.
_RECAL_MIN_LINE_MATCHES = 3

# The linear m/z term is fitted only when at least this many references, spanning at least this
# fraction of the reference m/z span, are matched on a line.
_RECAL_SLOPE_MIN_REFERENCES = 3

_RECAL_SLOPE_MIN_SPAN = 0.25

# Outlier cut-off of the slope fit, in robust standard deviations (1.4826 x MAD).
_RECAL_OUTLIER_SIGMA = 3.0

_MAD_TO_SIGMA = 1.4826

# Running-median window, in scan lines, applied to the line model.
_RECAL_LINE_SMOOTHING = 5

# Matched references, agreeing within one mass tolerance, a pixel needs before its own residual is used.
_RECAL_MIN_PIXEL_MATCHES = 3

# A calibrant match must reach this multiple of its spectrum's noise: 1.4826 x MAD of the intensities
# of the spectrum's noise peaks, the peaks outside the sample's recurrent m/z ranges (see
# noise_threshold). Relative to each spectrum, so it follows the intensity loss of a degrading laser
# instead of failing below a fixed intensity.
_SIGNAL_TO_NOISE = 5.0

# A spectrum with fewer noise peaks than this has no noise gate: its peaks are nearly all recurrent ions.
_NOISE_MIN_PEAKS = 10

# Peaks flattened per numba call (two float64 arrays); bounds the transient memory whatever the
# number of peaks per spectrum.
_BLOCK_PEAKS = 20_000_000

# Minimum number of automatically selected recalibration references per ion mode.
_RECALIBRATION_REFERENCES = 10

# A sample whose spectra match a reference less often than this is reported as a warning.
_RECAL_WARN_MATCHED = 0.5

# Half-width of the sample-level offset search, as a multiple of the line-level search window: how
# far from the dataset-wide reference masses a sample's calibrants are looked for.
_RECAL_COARSE_FACTOR = 10

# A reference supports an offset when, within one mass tolerance of it, it holds more signal peaks
# than its background explains at Poisson tail probability _RECAL_OFFSET_ALPHA. The offset is accepted
# when at least _RECAL_OFFSET_MIN_REFERENCES references support it and that many supporting
# references arise by chance anywhere in the search range with probability below _RECAL_OFFSET_CHANCE.
_RECAL_OFFSET_MIN_REFERENCES = 2

_RECAL_OFFSET_ALPHA = 1e-6

_RECAL_OFFSET_CHANCE = 1e-3


# No recurrence map: every peak of a spectrum counts as a noise peak (see noise_threshold).
_NO_RECURRENCE = (0, np.zeros(0, dtype=np.bool_), 1.0)


# --- m/z features (docs/scientific/msi_methods.md §4.1) ---

# Histogram bins per mass tolerance: 10 => bins a tenth of the tolerance wide.
_FEATURE_BINS_PER_TOLERANCE = 10

# Gaussian kernel FWHM (2 sqrt(2 ln 2) sigma), in mass tolerances, smoothing the peak density.
_FEATURE_KERNEL_FWHM = 1.0

# Half-width, in mass tolerances, of the running median giving the local background of the density.
_FEATURE_FLOOR_TOLERANCES = 50

# Width, in mass tolerances, of the blocks whose peak counts the running median is taken over.
_FEATURE_FLOOR_BLOCK = 5

# A density maximum is a feature when its range holds at least _FEATURE_MIN_PEAKS peaks, more than its
# background explains at Poisson tail probability _FEATURE_ALPHA.
_FEATURE_MIN_PEAKS = 5

_FEATURE_ALPHA = 1e-6


# Two samples' mass scales are compared when they share at least this many references (one reference
# of each within the calibrant search window).
_SCALE_MIN_SHARED = 3


# Microgrid background detection (see _detect_microgrid_spots).
# Fraction of spots an ion must occur in to be treated as background (matrix, standards, coating).
_MICROGRID_BACKGROUND_OCCUPANCY = 0.5

# Side, in spots, of the median window that models the local background. Cells must cover less than
# half of it for the median to follow the background only.
_MICROGRID_BACKGROUND_WINDOW = 9

# Minimum robust z-score of a seed spot. Raised automatically on large slides (see _MICROGRID_SEED_ALPHA).
_MICROGRID_SEED_Z = 4.0

# Expected number of pure-noise seeds per slide; sets the multiple-testing seed threshold.
_MICROGRID_SEED_ALPHA = 0.01

# Robust z-score a spot needs to join a seed's connected component.
_MICROGRID_GROW_Z = 2.0

# Components above this size are reported as suspect (debris, matrix crystals) but kept.
_MICROGRID_MAX_COMPONENT_SPOTS = 25

# Fallback ppm tolerance for the background ion grid when no mass tolerance is given.
_MICROGRID_DEFAULT_TOLERANCE_PPM = 10

# Spectra concatenated per vectorised block.
_MICROGRID_BLOCK_SPECTRA = 20_000
