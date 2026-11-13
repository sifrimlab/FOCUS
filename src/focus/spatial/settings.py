"""Fixed spatial limits and the preset-driven alignment clustering of spot modalities."""

from typing import Final

from focus.spatial.types import ClusteringParameters, ClusteringPreset

from focus.config.options import SpotResolution


# Spot clustering runs on at most this many units. Larger samples (and samples whose spots hold too
# little signal) are clustered on coarser units built by plan_spatial_units: k x k blocks of the
# acquisition lattice when one is detected, Voronoi supercells otherwise.
_SPATIAL_CAP = 250_000

# Features entering the clustering PCA under the dispersion selection (highly variable genes or
# features); the fallback when too few features are spatially coherent.
N_TOP_FEATURES = 2000

# Feature selection of the clustering: "spatial" keeps the features most coherent at the scale of
# tissue structures (focus.spatial.features), "dispersion" the highly variable ones. On noisy MSI
# acquisitions (prostate, 30 um MALDI) the dispersion selection followed scan-line defects and
# drifts and Leiden cut straight-edged blocks; the 100 most coherent features (mostly lipids,
# neighbour correlation of the band-passed map 0.5-0.75, against a median of 0.08-0.26) gave
# clusters along glands, lumens and stroma.
SPATIAL: Final = "spatial"
DISPERSION: Final = "dispersion"
N_SPATIAL_FEATURES = 100

# The spatial selection needs at least this many coherent features; otherwise (very sparse units,
# where only the sum of many weak features carries the signal) the dispersion selection is used.
# On a sparse synthetic raster (0.03-0.09 counts per feature and pixel, 3 x 3 blocks) 4 features
# passed and gave a random map, while all 300 features found both domains.
MIN_SPATIAL_FEATURES = 20

# Per-spot feature images offered by the alignment tool next to the clusters.
N_IMAGE_FEATURES = 12

# The signal criterion never coarsens a sample below this many units.
_MIN_UNITS = 1_000

# Lloyd iterations for Voronoi supercells: on uniform points, tile-occupancy CV falls from 0.55
# (no iteration) to 0.35 at 5, close to the Poisson floor; more iterations change little.
_LLOYD_ITERATIONS = 5


# Leiden overclusters the units into about this many clusters (the resolution is searched for it),
# and merge_unsupported_clusters joins the splits that no density valley separates. The number of
# clusters at a fixed resolution grows with the number of units (MALDI-TOF samples of 71k and 183k
# spectra and Visium HD samples of 25k blocks needed resolutions 0.5 to 2.0); every sample that
# matched its domains had 22 to 29 Leiden clusters (docs/scientific/spatial_clustering.md section 8).
_LEIDEN_CLUSTERS = 40

# The Leiden target is capped so the mean cluster holds at least this many units. On two halves of a cut Gaussian, the valley ratio falls below 0.8 in 25 % of cases at 50
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

# Weight of the neighbourhood mean in the clustering embedding [sqrt(1 - w) * own PCs, sqrt(w) *
# neighbourhood mean PCs], the weighting BANKSY uses for domain segmentation (lambda = 0.8). Keeping
# each unit's own PCs matters: with the neighbourhood mean alone, adjacent units get near-identical
# embeddings, the kNN graph copies the spatial grid, and Leiden tiles homogeneous tissue into
# compact patches.
_NEIGHBOURHOOD_WEIGHT = 0.8

# Neighbourhood rings of the embedding under each resolution (similarity_operator). Calibrated on
# Visium HD and MALDI-TOF samples (docs/scientific/spatial_clustering.md section 8).
NEIGHBOURHOOD_RINGS: dict[str, int] = {
    SpotResolution.STANDARD: 1,
    SpotResolution.SUB_CELLULAR: 2,
}

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
    "leiden_clusters": _LEIDEN_CLUSTERS,
    "min_cluster_units": _MIN_CLUSTER_UNITS,
    "merge_valley_ratio": _MERGE_VALLEY_RATIO,
    "gradient_bins": _GRADIENT_BINS,
    "random_state": 0,
    "max_units": _SPATIAL_CAP,
    "feature_selection": SPATIAL,
    "n_spatial_features": N_SPATIAL_FEATURES,
    "n_image_features": N_IMAGE_FEATURES,
}


def clustering_preset(mode: str, resolution: str, signal: str) -> ClusteringPreset:
    """Preset of a clustering ``mode`` under a ``resolution`` setting.

    ``sub_cellular`` grows units until the median unit reaches ``SIGNAL_TARGETS[signal]``;
    ``standard`` keeps native units below the cap. ``rings`` sets the edge-preserving
    neighbourhood of the embedding. The foreground mode never aggregates and has no neighbourhood.
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
        "rings": 0 if mode == FOREGROUND else NEIGHBOURHOOD_RINGS[resolution],
    }
