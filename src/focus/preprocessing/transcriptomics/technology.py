"""Technology presets: how (capture, unit) calibrates ST quality control, genes and normalization.

Every value here is fixed (not a user setting) and enters the preprocessing fingerprint through
:func:`calibration`, so changing one rebuilds the affected outputs.
"""

from dataclasses import dataclass

from focus.config.options import SpotResolution, StCapture, StNormalization, StUnit

# Spatially aware local outliers (spot units): robust z of a spot against its nearest spots,
# SpotSweeper's defaults (36 neighbours, cutoff 3; Totty et al. 2025).
LOCAL_NEIGHBOURS = 36
LOCAL_CUTOFF = 3.0

# Global low-tail outliers (cell units): median - 5 MAD on the log scale (Germain et al. 2020;
# the lenient end of the 3 to 5 MAD range of Heumos et al. 2023).
GLOBAL_NMADS = 5.0

# Sequencing panels: a gene is kept when it is detected in at least this many observations of at
# least one sample (Heumos et al. 2023). Imaging panels are designed, so every detected gene is kept.
MIN_GENE_OBSERVATIONS = {StCapture.SEQUENCING: 20, StCapture.IMAGING: 1}

# Non-gene features removed before any QC: probes and codewords of imaging panels, recognised by
# var['feature_types'] (anything but 'Gene Expression') or by these name prefixes.
GENE_FEATURE_TYPE = "Gene Expression"
CONTROL_PREFIXES = (
    "NegControlProbe_",
    "NegControlCodeword_",
    "NegPrb",
    "BLANK_",
    "Blank-",
    "UnassignedCodeword_",
    "DeprecatedCodeword_",
    "Intergenic_",
    "SystemControl",
)

# obs columns holding the segmented-cell area, in lookup order (Xenium, MERFISH, CosMx).
AREA_COLUMNS = ("cell_area", "area_um2", "area", "Area", "volume")

AREA_KEY = "focus_area"


@dataclass(frozen=True)
class Technology:
    """The calibration of one (capture, unit) pair."""

    capture: str
    unit: str

    @property
    def resolution(self) -> str:
        """Clustering preset for the alignment labels."""
        return (
            SpotResolution.STANDARD
            if self.unit == StUnit.SPOT
            else SpotResolution.SUB_CELLULAR
        )

    @property
    def outlier_rule(self) -> str:
        """``local`` for spots, ``global`` for cells, ``none`` for bins."""
        return {StUnit.SPOT: "local", StUnit.CELL: "global", StUnit.BIN: "none"}[
            self.unit
        ]

    @property
    def min_gene_observations(self) -> int:
        """Detection count a gene needs in one sample to stay in the merged panel."""
        return MIN_GENE_OBSERVATIONS[self.capture]

    def normalization(self, requested: str) -> str:
        """Resolve ``auto``: log-normalize for sequencing, area normalization for imaging."""
        if requested != StNormalization.AUTO:
            return requested
        if self.capture == StCapture.IMAGING:
            return StNormalization.AREA_LOG_NORMALIZE
        return StNormalization.LOG_NORMALIZE


def technology(capture: str, unit: str) -> Technology:
    """Return the calibration of ``(capture, unit)``; raises ValueError for unknown values."""
    if capture not in StCapture.list():
        raise ValueError(f"capture must be one of {StCapture.list()}, got {capture!r}")
    if unit not in StUnit.list():
        raise ValueError(f"unit must be one of {StUnit.list()}, got {unit!r}")
    return Technology(capture, unit)


def calibration() -> dict[str, object]:
    """Every fixed value above, for the preprocessing fingerprint."""
    return {
        "local_neighbours": LOCAL_NEIGHBOURS,
        "local_cutoff": LOCAL_CUTOFF,
        "global_nmads": GLOBAL_NMADS,
        "min_gene_observations": dict(MIN_GENE_OBSERVATIONS),
        "control_prefixes": list(CONTROL_PREFIXES),
        "area_columns": list(AREA_COLUMNS),
    }
