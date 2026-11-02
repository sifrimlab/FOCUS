"""Fixed spatial limits and resolution-specific analytical clustering presets."""

from focus.spatial.types import ClusteringPreset

from focus.config.options import SpotResolution


# Spot clustering runs on at most this many units. Larger samples (and ST samples whose spots hold
# too little signal) are clustered on coarser units built by plan_spatial_units: k x k blocks of the
# acquisition lattice when one is detected, Voronoi supercells otherwise.
_SPATIAL_CAP = 250_000

# Features entering the clustering PCA, for ST (highly variable genes) and MSI (highly variable features).
N_TOP_FEATURES = 2000

# The signal criterion never coarsens a sample below this many units.
_MIN_UNITS = 1_000

# Lloyd iterations for Voronoi supercells: on uniform points, tile-occupancy CV falls from 0.55
# (no iteration) to 0.35 at 5, close to the Poisson floor; more iterations change little.
_LLOYD_ITERATIONS = 5


# Weight of the neighbourhood mean in the clustering embedding [sqrt(1 - w) * own PCs, sqrt(w) * mean
# PCs], the weighting BANKSY uses for domain segmentation (lambda = 0.8). Keeping each unit's own PCs
# matters: with the neighbourhood mean alone, adjacent units get near-identical embeddings, the kNN
# graph copies the spatial grid, and Leiden tiles homogeneous tissue into compact patches (observed
# with a 2-ring mean on a synthetic two-domain lattice: 15 to 17 patches instead of 2).
_NEIGHBOURHOOD_WEIGHT = 0.8


# Leiden at a fixed resolution also cuts continuous variation (gradients, smooth expression programs)
# into bands, more of them the more units a sample has. merge_unsupported_clusters merges adjacent
# clusters whose valley ratio (_valley_ratio) is at least this value. Calibrated on synthetic
# lattices: at 102k units, 3 domains under a smooth field and a depth field went from 6 Leiden
# clusters to 3 (adjusted Rand index 1.00) at 0.7 and 0.8, and to 4 at 0.9, and a gradient cut into
# 3 bands merged into 1; at 22.5k units, a domain covering 1.9% of the sample stayed separate
# (ratio 0.01).
_MERGE_VALLEY_RATIO = 0.8

# Histogram bins of the projected density in _valley_ratio.
_VALLEY_BINS = 512


# Clustering presets selected by a spot modality's `resolution` setting; only what differs is listed.
# smoothing_rings: neighbourhood (in units) whose mean enters each unit's clustering embedding
# (spatial_embedding). signal_target (ST only): grow units until the median unit holds this many
# counts; None keeps native units below the cap. Calibrated on a Visium HD 16 um sample and data
# derived from it (counts thinned 64x to emulate 2 um bins; 3 x 3 sums at 96 um pitch to emulate
# standard Visium): with a 50-count target the 2 um emulation gave a near-random map (neighbour
# coherence 0.06), 1000 gave coherent domains (0.71) and moved the 16 um sample to 32 um blocks
# (0.81); one ring lifted the standard Visium emulation from 0.37 to 0.67.
CLUSTERING_PRESETS: dict[str, ClusteringPreset] = {
    SpotResolution.STANDARD: {"smoothing_rings": 1, "signal_target": None},
    SpotResolution.SUB_CELLULAR: {"smoothing_rings": 2, "signal_target": 1000},
}


def clustering_preset(resolution: str) -> ClusteringPreset:
    """Preset of a `resolution` setting; raises ValueError for an unknown value."""
    if resolution not in CLUSTERING_PRESETS:
        raise ValueError(
            f"resolution must be one of {SpotResolution.list()}, got {resolution!r}"
        )
    return CLUSTERING_PRESETS[resolution]
