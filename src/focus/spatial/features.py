"""Spatially coherent features of the clustering units: selection for clustering, feature images.

Feature selection by dispersion (highly variable features) favours whatever varies most, which on
MSI includes acquisition artifacts: scan-line defects and slow drifts along the scan. Morphology
shows instead as features that are coherent at the scale of tissue structures. Each candidate
feature is scored on its *band-passed* map: the large-scale background (a Gaussian of
`BACKGROUND_SIGMA` units, normalised over the occupied cells) is subtracted, and the score is the
correlation of the residual with the mean residual of the unit's 1-ring neighbours. Features
whose residual is dominated by whole lines of the unit grid (`MAX_LINE_SHARE`) are left out, as
line-scan artifacts.

The best `n_spatial_features` enter the clustering PCA; the best `n_image_features` that are
detected widely enough and mutually dissimilar become per-spot feature images for the alignment
tool. Units and every intermediate stay in memory.
"""

from dataclasses import dataclass
from typing import cast

import numpy as np
import scipy.sparse as sp
from scipy.ndimage import gaussian_filter, median_filter
from scipy.spatial import cKDTree

from focus.spatial.smoothing import smoothing_operator
from focus.spatial.units import SpatialUnits

# Large-scale background removed before scoring, in units of the median unit spacing.
BACKGROUND_SIGMA = 8.0
# Candidates: the expressed features with the highest mean over the units, the top
# `CANDIDATE_SHARE` of them but at least `MIN_CANDIDATES` (all, when fewer are expressed) and at
# most `MAX_CANDIDATES` (scored in column blocks of `BLOCK`).
CANDIDATE_SHARE = 0.3
MIN_CANDIDATES = 300
MAX_CANDIDATES = 1500
BLOCK = 128
# A feature whose residual variance lies more than this share in *thin* line deviations (the means
# of whole grid rows or columns minus their running median over `LINE_WINDOW` lines) is a line
# artifact: a defective scan band is a few lines wide (7 lines on a prostate MALDI section), while a
# domain boundary along a row is a step that the running median follows.
MAX_LINE_SHARE = 0.15
LINE_WINDOW = 21
# Features less coherent than this are noise at the tissue scale and are never selected (on the
# prostate MSI samples the 100 selected features scored 0.38-0.75).
MIN_COHERENCE = 0.1
# Feature images: detected (nonzero) in at least this share of the units, and correlated with
# every image already chosen by less than this (absolute, on the residuals).
MIN_IMAGE_DETECTION = 0.2
MAX_IMAGE_CORRELATION = 0.5


@dataclass
class SpatialFeatures:
    """Scored candidates of one sample, best first."""

    columns: np.ndarray  # (k,) column indices into the represented matrix, best first
    coherence: np.ndarray  # (k,) neighbour correlation of the band-passed residual
    image_columns: np.ndarray  # (m,) columns chosen as feature images, best first


def _raster(centroids: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """Cell (row, col) of every unit on a grid of the median nearest-neighbour spacing."""
    if len(centroids) < 2:
        return np.zeros((len(centroids), 2), dtype=np.intp), (1, 1)
    distance = np.asarray(cKDTree(centroids).query(centroids, k=2)[0])[:, 1]
    spacing = float(np.median(distance[distance > 0])) if np.any(distance > 0) else 1.0
    cells = np.round((centroids - centroids.min(axis=0)) / spacing).astype(np.intp)
    cells = cells[:, ::-1]  # (row = y, col = x)
    return cells, (int(cells[:, 0].max()) + 1, int(cells[:, 1].max()) + 1)


def _residuals(
    values: np.ndarray, cells: np.ndarray, shape: tuple[int, int]
) -> np.ndarray:
    """``values`` (units x features) minus their Gaussian background on the unit raster."""
    flat = cells[:, 0] * shape[1] + cells[:, 1]
    count = np.bincount(flat, minlength=shape[0] * shape[1]).reshape(shape)
    weight = gaussian_filter(count.astype(np.float64), BACKGROUND_SIGMA)
    out = np.empty_like(values)
    for j in range(values.shape[1]):
        total = np.bincount(flat, weights=values[:, j], minlength=count.size)
        smooth = gaussian_filter(total.reshape(shape), BACKGROUND_SIGMA)
        background = smooth / np.maximum(weight, 1e-12)
        out[:, j] = values[:, j] - background[cells[:, 0], cells[:, 1]]
    return out


def _neighbour_mean(units: SpatialUnits, unit_ok: np.ndarray) -> sp.csr_matrix:
    """Row-normalised mean over each kept unit's 1-ring neighbours, itself excluded."""
    operator = smoothing_operator(units, 1, unit_ok).tocoo()
    off = operator.row != operator.col
    adjacency = sp.csr_matrix(
        (np.ones(int(off.sum())), (operator.row[off], operator.col[off])),
        shape=operator.shape,
    )
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    return cast(
        sp.csr_matrix, sp.diags(1.0 / np.maximum(degree, 1.0)) @ adjacency
    ).tocsr()


def _line_share(
    residual: np.ndarray, cells: np.ndarray, shape: tuple[int, int]
) -> np.ndarray:
    """Per feature, the larger share of residual variance in thin row or column deviations."""
    variance = residual.var(axis=0) + 1e-12
    share = np.zeros(residual.shape[1])
    for axis in (0, 1):
        line = cells[:, axis]
        counts = np.bincount(line, minlength=shape[axis]).astype(np.float64)
        means = (
            np.stack(
                [
                    np.bincount(line, weights=residual[:, j], minlength=shape[axis])
                    for j in range(residual.shape[1])
                ],
                axis=1,
            )
            / np.maximum(counts, 1.0)[:, None]
        )
        thin = means - median_filter(means, size=(LINE_WINDOW, 1), mode="nearest")
        thin[counts == 0] = 0.0
        share = np.maximum(share, thin[line].var(axis=0) / variance)
    return share


def _candidates(mean: np.ndarray, expressed: np.ndarray) -> np.ndarray:
    """The expressed columns with the highest mean, as many as the candidate rules allow."""
    n = min(MAX_CANDIDATES, max(int(CANDIDATE_SHARE * expressed.size), MIN_CANDIDATES))
    return np.asarray(expressed[np.argsort(-mean[expressed], kind="stable")][:n])


def _column_correlation(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pearson correlation of every column of ``a`` with the same column of ``b`` (-inf if flat)."""
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    denominator = np.sqrt((a**2).sum(axis=0) * (b**2).sum(axis=0))
    return np.where(
        denominator > 0, (a * b).sum(axis=0) / np.maximum(denominator, 1e-300), -np.inf
    )


def _choose_images(
    ranked: np.ndarray, detection: np.ndarray, n_images: int
) -> list[int]:
    """Positions of the ranked residual columns chosen as images: detected widely enough, and
    correlated with every image already chosen by less than `MAX_IMAGE_CORRELATION`."""
    images: list[int] = []
    for i in range(ranked.shape[1]):
        if len(images) >= n_images:
            break
        if detection[i] < MIN_IMAGE_DETECTION:
            continue
        if all(
            abs(np.corrcoef(ranked[:, i], ranked[:, p])[0, 1]) < MAX_IMAGE_CORRELATION
            for p in images
        ):
            images.append(i)
    return images


def rank_spatial_features(
    X: sp.csr_matrix, units: SpatialUnits, unit_ok: np.ndarray, n_images: int
) -> SpatialFeatures:
    """
    Score the candidate columns of ``X`` (kept units x features, log scale) by spatial coherence,
    best first, and choose up to ``n_images`` of them as feature images.
    """
    columns = sp.csc_matrix(X, dtype=np.float64)
    n = columns.shape[0]
    mean = np.asarray(columns.mean(axis=0)).ravel()
    expressed = np.flatnonzero(mean > 0)
    empty = SpatialFeatures(
        np.array([], dtype=np.intp), np.array([]), np.array([], dtype=np.intp)
    )
    if n < 10 or expressed.size == 0:
        return empty
    candidates = _candidates(mean, expressed)
    cells, shape = _raster(np.asarray(units.centroids)[np.flatnonzero(unit_ok)])
    neighbours = _neighbour_mean(units, unit_ok)
    has_neighbours = np.asarray(neighbours.sum(axis=1)).ravel() > 0
    coherence = np.full(candidates.size, -np.inf)
    share = np.ones(candidates.size)
    residuals = np.empty((n, candidates.size))
    for start in range(0, candidates.size, BLOCK):
        block = slice(start, start + BLOCK)
        values = columns[:, candidates[block]].toarray()
        r = _residuals(values, cells, shape)
        residuals[:, block] = r
        nb = neighbours @ r
        coherence[block] = _column_correlation(r[has_neighbours], nb[has_neighbours])
        share[block] = _line_share(r, cells, shape)
    keep = np.flatnonzero(
        (share <= MAX_LINE_SHARE)
        & np.isfinite(coherence)
        & (coherence >= MIN_COHERENCE)
    )
    order = keep[np.argsort(-coherence[keep], kind="stable")]
    detection = np.diff(columns.indptr)[candidates[order]] / n
    images = _choose_images(residuals[:, order], detection, n_images)
    return SpatialFeatures(
        candidates[order],
        coherence[order],
        candidates[order][np.asarray(images, dtype=np.intp)],
    )


def image_values(X: sp.csr_matrix, columns: np.ndarray) -> np.ndarray:
    """(rows of ``X`` x m) uint8 display values of ``columns``: 1..255 over the 1st-99th percentile.

    0 is reserved for spots without a value (none in ``X``).
    """
    out = np.zeros((X.shape[0], len(columns)), dtype=np.uint8)
    if not len(columns):
        return out
    values = sp.csc_matrix(X)[:, columns].toarray()
    for j in range(values.shape[1]):
        lo, hi = np.percentile(values[:, j], [1, 99])
        scaled = np.clip((values[:, j] - lo) / max(hi - lo, 1e-12), 0.0, 1.0)
        out[:, j] = (1 + np.round(scaled * 254)).astype(np.uint8)
    return out
