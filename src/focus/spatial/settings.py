"""Fixed spatial limits and the preset-driven alignment clustering of spot modalities."""

from typing import Final

from focus.spatial.types import ClusteringParameters, ClusteringPreset

from focus.config.options import SpotResolution


# Spot clustering runs on at most this many units. Larger samples (and samples whose spots hold too
# little signal) are clustered on coarser units built by plan_spatial_units: k x k blocks of the
# acquisition lattice when one is detected, Voronoi supercells otherwise.
_SPATIAL_CAP = 250_000

# Features entering the clustering PCA, for ST (highly variable genes) and MSI (highly variable features).
N_TOP_FEATURES = 2000

# The signal criterion never coarsens a sample below this many units.
_MIN_UNITS = 1_000

# Lloyd iterations for Voronoi supercells: on uniform points, tile-occupancy CV falls from 0.55
# (no iteration) to 0.35 at 5, close to the Poisson floor; more iterations change little.
_LLOYD_ITERATIONS = 5


# Leiden resolution of the partition, four times that of the former smoothed clustering (0.5):
# Leiden overclusters every domain, and merge_unsupported_clusters joins the splits that no density
# valley separates. A partition at a low resolution joins distinct domains before the valley test
# can see them. Calibrated on synthetic lattices and a Visium HD sample
# (docs/scientific/spatial_clustering.md section 8).
_OVERCLUSTER_RESOLUTION = 2.0

# The resolution halves (down to a quarter) while the median Leiden cluster holds fewer units than
# this. On two halves of a cut Gaussian, the valley ratio falls below 0.8 in 25 % of cases at 50
# units per cluster, 8 % at 200, 4 % at 300 and 1 % at 500, and such false valleys keep fragments
# of one domain apart.
_MIN_CLUSTER_UNITS = 300

# merge_unsupported_clusters merges adjacent clusters whose valley ratio (_valley_ratio) is at least
# this value.
_MERGE_VALLEY_RATIO = 0.8

# Histogram bins of the projected density in _valley_ratio.
_VALLEY_BINS = 512

# Share of each tail of a projection left out of the density range of _valley_ratio, so a few
# outlying units cannot compress the density into a few bins and hide a valley.
_VALLEY_TAIL = 0.005

# Ordered levels of a gradient map (mode "gradient").
_GRADIENT_BINS = 8


# Clustering modes, set by a modality preset:
# - domains: tissue domains; overcluster, then merge the splits without a density valley
# - gradient: smooth gradients; ordered quantile levels of the first principal component
# - foreground: foreground against background only; no clustering
DOMAINS: Final = "domains"
GRADIENT: Final = "gradient"
FOREGROUND: Final = "foreground"

# Signal that sub_cellular aggregation grows units for.
COUNTS: Final = "counts"  # summed counts (ST UMIs)
FEATURES: Final = "features"  # features detected in the summed unit (MSI ions)

# Median signal a unit must reach under the sub_cellular resolution. Counts: calibrated on a Visium
# HD 16 um sample and data derived from it (counts thinned 64x to emulate 2 um bins): a 50-count
# target gave a near-random map (neighbour coherence 0.06), 1000 gave coherent domains (0.71).
# Features: calibrated on a MALDI-TOF tissue acquisition (10 x 20 um pixels, median 291 detected
# features) whose intensities were thinned binomially to emulate smaller pixels: at a median of 37
# and 12 features per pixel, native pixels gave one cluster and a random map; blocks reaching 200
# detected features gave coherent maps (coherence 0.55 and 0.62) and the closest agreement with the
# labels of the full data among the targets 50, 100, 200 and 400.
SIGNAL_TARGETS: dict[str, float] = {COUNTS: 1000, FEATURES: 200}

# Shared numerical settings of the clustering (modalities add their eligibility rule).
PARAMETERS: ClusteringParameters = {
    "n_top_features": N_TOP_FEATURES,
    "n_pcs": 50,
    "n_neighbors": 15,
    "leiden_resolution": _OVERCLUSTER_RESOLUTION,
    "min_cluster_units": _MIN_CLUSTER_UNITS,
    "merge_valley_ratio": _MERGE_VALLEY_RATIO,
    "gradient_bins": _GRADIENT_BINS,
    "random_state": 0,
    "max_units": _SPATIAL_CAP,
}


def clustering_preset(mode: str, resolution: str, signal: str) -> ClusteringPreset:
    """Preset of a clustering ``mode`` under a ``resolution`` setting.

    ``sub_cellular`` grows units until the median unit reaches ``SIGNAL_TARGETS[signal]``;
    ``standard`` keeps native units below the cap. The foreground mode never aggregates.
    Raises ValueError for an unknown mode, resolution or signal.
    """
    if mode not in (DOMAINS, GRADIENT, FOREGROUND):
        raise ValueError(f"Unknown clustering mode {mode!r}")
    if resolution not in SpotResolution.list():
        raise ValueError(
            f"resolution must be one of {SpotResolution.list()}, got {resolution!r}"
        )
    if signal not in SIGNAL_TARGETS:
        raise ValueError(f"Unknown clustering signal {signal!r}")
    grow = resolution == SpotResolution.SUB_CELLULAR and mode != FOREGROUND
    return {
        "mode": mode,
        "signal": signal if grow else None,
        "signal_target": SIGNAL_TARGETS[signal] if grow else None,
    }
