"""Fixed MSI processing thresholds, method version and progress-step declarations."""

import logging

import numpy as np


logger = logging.getLogger("focus.preprocessing.msi")


# MSI dataset preprocessing steps, in order; 1, 2, 4 and 7 run per sample.
_DATASET_STEPS = (
    "Loading MSI data",
    "Selecting recalibration references",
    "Aligning sample mass scales",
    "Recalibrating spectra and selecting features",
    "Building global m/z backbone",
    "Annotating features",
    "Binning intensities",
    "Merging samples",
)


def _step(index: int) -> tuple[int, int, str]:
    """(index, total, name) of MSI dataset step ``index`` for StepReporter."""
    return index, len(_DATASET_STEPS), _DATASET_STEPS[index - 1]


# Part of the preprocessing fingerprint: bump whenever the same input and settings produce a
# different output, so cached results are rebuilt.
METHOD_VERSION = "msi-19"


# --- Dual polarity pairing (docs/scientific/msi_methods.md §2.2) ---

# Log-spaced m/z bands summed per spectrum during the binary scan; their composition is the
# content both polarities are registered on.
_PAIRING_BAND_EDGES = np.geomspace(50.0, 3000.0, 49)

# Principal components of the band composition per polarity, and the seeded subsample they are
# fitted on.
_PAIRING_COMPONENTS = 6

_PAIRING_PCA_SPECTRA = 30_000

# Coarse search grid: cells of this many pixel pitches (at least _PAIRING_MIN_CELLS across the
# shorter footprint side) and rotations every _PAIRING_ANGLE_STEP degrees over the full circle.
_PAIRING_COARSE_PITCHES = 4

_PAIRING_MIN_CELLS = 24

_PAIRING_ANGLE_STEP = 3.0

# High-pass Gaussian sigma, in pixel pitches (at most a sixth of the shorter footprint side):
# smooth fields correlate at any pose and are removed before scoring.
_PAIRING_HIGHPASS_PITCHES = 8

# A pose must overlap this fraction of the smaller footprint.
_PAIRING_MIN_OVERLAP = 0.05

# Verification: the best coarse score must exceed every competing pose (another translation more
# than _PAIRING_PEAK_EXCLUSION coarse cells away, or another rotation more than
# _PAIRING_ANGLE_EXCLUSION degrees away) by this factor, and the poses found with each half of the
# components must land within _PAIRING_MAX_DISAGREEMENT coarse cells of the final pose.
_PAIRING_MIN_MARGIN = 1.3

_PAIRING_PEAK_EXCLUSION = 8

_PAIRING_ANGLE_EXCLUSION = 10.0

_PAIRING_MAX_DISAGREEMENT = 2.0

# Final pose: the overlap is cut into square tiles of this many pixel pitches, each tile's shift is
# searched within _PAIRING_TILE_RADIUS pitches, and the rotation and translation are fitted to the
# tile shifts; tiles further than _PAIRING_TILE_OUTLIER pitches from the fit are dropped.
_PAIRING_TILE_PITCHES = 24

_PAIRING_TILE_RADIUS = 4

_PAIRING_TILE_OUTLIER = 1.5

# Lattice snap: the sub-pixel offset is applied when the nearest-neighbour displacements, taken
# modulo the pitch, have at least this circular coherence on both axes (commensurate lattices).
_PAIRING_SNAP_COHERENCE = 0.8


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


# --- Spectrum representation and centroiding (docs/scientific/msi_methods.md §1.1 and §1.2) ---

# The representation probe reads this many evenly spaced spectra; a spectrum with fewer points than
# _PROFILE_MIN_POINTS says nothing about the sampling and is skipped.
_PROFILE_PROBE_SPECTRA = 512

_PROFILE_MIN_POINTS = 8

# Log-spaced m/z bands over which the grid step is measured and the baseline and noise are estimated.
_PROFILE_BANDS = 64

# A spacing belongs to the sampling grid when it lies within this fraction of the band's grid step; a
# spectrum is a sampled profile when at least _PROFILE_REGULARITY of its spacings do.
_PROFILE_GRID_TOLERANCE = 0.25

_PROFILE_REGULARITY = 0.75

# Share of the probed spectra that must be sampled profiles for the file to be one; a file between the
# two bounds mixes both representations and is refused.
_PROFILE_SHARE = (0.1, 0.9)

# A sampled profile is picked only when its largest peaks span at least this many grid steps at half
# maximum (the median of each probed spectrum's _PROFILE_TOP_APEXES largest apexes), and its grid step
# is at most _PROFILE_MAX_STEP Da: a regular grid of coarser steps is a list of isotopes, not a profile.
_PROFILE_MIN_POINTS_PER_PEAK = 3.0

_PROFILE_MAX_STEP = 0.1

_PROFILE_TOP_APEXES = 10

# Only apexes at least this many noise deviations above the baseline are measured: a maximum of noise
# is one or two points wide whatever the sampling.
_PROFILE_APEX_SIGMA = 10.0

# Peak picking: two points are neighbours on the grid when closer than this multiple of the grid step;
# an apex is a peak when it stands this multiple of the baseline noise above the baseline; two apexes
# are one peak unless the signal between them falls below this fraction of the lower one.
_PICK_GAP = 1.5

_PICK_SIGNAL_TO_NOISE = 3.0

_PICK_VALLEY = 0.5

# Source points per picking batch: bounds the transient memory whatever the spectrum length.
_PICK_BATCH_POINTS = 4_000_000
