"""Sample-type presets: how `sample_type` calibrates MSI foreground detection and feature selection.

Every value here is fixed (not a user setting) and enters the preprocessing fingerprint through
:func:`calibration`, so changing one rebuilds the affected outputs.
"""

from dataclasses import asdict, dataclass

from focus.config.options import MsiSampleType
from focus.spatial.settings import DOMAINS, FOREGROUND, GRADIENT

# --- Per-sample feature selection (docs/scientific/msi_methods.md §5) ---

# A sample feature is kept when its presence is at least this fraction of the presence of the
# sample's most present feature (MALDIquant filterPeaks minFrequency; Gibb and Strimmer 2012).
FREQUENCY_THRESHOLD = 0.01

# Signal-to-noise a peak needs to count: the 3-sigma detection limit (Long and Winefordner 1983).
SIGNAL_TO_NOISE = 3.0

# The S/N filter needs an independent background: at least this many background spectra, and at
# least this share of the sample's spectra.
SNR_MIN_BACKGROUND_SPECTRA = 100
SNR_MIN_BACKGROUND_FRACTION = 0.01

# Every window of the background noise profile must hold at least this many noise peaks.
SNR_MIN_NOISE_PEAKS = 50

# Share of the foreground TIC removed by the S/N filter above which a warning is reported.
SNR_WARN_TIC_FRACTION = 0.05

# Decoy annotation: the features are matched again at masses shifted by these multiples of the
# window width (2 x mass tolerance), and the mean number of decoy matches estimates the matches
# expected by chance (target-decoy; Elias and Gygi 2007). A chance share above SNR_WARN_CHANCE of the
# annotated features means the annotation protects noise from the S/N filter.
DECOY_OFFSETS = (-7, -5, -3, 3, 5, 7)
DECOY_WARN_CHANCE = 0.5

# --- Intensity binning (docs/scientific/msi_methods.md §7) ---

# Bounds of a backbone feature's Gaussian width, in mass tolerances: one feature-grid bin, and a
# full width at half maximum of one tolerance.
SIGMA_MIN_TOLERANCES = 0.1
SIGMA_MAX_TOLERANCES = 1.0 / 2.3548

# --- Foreground detection (docs/scientific/msi_methods.md §4) ---

# A connected foreground component smaller than this share of the largest one is removed
# (tissue_noisy cleanup).
COMPONENT_MIN_FRACTION = 0.1

# The two score components must be separated by at least this Ashman's D,
# sqrt(2) |mu_1 - mu_2| / sqrt(s_1^2 + s_2^2), the criterion for a clean separation of two
# Gaussians (Ashman et al. 1994); otherwise every spectrum is foreground. BIC alone also splits
# tissue regions that differ in their number of ions.
MIN_SEPARATION = 2.0

# A background holds a small subset of the foreground's recurrent ions (matrix ions, or noise that
# never recurs): the lower component must hold at most this share of the recurrent ions of the
# higher one. Two tissue regions that differ in their number of ions stay foreground.
MAX_BACKGROUND_RATIO = 0.5

# With a lipid database, the share of a spectrum's intensity in the annotated recurrent ions is a
# tissue score: matrix-covered glass holds as many recurrent ions as tissue (matrix clusters), but
# few lipids. It is used when at least this many recurrent ions are annotated...
LIPID_MIN_ANNOTATED = 10
# ...and they hold at least this many times the intensity share of the ions annotated at decoy
# masses (DECOY_OFFSETS): weighted by intensity like the score, so a dense database that annotates
# many weak ions by chance still qualifies when the intense ions are true lipids.
LIPID_MIN_ENRICHMENT = 2.0
# ...and its lower component must hold at most this share of the lipid fraction of the higher one;
# otherwise two tissue regions differ in lipid content and the recurrent-ion score decides.
LIPID_MAX_BACKGROUND_RATIO = 0.5


@dataclass(frozen=True)
class SampleProfile:
    """The foreground calibration of one `sample_type`."""

    sample_type: str
    # "mixture": GMM 1 vs 2 components by BIC on the recurrent-ion score; "microgrid": local outliers
    # against the background ions.
    detector: str
    # Side, in pixels, of the median filter applied to the score on the raster before classification
    # (0 = none).
    score_median: int
    # Spatial cleanup of the classified mask: "fill_open", "fill_open_components", "majority", "none".
    cleanup: str
    # Side, in pixels, of the square structuring element of the opening or majority vote.
    cleanup_size: int
    # Alignment clustering mode (focus.spatial.settings): tissue domains, ordered gradient levels,
    # or foreground against background without clustering.
    clustering_mode: str
    # Dual polarity pairing when the content registration cannot verify a pose: True pairs by the
    # recorded stage positions with a warning (content without shared shape, such as smooth
    # cultures); False stops the sample.
    stage_pairing_fallback: bool


_PROFILES = {
    MsiSampleType.TISSUE: SampleProfile(
        MsiSampleType.TISSUE, "mixture", 0, "fill_open", 3, DOMAINS, False
    ),
    MsiSampleType.TISSUE_NOISY: SampleProfile(
        MsiSampleType.TISSUE_NOISY,
        "mixture",
        3,
        "fill_open_components",
        5,
        DOMAINS,
        False,
    ),
    MsiSampleType.CELL_CULTURE: SampleProfile(
        MsiSampleType.CELL_CULTURE, "mixture", 0, "majority", 3, GRADIENT, True
    ),
    MsiSampleType.MICROGRID: SampleProfile(
        MsiSampleType.MICROGRID, "microgrid", 0, "none", 0, FOREGROUND, False
    ),
}


def sample_profile(sample_type: str) -> SampleProfile:
    """Calibration of a `sample_type`; raises ValueError for an unknown value."""
    if sample_type not in _PROFILES:
        raise ValueError(
            f"sample_type must be one of {MsiSampleType.list()}, got {sample_type!r}"
        )
    return _PROFILES[sample_type]


def calibration() -> dict[str, object]:
    """Every fixed value of this module, for the preprocessing fingerprint."""
    return {
        "frequency_threshold": FREQUENCY_THRESHOLD,
        "signal_to_noise": SIGNAL_TO_NOISE,
        "snr_min_background_spectra": SNR_MIN_BACKGROUND_SPECTRA,
        "snr_min_background_fraction": SNR_MIN_BACKGROUND_FRACTION,
        "snr_min_noise_peaks": SNR_MIN_NOISE_PEAKS,
        "decoy_offsets": list(DECOY_OFFSETS),
        "sigma_tolerances": [SIGMA_MIN_TOLERANCES, SIGMA_MAX_TOLERANCES],
        "component_min_fraction": COMPONENT_MIN_FRACTION,
        "min_separation": MIN_SEPARATION,
        "max_background_ratio": MAX_BACKGROUND_RATIO,
        "lipid_min_annotated": LIPID_MIN_ANNOTATED,
        "lipid_min_enrichment": LIPID_MIN_ENRICHMENT,
        "lipid_max_background_ratio": LIPID_MAX_BACKGROUND_RATIO,
        "profiles": {name: asdict(profile) for name, profile in _PROFILES.items()},
    }
