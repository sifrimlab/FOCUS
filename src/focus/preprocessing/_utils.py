import os
import gc
import logging
from typing import NamedTuple

import numpy as np
import scipy.sparse as sp

from focus.constants import FocusOutputDirectories, SpotResolution
from focus.reporting import StepReporter, get_reporter  # noqa: F401  (re-exported for pipeline modules)

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

# Clustering presets selected by a spot modality's `resolution` setting; only what differs is listed.
# smoothing_rings: neighbourhood (in units) whose mean enters each unit's clustering embedding
# (spatial_embedding). signal_target (ST only): grow units until the median unit holds this many
# counts; None keeps native units below the cap. Calibrated on a Visium HD 16 um sample and data
# derived from it (counts thinned 64x to emulate 2 um bins; 3 x 3 sums at 96 um pitch to emulate
# standard Visium): with a 50-count target the 2 um emulation gave a near-random map (neighbour
# coherence 0.06), 1000 gave coherent domains (0.71) and moved the 16 um sample to 32 um blocks
# (0.81); one ring lifted the standard Visium emulation from 0.37 to 0.67.
CLUSTERING_PRESETS = {
	SpotResolution.STANDARD: {'smoothing_rings': 1, 'signal_target': None},
	SpotResolution.SUB_CELLULAR: {'smoothing_rings': 2, 'signal_target': 1000},
}


def clustering_preset(resolution: str) -> dict:
	"""Preset of a `resolution` setting; raises ValueError for an unknown value."""
	if resolution not in CLUSTERING_PRESETS:
		raise ValueError(f"resolution must be one of {SpotResolution.list()}, got {resolution!r}")
	return CLUSTERING_PRESETS[resolution]


def validate_path_readable(path: str) -> None:
	"""Validate that a path exists and is readable.

	Raises FileNotFoundError if the path does not exist,
	PermissionError if the path is not readable.
	"""
	if not os.path.exists(path):
		raise FileNotFoundError(f"The specified path does not exist: {path}")
	if not os.access(path, os.R_OK):
		raise PermissionError(f"The specified path is not readable: {path}")


def discover_sample_ids(path: str, ignore_samples: list[str] | None = None) -> list[str]:
	"""Discover sample IDs from subdirectories under the given path.

	Returns a sorted list of directory names, excluding standard FOCUS output directories
	and any sample IDs listed in ignore_samples.
	"""
	excluded = set(FocusOutputDirectories.list()) | set(ignore_samples or [])
	sample_ids = sorted(
		d for d in os.listdir(path)
		if os.path.isdir(os.path.join(path, d)) and d not in excluded
	)
	return sample_ids


_IMZML_EXTENSION = '.imzML'
_IBD_EXTENSION = '.ibd'


def find_imzml_pair(directory: str) -> tuple[str, str] | None:
	"""Resolve one MSI ion mode directory to its complete imzML/IBD file pair.

	This is the single source of truth for "was this ion mode acquired?". Directory existence is
	NOT evidence: the GUI (SampleManager) scaffolds both pos/ and neg/ for every MSI modality, so
	an empty ion mode directory must read as "this polarity was not acquired" rather than as
	"a second polarity is present".

	Returns the absolute (imzML, IBD) paths, or None when the directory is missing or holds
	neither an imzML nor an IBD file — i.e. this ion mode was not acquired.

	Raises FileNotFoundError when the directory holds imzML and/or IBD files but no pair sharing
	a base name. A file of either kind means the user meant to use this ion mode, so a partial
	pair is a broken or half-transferred acquisition and must never be silently skipped.
	"""
	if not os.path.isdir(directory):
		return None

	# Files only: a *directory* named "data.ibd" must not satisfy the pair.
	names = sorted(f for f in os.listdir(directory) if os.path.isfile(os.path.join(directory, f)))
	imzml_files = [f for f in names if f.endswith(_IMZML_EXTENSION)]
	ibd_files = [f for f in names if f.endswith(_IBD_EXTENSION)]

	# Neither kind of file: this ion mode was not acquired. This is what lets the GUI scaffold
	# both pos/ and neg/ unconditionally without the user having to delete the unused one.
	if not imzml_files and not ibd_files:
		return None

	# Something is here, so this ion mode was intended: it must resolve to a complete pair.
	# Sorted listing -> deterministic choice when a directory holds several acquisitions
	# (os.listdir order is filesystem-dependent).
	ibd_stems = {f[: -len(_IBD_EXTENSION)] for f in ibd_files}
	for name in imzml_files:
		stem = name[: -len(_IMZML_EXTENSION)]
		if stem in ibd_stems:
			return os.path.join(directory, name), os.path.join(directory, stem + _IBD_EXTENSION)

	raise FileNotFoundError(
		f"Incomplete MSI acquisition in '{directory}': found {_IMZML_EXTENSION} files "
		f"{imzml_files or '[]'} and {_IBD_EXTENSION} files {ibd_files or '[]'}, but no pair "
		f"sharing a base name. Each ion mode directory must hold a complete "
		f"{_IMZML_EXTENSION} + {_IBD_EXTENSION} pair, or be left empty if that ion mode was "
		f"not acquired."
	)


def create_output_directories(path: str, sample_ids: list[str], modality_name: str) -> None:
	"""Create per-sample and merged preprocessing output directories."""
	for sample_id in sample_ids:
		os.makedirs(
			os.path.join(path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name),
			exist_ok=True,
		)
	os.makedirs(
		os.path.join(path, FocusOutputDirectories.MERGED, FocusOutputDirectories.PREPROCESSING),
		exist_ok=True,
	)


def _unique_rows(idx: np.ndarray) -> bool:
	span = idx.max(axis=0) + 1
	return np.unique(idx[:, 0] * span[1] + idx[:, 1]).size == len(idx)


def _direction_gap(a: np.ndarray, b) -> np.ndarray:
	"""Smallest angle (radians) between undirected directions ``a`` and ``b``, all in [0, pi)."""
	return np.abs(((a - b + np.pi / 2) % np.pi) - np.pi / 2)


def infer_lattice(coords: np.ndarray, sample: int = 20_000, seed: int = 0) -> tuple[np.ndarray, np.ndarray] | None:
	"""Integer lattice indices of every spot from the coordinates alone, or ``None`` if irregular.

	Works for square lattices at any rotation (Visium HD), hexagonal lattices (standard Visium,
	expressed in a 60 degree basis) and rectangular rasters (MSI), with tissue holes. No metadata
	beyond the coordinates is needed; ``spot_size`` is a footprint and can differ from the pitch.

	1. On a seeded sample of at most ``sample`` spots, the 8 nearest displacements of each spot
	   give the basis: ``a`` is the median vector of the dominant direction among nearest-shell
	   displacements (length <= 1.2 x median nearest distance); ``b`` is the shortest displacement
	   cluster more than 25 degrees away from ``a`` (length <= 3 x the nearest distance).
	2. Starting from the spot closest to the median position, spots within 20 steps are rounded
	   to indices and the basis and origin are refitted by least squares; the radius triples until
	   it holds every spot. Refitting on a growing region keeps a small basis error from
	   accumulating into rounding mistakes thousands of steps away.
	3. The lattice is accepted only if the 99th percentile residual is <= 0.1 step, the maximum
	   residual is <= 0.45 step (rounding stays unambiguous, so a few displaced spots do not
	   reject the lattice), and every spot has its own lattice site.

	Returns
	-------
	tuple[np.ndarray, np.ndarray] | None
		``(idx, basis)``: (n, 2) int64 indices starting at 0, and the (2, 2) basis whose row ``i``
		is the physical displacement of one step along ``idx[:, i]`` (``coords ~ idx @ basis + o``).
	"""
	from scipy.spatial import cKDTree

	coords = np.asarray(coords, dtype=np.float64)
	n = len(coords)
	if n < 16 or not np.isfinite(coords).all():
		return None
	rng = np.random.default_rng(seed)
	probe = coords[rng.choice(n, min(sample, n), replace=False)]
	dist, nb = cKDTree(coords).query(probe, k=9)
	vec = (coords[nb[:, 1:]] - probe[:, None, :]).reshape(-1, 2)
	length = dist[:, 1:].ravel()
	nearest = float(np.median(dist[:, 1]))
	if not nearest > 0:
		return None
	angle = np.mod(np.arctan2(vec[:, 1], vec[:, 0]), np.pi)

	def axis(candidates):
		hist, edges = np.histogram(angle[candidates], bins=360, range=(0, np.pi))
		mode = edges[hist.argmax()] + np.pi / 720
		v = vec[candidates & (_direction_gap(angle, mode) < np.deg2rad(5))]
		return np.median(v * np.sign(v @ [np.cos(mode), np.sin(mode)])[:, None], axis=0), mode

	a, mode = axis(length <= 1.2 * nearest)
	other = (_direction_gap(angle, mode) > np.deg2rad(25)) & (length <= 3 * nearest)
	if not other.any():
		return None
	b, _ = axis(other & (length <= 1.15 * np.quantile(length[other], 0.02)))
	basis = np.vstack([a, b])
	if abs(np.linalg.det(basis)) < 1e-3 * nearest ** 2:
		return None
	origin = coords[np.argmin(np.linalg.norm(coords - np.median(coords, axis=0), axis=1))]
	radius = 20 * nearest
	while True:
		inside = np.linalg.norm(coords - origin, axis=1) <= radius
		idx = np.rint(np.linalg.solve(basis.T, (coords[inside] - origin).T).T)
		design = np.column_stack([idx, np.ones(len(idx))])
		if np.linalg.matrix_rank(design) == 3:
			fit = np.linalg.lstsq(design, coords[inside], rcond=None)[0]
			basis, origin = fit[:2], fit[2]
		if inside.all():
			break
		radius *= 3
	idx = np.rint(np.linalg.solve(basis.T, (coords - origin).T).T)
	residual = np.linalg.norm(coords - (idx @ basis + origin), axis=1) / np.linalg.norm(basis, axis=1).min()
	idx = idx.astype(np.int64)
	idx -= idx.min(axis=0)
	if np.quantile(residual, 0.99) > 0.1 or residual.max() > 0.45 or not _unique_rows(idx):
		return None
	return idx, basis


def _lattice_units(idx: np.ndarray, k: int) -> np.ndarray:
	"""Flat id of the k x k lattice block holding each spot (blocks anchored at index 0)."""
	q = idx // k
	return q[:, 0] * (int(q[:, 1].max()) + 1) + q[:, 1]


def _unit_sizes_and_sums(flat: np.ndarray, weights: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray | None]:
	"""Spot count and summed ``weights`` of every occupied unit id in ``flat``."""
	if flat.max() < 4 * len(flat) + 1_000_000:
		sizes = np.bincount(flat)
		keep = sizes > 0
		sums = None if weights is None else np.bincount(flat, weights=weights)[keep]
		return sizes[keep], sums
	_, inverse = np.unique(flat, return_inverse=True)
	return np.bincount(inverse), None if weights is None else np.bincount(inverse, weights=weights)


def _voronoi_units(coords: np.ndarray, m: int, seed: int, iterations: int = _LLOYD_ITERATIONS) -> tuple[np.ndarray, int]:
	"""Assign spots to ``m`` Voronoi tiles: seeded spot sample as seeds, then Lloyd iterations.

	Seeds follow spot density, and each Lloyd iteration moves a seed to the centroid of its tile,
	so tiles hold similar spot counts. Nothing about the tiling is periodic.
	"""
	from scipy.spatial import cKDTree

	rng = np.random.default_rng(seed)
	seeds = coords[np.sort(rng.choice(len(coords), size=m, replace=False))]
	for iteration in range(iterations + 1):
		_, nearest = cKDTree(seeds).query(coords, workers=-1)
		if iteration == iterations:
			break
		size = np.bincount(nearest, minlength=m)
		filled = size > 0
		for axis in (0, 1):
			seeds[filled, axis] = np.bincount(nearest, weights=coords[:, axis], minlength=m)[filled] / size[filled]
	uniq, bin_ids = np.unique(nearest, return_inverse=True)
	return bin_ids.astype(np.intp), int(uniq.size)


class SpatialUnits(NamedTuple):
	"""Clustering units of one sample (see :func:`plan_spatial_units`); never persisted."""
	bin_ids: np.ndarray | None   # (n_spots,) unit of each spot; None when every spot is its own unit
	n_units: int
	info: dict                   # provenance
	centroids: np.ndarray        # (n_units, 2) unit centroid
	grid: np.ndarray | None      # (n_units, 2) unit lattice index; None for irregular layouts
	basis: np.ndarray | None     # (2, 2) physical step of one unit-grid index


def _unit_centroids(coords: np.ndarray, bin_ids: np.ndarray, n_units: int) -> np.ndarray:
	size = np.bincount(bin_ids, minlength=n_units)
	return np.column_stack([np.bincount(bin_ids, weights=coords[:, i], minlength=n_units) / size for i in (0, 1)])


def plan_spatial_units(
	coords: np.ndarray,
	*,
	cap: int = _SPATIAL_CAP,
	counts: np.ndarray | None = None,
	min_median_counts: float | None = None,
	seed: int = 0,
) -> SpatialUnits:
	"""Choose the units spot clustering runs on, bounding its cost without a periodic imprint.

	The layout is read from the coordinates (:func:`infer_lattice`). Spots are clustered as they
	are unless the sample has more than ``cap`` spots or (when ``counts`` and
	``min_median_counts`` are given, ST sub-cellular) the median spot holds fewer than
	``min_median_counts``. Otherwise spots are grouped into units; the caller sums each unit's
	expression (:func:`_bin_sum_matrix`) and every spot inherits its unit's label.

	- **lattice**: the unit of a spot is ``(i // k, j // k)`` on its lattice indices, a block of
	  k x k sites (a parallelogram on hexagonal lattices). Every interior block holds exactly k^2
	  sites, so unit occupancy cannot vary periodically, and the blocks follow the lattice, not the
	  image axes. ``k`` is the smallest integer with at most ``cap`` occupied blocks whose median
	  block count reaches ``min_median_counts``; the signal criterion stops before fewer than
	  ``_MIN_UNITS`` blocks remain.
	- **voronoi** (no lattice: segmented cells, beads): ``m`` tiles from :func:`_voronoi_units`,
	  ``m = min(cap, n / s)`` where ``s`` spots per tile is the estimate that reaches the
	  median-count target (never fewer than ``_MIN_UNITS`` tiles).

	Fully deterministic for a given ``seed``. The returned geometry (unit grid and basis, or unit
	centroids) defines the neighbourhoods of :func:`smoothing_operator`.
	"""
	coords = np.asarray(coords, dtype=np.float64)
	n = len(coords)
	signal = counts is not None and min_median_counts is not None and n > _MIN_UNITS
	counts = None if counts is None else np.asarray(counts, dtype=np.float64)
	need_cap = n > cap
	need_signal = signal and float(np.median(counts)) < min_median_counts
	limited = False
	lattice = infer_lattice(coords, seed=seed)

	if lattice is not None:
		idx, basis = lattice
		step = np.linalg.norm(basis, axis=1).tolist()
		k, driver = 1, []
		if need_cap:
			k = max(1, int(np.sqrt(n / cap)))
			while len(_unit_sizes_and_sums(_lattice_units(idx, k))[0]) > cap:
				k += 1
			driver.append('cap')
		if need_signal:
			k_cap = k
			while True:
				_, sums = _unit_sizes_and_sums(_lattice_units(idx, k), counts)
				if np.median(sums) >= min_median_counts:
					break
				if len(_unit_sizes_and_sums(_lattice_units(idx, k + 1))[0]) < _MIN_UNITS:
					limited = True
					break
				k += 1
			if k > k_cap:
				driver.append('signal')
		if k == 1:
			return SpatialUnits(None, n, {'method': 'native', 'layout': 'lattice', 'step': step, 'n_units': n,
				'signal_limited': limited}, coords, idx, basis)
		width = int(idx[:, 1].max() // k) + 1
		uniq, bin_ids = np.unique(_lattice_units(idx, k), return_inverse=True)
		bin_ids = bin_ids.astype(np.intp)
		n_units = int(uniq.size)
		info = {'method': 'lattice', 'layout': 'lattice', 'step': step, 'k': k,
			'unit_size': [k * v for v in step], 'n_units': n_units,
			'driver': '+'.join(driver), 'signal_limited': limited}
		return SpatialUnits(bin_ids, n_units, info, _unit_centroids(coords, bin_ids, n_units),
			np.column_stack([uniq // width, uniq % width]), k * basis)

	m, driver = n, []
	if need_cap:
		m = cap
		driver.append('cap')
	if need_signal:
		median = float(np.median(counts))
		per_tile = int(np.ceil(min_median_counts / median)) if median > 0 else n
		m_signal = n // per_tile
		if m_signal < _MIN_UNITS:
			m_signal, limited = _MIN_UNITS, True
		if m_signal < m:
			m = m_signal
			driver.append('signal')
	if m >= n:
		return SpatialUnits(None, n, {'method': 'native', 'layout': 'irregular', 'n_units': n,
			'signal_limited': limited}, coords, None, None)
	bin_ids, n_units = _voronoi_units(coords, m, seed)
	info = {'method': 'voronoi', 'layout': 'irregular', 'n_units': n_units, 'lloyd_iterations': _LLOYD_ITERATIONS,
		'seed': seed, 'driver': '+'.join(driver), 'signal_limited': limited}
	return SpatialUnits(bin_ids, n_units, info, _unit_centroids(coords, bin_ids, n_units), None, None)


def smoothing_operator(units: SpatialUnits, rings: int, keep: np.ndarray | None = None) -> sp.csr_matrix:
	"""Row-normalised neighbourhood mean over the kept units, shape (n_kept, n_kept).

	Each kept unit averages itself and its kept neighbours. Applied to a PCA embedding before the
	kNN graph, it lets weak units borrow signal from adjacent tissue, so labels follow spatial
	domains instead of per-unit noise. The neighbourhood is isotropic in physical space:

	- lattice layouts: units whose grid offset has a physical length of at most (rings + 0.5)
	  times the longest grid step; for rings=1 that is 8 units on square grids and 6 on hexagonal
	  ones. Lookup uses sorted grid keys, O(n log n).
	- irregular layouts: the 3 * rings * (rings + 1) nearest unit centroids (6 for rings=1).

	``rings=0`` returns the identity. ``keep`` selects the units taking part (eligible units).
	"""
	keep = np.ones(units.n_units, dtype=bool) if keep is None else np.asarray(keep, dtype=bool)
	kept = np.flatnonzero(keep)
	m = len(kept)
	if rings <= 0 or m < 2:
		return sp.identity(m, dtype=np.float32, format='csr')
	rows, cols = [np.arange(m)], [np.arange(m)]
	if units.grid is not None:
		steps = np.linalg.norm(units.basis, axis=1)
		limit = (rings + 0.5) * steps.max()
		reach = int(np.ceil(limit / steps.min()))
		grid = units.grid[kept] - units.grid[kept].min(axis=0) + reach
		width = int(grid[:, 1].max()) + reach + 1
		key = grid[:, 0] * width + grid[:, 1]
		order = np.argsort(key)
		ordered = key[order]
		for di in range(-reach, reach + 1):
			for dj in range(-reach, reach + 1):
				if (di, dj) == (0, 0) or np.linalg.norm(np.array([di, dj]) @ units.basis) > limit * (1 + 1e-9):
					continue
				target = key + di * width + dj
				pos = np.minimum(np.searchsorted(ordered, target), m - 1)
				hit = ordered[pos] == target
				rows.append(np.flatnonzero(hit))
				cols.append(order[pos[hit]])
	else:
		from scipy.spatial import cKDTree
		k = min(3 * rings * (rings + 1), m - 1)
		centroids = units.centroids[kept]
		_, nb = cKDTree(centroids).query(centroids, k=k + 1, workers=-1)
		rows.append(np.repeat(np.arange(m), k))
		cols.append(nb[:, 1:].ravel())
	rows, cols = np.concatenate(rows), np.concatenate(cols)
	adjacency = sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(m, m))
	return (sp.diags(1.0 / np.asarray(adjacency.sum(axis=1)).ravel()) @ adjacency).tocsr()


def spatial_embedding(pcs: np.ndarray, operator: sp.csr_matrix, weight: float = _NEIGHBOURHOOD_WEIGHT) -> np.ndarray:
	"""Clustering embedding of each unit: its own PCs and its neighbourhood mean, weighted.

	``operator`` is a :func:`smoothing_operator`; ``weight`` is the share of the neighbourhood
	term (see ``_NEIGHBOURHOOD_WEIGHT``). Euclidean distances on the result are
	``(1 - weight) * d_own^2 + weight * d_neighbourhood^2``.
	"""
	return np.hstack([np.sqrt(1 - weight) * pcs, np.sqrt(weight) * (operator @ pcs)]).astype(np.float32)


def label_coherence(labels: np.ndarray, units: SpatialUnits, keep: np.ndarray | None = None) -> float | None:
	"""Agreement of labels with their 1-ring neighbours, chance-corrected (0 = random, 1 = perfect).

	Descriptive provenance for the clustering, computed on the kept units; ``None`` when undefined
	(fewer than two units or a single label).
	"""
	neighbours = smoothing_operator(units, 1, keep).tocoo()
	off = neighbours.row != neighbours.col
	if not off.any():
		return None
	observed = float(np.mean(labels[neighbours.row[off]] == labels[neighbours.col[off]]))
	_, freq = np.unique(labels, return_counts=True)
	chance = float(np.sum((freq / freq.sum()) ** 2))
	return None if chance >= 1 else (observed - chance) / (1 - chance)


def describe_units(n_spots: int, info: dict) -> str:
	"""One reporter line for a clustering unit plan."""
	if info['method'] == 'native':
		return f"Clustering: {n_spots} spots at native resolution ({info['layout']} layout)"
	if info['method'] == 'lattice':
		size = ' x '.join(f"{v:g}" for v in np.round(info['unit_size'], 2))
		return (f"Clustering: {n_spots} spots -> {info['n_units']} lattice blocks "
			f"(k={info['k']}, {size}; {info['driver']})")
	return f"Clustering: {n_spots} spots -> {info['n_units']} Voronoi supercells ({info['driver']})"


def _bin_sum_matrix(X, bin_ids: np.ndarray, n_bins: int):
	"""Sum the rows of ``X`` within each bin: returns ``A @ X`` of shape ``(n_bins, n_vars)``.

	``A`` is the ``(n_bins, n_obs)`` sparse 0/1 assignment matrix (one ``1`` per column at the
	point's bin). Sparse ``X`` stays sparse (CSR @ CSR -> CSR); dense ``X`` yields a dense result.
	The summed matrix is an internal, throwaway intermediate — it is never persisted.
	"""
	n_obs = X.shape[0]
	A = sp.csr_matrix(
		(np.ones(n_obs, dtype=np.float32), (bin_ids, np.arange(n_obs, dtype=np.intp))),
		shape=(n_bins, n_obs),
	)
	return A @ X


def compute_cluster_labels(
	X,
	*,
	leiden_resolution: float,
	normalize_target_sum: float | None,
	coordinates: np.ndarray | None = None,
	smoothing_rings: int = 1,
	cap: int = _SPATIAL_CAP,
	n_pcs_cap: int = 50,
	random_state: int = 0,
) -> np.ndarray:
	"""Compute per-spot cluster labels for alignment-stage spot colouring, scalably.

	The labels are consumed only by the alignment GUI for categorical colouring (no
	downstream algorithm uses them numerically), so the goal is a fast, "Leiden-like"
	partition rather than an exact Leiden over every spot — which does not scale to
	millions of points. Large samples are *coarsened* instead of subsampled, which also
	aggregates the weak per-spot signal of ultra-high-resolution modalities:

	1. When ``n_obs > cap`` (and ``coordinates`` are given), :func:`plan_spatial_units` groups
	   the spots into at most ``cap`` units: k x k blocks of the acquisition raster (inferred
	   from ``coordinates``) or Voronoi supercells when no raster is found. Every spot of a unit
	   is SUMMED into one pseudo-spot (:func:`_bin_sum_matrix`). Smaller samples run on every spot.
	2. Normalize the run matrix (see ``normalize_target_sum``), keep the ``N_TOP_FEATURES`` most
	   variable features (as ST does), run PCA, append each unit's
	   ``smoothing_rings`` neighbourhood mean to its PCs (:func:`spatial_embedding`), then
	   ``neighbors -> leiden``, bounded by ``cap`` rows.
	3. Propagate each unit's Leiden label back to every spot of the unit (identity when no
	   coarsening happened). No per-spot projection.

	``X`` is never mutated and the summed / normalized matrix lives only on a throwaway
	``AnnData`` that is discarded — none of these internal intermediates are persisted; the
	function returns only the per-spot label array. Fully deterministic (binning is RNG-free;
	PCA / Leiden seeded via ``random_state``).

	Parameters
	----------
	X : scipy.sparse matrix | np.ndarray
		Expression / intensity matrix, shape (n_obs, n_vars).
	leiden_resolution : float
		Resolution for the Leiden run on the (binned or full) run matrix.
	normalize_target_sum : float | None
		When a float (spatial transcriptomics), ``X`` holds raw counts: the run matrix is
		total-count normalized to this target and log1p-transformed before PCA. When ``None``
		(MSI, already per-spot normalized): if the run matrix was produced by summing bins it
		is total-count normalized (target = median) so per-bin occupancy washes out — summed
		bins are no longer per-spot normalized — otherwise the matrix is used as-is.
	coordinates : np.ndarray | None
		(n_obs, 2) spatial coordinates enabling coarsening. When ``None``, no coarsening is
		done and Leiden runs on every spot regardless of ``cap``.
	smoothing_rings : int
		Neighbourhood of the embedding smoothing (``CLUSTERING_PRESETS``); 0 disables it.
	cap : int
		Maximum number of rows Leiden runs on (= maximum number of units). Samples at or
		below this are clustered directly.
	n_pcs_cap : int
		Upper bound on the number of principal components.
	random_state : int
		Seed for PCA / Leiden.

	Returns
	-------
	np.ndarray
		Length-n_obs array of string cluster labels (dtype object). All ``'0'`` when the
		sample is too small to cluster or resolves to a single cluster.
	"""
	import anndata as ad
	import scanpy as sc

	n_obs, n_vars = X.shape

	# 1. Build the matrix Leiden runs on: summed units for large samples, else all spots.
	units, bin_ids = None, None
	if coordinates is not None:
		units = plan_spatial_units(coordinates, cap=cap, seed=random_state)
		bin_ids = units.bin_ids
		get_reporter().detail(describe_units(n_obs, units.info))
	if bin_ids is not None:
		X_run = _bin_sum_matrix(X, bin_ids, units.n_units)   # (n_units, n_vars); sparse-preserving, throwaway
		binned = True
	else:
		X_run = X.copy()                              # copy: normalize_total/log1p mutate in place
		binned = False

	n_run = X_run.shape[0]
	if n_run < 2 or n_vars < 3:
		return np.array(['0'] * n_obs, dtype=object)

	# 2. Normalize the run matrix, then pca -> neighbors -> leiden on the bounded matrix.
	sub = ad.AnnData(X=X_run)
	if normalize_target_sum is not None:
		# ST: run matrix holds (summed) raw counts -> total-count normalize + log1p.
		sc.pp.normalize_total(sub, target_sum=normalize_target_sum, inplace=True)
		sc.pp.log1p(sub)
	elif binned:
		# MSI: spots were already normalized, but SUMMING bins breaks that; re-normalize each
		# pseudo-spot (target = median) so cluster structure reflects composition, not how many
		# spots happened to fall in a bin. No log1p (matches MSI's unbinned no-log path).
		sc.pp.normalize_total(sub, inplace=True)
	# Same feature budget as ST. The 'cell_ranger' flavor ranks mean-binned normalized dispersion
	# without assuming log counts, so it holds for every MSI intensity normalization (CLR is negative).
	if sub.n_vars > N_TOP_FEATURES:
		sc.pp.highly_variable_genes(sub, flavor='cell_ranger', n_top_genes=N_TOP_FEATURES)
		sub = sub[:, sub.var['highly_variable'].to_numpy()].copy()
	n_pcs = min(n_pcs_cap, n_run - 1, sub.n_vars - 1)
	if n_pcs < 2:
		return np.array(['0'] * n_obs, dtype=object)
	sc.pp.pca(sub, n_comps=n_pcs)                     # random_state=0 by default
	sub.obsm['X_spatial'] = (spatial_embedding(sub.obsm['X_pca'], smoothing_operator(units, smoothing_rings))
		if units is not None and smoothing_rings > 0 else sub.obsm['X_pca'])
	sc.pp.neighbors(sub, n_neighbors=min(15, sub.n_obs - 1), use_rep='X_spatial')
	sc.tl.leiden(sub, resolution=leiden_resolution, key_added='leiden',
				 flavor='igraph', n_iterations=2, directed=False)
	run_labels = sub.obs['leiden'].to_numpy()
	del sub
	gc.collect()
	if units is not None:
		coherence = label_coherence(run_labels.astype(str), units)
		get_reporter().detail(f"Clustering: {np.unique(run_labels).size} clusters, smoothing {smoothing_rings} rings, "
			f"coherence {'n/a' if coherence is None else f'{coherence:.2f}'}")

	if np.unique(run_labels).shape[0] == 1:
		return np.array(['0'] * n_obs, dtype=object)

	# 3. Propagate: each spot inherits its unit's label (identity when no coarsening happened).
	labels = run_labels if bin_ids is None else run_labels[bin_ids]
	return labels.astype(str).astype(object)
