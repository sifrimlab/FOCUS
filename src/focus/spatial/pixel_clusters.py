"""Pixel phenotypes of multiplexed fluorescence images by pixel-level clustering (Pixie).

Follows Pixie (Liu et al. 2023) with the FlowSOM map and metaclustering (Van Gassen et al. 2015):

1. Every channel is smoothed with a Gaussian of ``BLUR_SIGMA_PX`` pixels.
2. Every pixel is divided by its total over the channels (pixels with zero total are left out).
3. Every channel is divided by its 99.9th percentile over the sampled pixels.
4. A ``SOM_SHAPE`` self-organising map is trained on the sampled pixels as FlowSOM trains it:
   codes initialised on random pixels, bubble neighbourhood on the Chebyshev grid distance, learning
   rate and radius decreasing linearly over ``SOM_PASSES`` passes (radius 0.5 once below 1), stop
   after a pass that moves the codes by less than 1 in total.
5. The mean of the pixels of every SOM node (nodes without pixels drop out), z-scored per channel
   and capped at ``ZSCORE_CAP``, is grouped by consensus hierarchical clustering (Monti et al.
   2003, ConsensusClusterPlus settings of FlowSOM: ``RESAMPLES`` resamples of ``RESAMPLE_ITEMS`` of
   the nodes, average linkage, Euclidean distance).

In Pixie the user sets the number of metaclusters and adjusts them by inspection. FOCUS adaptation:
the number is set by the automatic criterion of FlowSOM (``MetaClustering``, ``max = 20``): the elbow of the within-cluster
sum of squares of the consensus partitions for ``k = 1 .. MAX_METACLUSTERS``, after one pass of
3-point smoothing, located by the best fit of two straight lines. The sum of squares weights every
node by its pixels (FOCUS adaptation: the phenotypes describe pixels, and the sparse nodes that SOM
places between phenotypes would otherwise each count as much as a phenotype).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numba import njit

BLUR_SIGMA_PX = 2.0
SOM_SHAPE = (10, 10)
SOM_PASSES = 10
SOM_ALPHA = (0.05, 0.01)
SOM_RADIUS_QUANTILE = 0.67
ZSCORE_CAP = 3.0
MAX_METACLUSTERS = 20
RESAMPLES = 100
RESAMPLE_ITEMS = 0.9
ELBOW_SMOOTHING = 0.2
NORMALIZATION_QUANTILE = 99.9
_BATCH = 262_144


@dataclass(frozen=True)
class PixelClusters:
    """Trained Pixie model: channel scale, SOM codes and the metacluster of every node."""

    scale: np.ndarray  # (C,) 99.9th percentile of the pixel-normalized channels
    codes: np.ndarray  # (nodes, C) SOM codes in the normalized space
    metacluster: np.ndarray  # (nodes,) metacluster of every node, 0..k-1
    sse: list[float]  # within-cluster sum of squares for k = 1 .. MAX_METACLUSTERS

    @property
    def k(self) -> int:
        """Number of metaclusters."""
        return int(self.metacluster.max()) + 1


def blur(planes: np.ndarray) -> np.ndarray:
    """``(C, H, W)`` planes smoothed per channel with the Pixie Gaussian."""
    from scipy.ndimage import gaussian_filter

    return np.stack(
        [gaussian_filter(p, BLUR_SIGMA_PX, mode="nearest") for p in planes]
    ).astype(np.float32)


def normalize_pixels(pixels: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Each ``(N, C)`` pixel divided by its total; the mask of pixels with signal."""
    total = pixels.sum(axis=1)
    signal = np.isfinite(total) & (total > 0)
    out = np.zeros_like(pixels, dtype=np.float32)
    out[signal] = pixels[signal] / total[signal, None]
    return out, signal


@njit(cache=True)
def _train_som(
    data: np.ndarray,
    codes: np.ndarray,
    grid_distance: np.ndarray,
    order: np.ndarray,
    alpha: tuple[float, float],
    radius: float,
) -> None:
    """Online SOM updates in ``order``, as FlowSOM's ``C_SOM`` (Euclidean, bubble neighbourhood)."""
    total = order.size
    n = data.shape[0]
    threshold = radius
    step_down = radius / total
    change = 1.0
    for step in range(total):
        if step % n == 0:
            if change < 1.0:
                break
            change = 0.0
        x = data[order[step]]
        best, nearest = np.inf, 0
        for j in range(codes.shape[0]):
            d = ((codes[j] - x) ** 2).sum()
            if d < best:
                best, nearest = d, j
        if threshold < 1.0:
            threshold = 0.5
        rate = alpha[0] - (alpha[0] - alpha[1]) * step / total
        for j in range(codes.shape[0]):
            if grid_distance[nearest, j] <= threshold:
                delta = x - codes[j]
                change += np.abs(delta).sum()
                codes[j] += rate * delta
        threshold -= step_down


def _som(data: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """FlowSOM codes of ``data``: initialised on random pixels, ``SOM_PASSES`` passes."""
    rows, cols = SOM_SHAPE
    grid = np.array([(i, j) for j in range(cols) for i in range(rows)], np.float64)
    grid_distance = np.abs(grid[:, None] - grid[None]).max(axis=2)
    nodes = min(rows * cols, len(data))
    codes = data[rng.choice(len(data), nodes, replace=False)].astype(np.float64)
    grid_distance = grid_distance[:nodes, :nodes]
    radius = float(np.quantile(grid_distance, SOM_RADIUS_QUANTILE))
    order = rng.integers(0, len(data), SOM_PASSES * len(data))
    _train_som(data.astype(np.float64), codes, grid_distance, order, SOM_ALPHA, radius)
    return codes


def nearest_code(data: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """Index of the nearest code of every ``(N, C)`` row (Euclidean)."""
    norms = (codes**2).sum(axis=1)
    out = np.empty(len(data), dtype=np.int64)
    for start in range(0, len(data), _BATCH):
        block = np.asarray(data[start : start + _BATCH], dtype=np.float64)
        out[start : start + _BATCH] = np.argmin(norms - 2 * block @ codes.T, axis=1)
    return out


def consensus_partitions(
    items: np.ndarray, rng: np.random.Generator
) -> list[np.ndarray]:
    """Consensus classes of ``(n, C)`` items for ``k = 1 .. MAX_METACLUSTERS`` (0-based labels)."""
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform

    n = len(items)
    ks = list(range(2, min(MAX_METACLUSTERS, n) + 1))
    together = np.zeros((len(ks), n, n))
    sampled = np.zeros((n, n))
    size = max(2, int(round(RESAMPLE_ITEMS * n)))
    for _ in range(RESAMPLES):
        pick = np.sort(rng.choice(n, size, replace=False))
        tree = linkage(items[pick], method="average", metric="euclidean")
        sampled[np.ix_(pick, pick)] += 1
        for i, k in enumerate(ks):
            labels = fcluster(tree, k, criterion="maxclust")
            together[i][np.ix_(pick, pick)] += labels[:, None] == labels[None]
    partitions = [np.zeros(n, dtype=np.int64)]
    for i, k in enumerate(ks):
        consensus = together[i] / np.maximum(sampled, 1)
        np.fill_diagonal(consensus, 1.0)
        tree = linkage(squareform(1.0 - consensus, checks=False), method="average")
        partitions.append(fcluster(tree, k, criterion="maxclust").astype(np.int64) - 1)
    return partitions


def within_sse(items: np.ndarray, labels: np.ndarray, weights: np.ndarray) -> float:
    """Within-cluster sum of squares of a partition of ``weights``-weighted items."""
    total = 0.0
    for c in np.unique(labels):
        sel = labels == c
        w = weights[sel]
        centre = (items[sel] * w[:, None]).sum(axis=0) / w.sum()
        total += float((w * ((items[sel] - centre) ** 2).sum(axis=1)).sum())
    return total


def elbow(values: list[float]) -> int:
    """FlowSOM's ``DetermineNumberOfClusters``: the 1-based ``k`` at the elbow of ``values``.

    One in-place pass of 3-point smoothing, then the split ``i`` whose two least-squares lines
    (``1 .. i - 1`` and ``i .. n``) have the smallest sum of absolute residuals.
    """
    y = np.asarray(values, dtype=np.float64).copy()
    n = len(y)
    if n < 3:
        return n
    for i in range(1, n - 1):
        y[i] = (1 - ELBOW_SMOOTHING) * y[i] + ELBOW_SMOOTHING / 2 * (
            y[i - 1] + y[i + 1]
        )
    x = np.arange(1, n + 1, dtype=np.float64)

    def residual(a: int, b: int) -> float:
        if b - a < 2:
            return 0.0
        slope, intercept = np.polyfit(x[a:b], y[a:b], 1)
        return float(np.abs(y[a:b] - (slope * x[a:b] + intercept)).sum())

    best, optimal = np.inf, 1
    for i in range(2, n):
        r = residual(0, i - 1) + residual(i - 1, n)
        if r < best:
            best, optimal = r, i
    return optimal


def fit_pixel_clusters(pixels: np.ndarray, seed: int = 0) -> PixelClusters:
    """Pixie model of ``(N, C)`` blurred pixel intensities (pixels without signal are ignored)."""
    rng = np.random.default_rng(seed)
    normalized, signal = normalize_pixels(pixels)
    normalized = normalized[signal]
    if len(normalized) < 2:
        raise ValueError("too few pixels with signal to learn pixel phenotypes")
    scale = np.percentile(normalized, NORMALIZATION_QUANTILE, axis=0)
    scale = np.where(scale > 0, scale, 1.0).astype(np.float32)
    data = normalized / scale
    codes = _som(data, rng)
    # Pixie clusters the mean of the pixels of every SOM node: nodes without pixels drop out.
    nodes = nearest_code(data, codes)
    counts = np.bincount(nodes, minlength=len(codes))
    kept = np.flatnonzero(counts)
    sums = np.zeros_like(codes)
    np.add.at(sums, nodes, data)
    node_means = sums[kept] / counts[kept, None]
    centre, spread = node_means.mean(axis=0), node_means.std(axis=0)
    z = (node_means - centre) / np.where(spread > 0, spread, 1.0)
    items = np.clip(z, -ZSCORE_CAP, ZSCORE_CAP)
    partitions = consensus_partitions(items, rng)
    weights = counts[kept].astype(np.float64)
    sse = [within_sse(items, labels, weights) for labels in partitions]
    metacluster = partitions[elbow(sse) - 1]
    return PixelClusters(scale, codes[kept].astype(np.float32), metacluster, sse)


def assign_pixel_clusters(model: PixelClusters, pixels: np.ndarray) -> np.ndarray:
    """Metacluster of every ``(N, C)`` blurred pixel; ``-1`` for pixels without signal."""
    normalized, signal = normalize_pixels(pixels)
    labels = np.full(len(pixels), -1, dtype=np.int64)
    if signal.any():
        nodes = nearest_code(normalized[signal] / model.scale, model.codes)
        labels[signal] = model.metacluster[nodes]
    return labels
