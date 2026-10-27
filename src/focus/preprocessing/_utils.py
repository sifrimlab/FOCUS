import os
import gc
import logging

import numpy as np
import scipy.sparse as sp

from focus.constants import FocusOutputDirectories
from focus.reporting import StepReporter, get_reporter  # noqa: F401  (re-exported for pipeline modules)

# Spot clustering runs on at most this many units. Larger samples (and ST samples whose spots hold
# too little signal) are clustered on coarser units built by plan_spatial_units: k x k blocks of the
# acquisition lattice when one is detected, Voronoi supercells otherwise.
_SPATIAL_CAP = 250_000
# The signal criterion never coarsens a sample below this many units.
_MIN_UNITS = 1_000
# Lloyd iterations for Voronoi supercells: on uniform points, tile-occupancy CV falls from 0.55
# (no iteration) to 0.35 at 5, close to the Poisson floor; more iterations change little.
_LLOYD_ITERATIONS = 5


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


def _fit_lattice(grid: np.ndarray, coords: np.ndarray, max_rows: int = 10_000) -> np.ndarray | None:
	"""Fit ``coords ~ grid @ axes + origin`` on an even subsample of at most ``max_rows`` rows.

	Returns the (2, 2) step vectors (row ``i`` = displacement for one step of ``grid[:, i]``) when
	the fit has full rank and every residual is within 2% of the shorter step, else ``None``.
	Shared by :func:`lattice_indices` and the alignment display basis
	(``focus.alignment.spot_payload.acquisition_basis``).
	"""
	n = len(grid)
	if n < 4:
		return None
	rows = np.linspace(0, n - 1, min(max_rows, n), dtype=int)
	g = np.asarray(grid, dtype=np.float64)[rows]
	c = np.asarray(coords, dtype=np.float64)[rows]
	if not (np.isfinite(g).all() and np.isfinite(c).all()):
		return None
	design = np.column_stack([g, np.ones(len(g))])
	fit, _, rank, _ = np.linalg.lstsq(design, c, rcond=None)
	axes = fit[:2]
	lengths = np.linalg.norm(axes, axis=1)
	if rank != 3 or np.any(lengths <= 0):
		return None
	if np.max(np.linalg.norm(design @ fit - c, axis=1)) > 0.02 * lengths.min():
		return None
	return axes


def _unique_rows(idx: np.ndarray) -> bool:
	span = idx.max(axis=0) + 1
	return np.unique(idx[:, 0] * span[1] + idx[:, 1]).size == len(idx)


def lattice_indices(coords: np.ndarray, obs=None, spot_size=None) -> tuple[np.ndarray, np.ndarray, str] | None:
	"""Integer acquisition-lattice indices of every spot, or ``None`` when no regular lattice is found.

	Tried in order:

	- ``obs['array_row']`` / ``obs['array_col']`` (Visium-style), accepted when the values are
	  integral, the pairs are unique and a linear fit to ``coords`` holds (:func:`_fit_lattice`). A
	  hexagonal lattice (``row + col`` has one parity for every spot, as in standard Visium) has its
	  column index halved, so every row contributes the same number of spots to a block.
	- an axis-aligned raster of pitch ``spot_size`` (MSI): ``round((coords - min) / spot_size)``,
	  accepted when at least 99% of spots lie within 0.1 pitch of a raster node and nodes are unique.

	Returns
	-------
	tuple[np.ndarray, np.ndarray, str] | None
		``(idx, step, source)``: (n, 2) int64 indices starting at 0, the (2,) physical length of one
		index step along each column of ``idx`` (coordinate units), and ``'array_row/array_col'`` or
		``'raster'``.
	"""
	coords = np.asarray(coords, dtype=np.float64)
	if obs is not None and {'array_row', 'array_col'}.issubset(obs.columns):
		grid = obs[['array_row', 'array_col']].to_numpy(dtype=np.float64)
		if np.isfinite(grid).all() and np.array_equal(grid, np.round(grid)):
			idx = grid.astype(np.int64)
			idx -= idx.min(axis=0)
			axes = _fit_lattice(idx[:, ::-1], coords)
			if axes is not None and _unique_rows(idx):
				step = np.linalg.norm(axes, axis=1)[::-1].copy()
				parity = (idx[:, 0] + idx[:, 1]) % 2
				if parity.min() == parity.max():
					idx[:, 1] //= 2
					step[1] *= 2
				return idx, step, 'array_row/array_col'
	if spot_size is not None and np.isfinite(coords).all():
		pitch = np.broadcast_to(np.asarray(spot_size, dtype=np.float64).ravel(), (2,)).copy()
		if np.all(pitch > 0):
			rel = (coords - coords.min(axis=0)) / pitch
			nodes = np.rint(rel)
			idx = nodes.astype(np.int64)
			if np.all(np.abs(rel - nodes) <= 0.1, axis=1).mean() >= 0.99 and _unique_rows(idx):
				return idx, pitch, 'raster'
	return None


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


def plan_spatial_units(
	coords: np.ndarray,
	*,
	cap: int = _SPATIAL_CAP,
	obs=None,
	spot_size=None,
	counts: np.ndarray | None = None,
	min_median_counts: float | None = None,
	seed: int = 0,
) -> tuple[np.ndarray | None, int, dict]:
	"""Choose the units spot clustering runs on, bounding its cost without a periodic imprint.

	Spots are clustered as they are unless the sample has more than ``cap`` spots or (when
	``counts`` and ``min_median_counts`` are given, ST) the median spot holds fewer than
	``min_median_counts``. Otherwise spots are grouped into units; the caller sums each unit's
	expression (:func:`_bin_sum_matrix`) and every spot inherits its unit's label.

	- **lattice** (:func:`lattice_indices` finds one): the unit of a spot is ``(row // k, col // k)``,
	  a k x k block of lattice sites anchored at the lattice origin. Every interior block holds
	  exactly k^2 sites, so unit occupancy cannot vary periodically, and the blocks depend only on
	  the lattice indices, not on how the coordinates are rotated or translated. ``k`` is the
	  smallest integer with at most ``cap`` occupied blocks whose median block count reaches
	  ``min_median_counts``; the signal criterion stops before fewer than ``_MIN_UNITS`` blocks remain.
	- **voronoi** (no lattice: segmented cells, beads, irregular layouts): ``m`` tiles from
	  :func:`_voronoi_units`, ``m = min(cap, n / s)`` where ``s`` spots per tile is the estimate that
	  reaches the median-count target (never fewer than ``_MIN_UNITS`` tiles).

	Fully deterministic for a given ``seed``. Units are an in-memory intermediate and are never
	persisted; only the returned provenance is.

	Returns
	-------
	bin_ids : np.ndarray | None
		(n,) intp unit of each spot, or ``None`` when spots are clustered as they are.
	n_units : int
		Number of units (``n`` when ``bin_ids`` is ``None``).
	info : dict
		Provenance: ``method`` (``'native'``, ``'lattice'`` or ``'voronoi'``), ``n_units``,
		``driver`` (``'cap'``, ``'signal'`` or ``'cap+signal'``), ``signal_limited`` and, for a
		lattice, ``k``, ``unit_size`` (coordinate units) and ``lattice_source``.
	"""
	coords = np.asarray(coords, dtype=np.float64)
	n = len(coords)
	signal = counts is not None and min_median_counts is not None and n > _MIN_UNITS
	counts = None if counts is None else np.asarray(counts, dtype=np.float64)
	need_cap = n > cap
	need_signal = signal and float(np.median(counts)) < min_median_counts
	if not (need_cap or need_signal):
		return None, n, {'method': 'native', 'n_units': n}

	limited = False
	lattice = lattice_indices(coords, obs, spot_size)
	if lattice is not None:
		idx, step, source = lattice
		k, driver = 1, []
		if need_cap:
			k = max(1, int(np.sqrt(n / cap)))
			while len(_unit_sizes_and_sums(_lattice_units(idx, k))[0]) > cap:
				k += 1
			driver.append('cap')
		if signal:
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
			return None, n, {'method': 'native', 'n_units': n, 'signal_limited': limited}
		uniq, bin_ids = np.unique(_lattice_units(idx, k), return_inverse=True)
		n_units = int(uniq.size)
		info = {'method': 'lattice', 'lattice_source': source, 'k': k,
			'unit_size': (k * step).tolist(), 'n_units': n_units}
	else:
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
			return None, n, {'method': 'native', 'n_units': n, 'signal_limited': limited}
		bin_ids, n_units = _voronoi_units(coords, m, seed)
		info = {'method': 'voronoi', 'n_units': n_units, 'lloyd_iterations': _LLOYD_ITERATIONS, 'seed': seed}
	info.update(driver='+'.join(driver), signal_limited=limited)
	return bin_ids.astype(np.intp), n_units, info


def describe_units(n_spots: int, info: dict) -> str:
	"""One reporter line for a clustering unit plan."""
	if info['method'] == 'native':
		return f"Clustering: {n_spots} spots at native resolution"
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
	spot_size=None,
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
	   the spots into at most ``cap`` units: k x k blocks of the acquisition raster (recovered
	   from ``coordinates`` and ``spot_size``) or Voronoi supercells when no raster is found.
	   Every spot of a unit is SUMMED into one pseudo-spot (:func:`_bin_sum_matrix`). Smaller
	   samples skip this and run on every spot.
	2. Normalize the run matrix (see ``normalize_target_sum``) and run the standard
	   ``pca -> neighbors -> leiden`` on it, bounded by ``cap`` rows.
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
	spot_size : array-like | None
		Raster pitch, used to recover the acquisition raster from ``coordinates``.
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
	bin_ids = None
	if coordinates is not None:
		bin_ids, n_bins, units = plan_spatial_units(coordinates, cap=cap, spot_size=spot_size, seed=random_state)
		get_reporter().detail(describe_units(n_obs, units))
	if bin_ids is not None:
		X_run = _bin_sum_matrix(X, bin_ids, n_bins)   # (n_units, n_vars); sparse-preserving, throwaway
		binned = True
	else:
		X_run = X.copy()                              # copy: normalize_total/log1p mutate in place
		binned = False

	n_run = X_run.shape[0]
	n_pcs = min(n_pcs_cap, n_run - 1, n_vars - 1)
	if n_run < 2 or n_pcs < 2:
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
	sc.pp.pca(sub, n_comps=n_pcs)                     # random_state=0 by default
	sc.pp.neighbors(sub, n_neighbors=min(15, sub.n_obs - 1))
	sc.tl.leiden(sub, resolution=leiden_resolution, key_added='leiden',
				 flavor='igraph', n_iterations=2, directed=False)
	run_labels = sub.obs['leiden'].to_numpy()
	del sub
	gc.collect()

	if np.unique(run_labels).shape[0] == 1:
		return np.array(['0'] * n_obs, dtype=object)

	# 3. Propagate: each spot inherits its unit's label (identity when no coarsening happened).
	labels = run_labels if bin_ids is None else run_labels[bin_ids]
	return labels.astype(str).astype(object)
