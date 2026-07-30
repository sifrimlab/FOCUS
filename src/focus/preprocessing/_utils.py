import os
import re
import gc
import logging

import numpy as np
import scipy.sparse as sp
import tqdm as _tqdm_lib

from focus.constants import FocusOutputDirectories

# Samples above this spot count are coarsened onto a spatially-uniform grid of at most this
# many bins: all spots in a bin are SUMMED into one pseudo-spot, Leiden runs on the bins, and
# each bin's label propagates back to its spots. Below it, the full sample is used directly.
# The SAME cap is reused by the alignment GUI so both stages build the identical grid (and thus
# agree on which spots share a bin / a label). Import it there rather than redefining it.
_SPATIAL_CAP = 100_000


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


def _parse_step_label(desc: str) -> tuple[int, int]:
	"""Parse '3/9 - ...' → (3, 9). Returns (0, 0) if the pattern is not found."""
	m = re.match(r'^(\d+)/(\d+)', desc)
	if m:
		return int(m.group(1)), int(m.group(2))
	return 0, 0


class StepReporter:
	"""Single, unified reporting interface for the whole pipeline.

	Every textual line is fanned out to all *available* sinks through one call:
	  - console + the focus.log file, via the shared ``focus`` logger;
	  - the web GUI, via the optional ``callback`` (absent in headless runs).

	Sinks degrade gracefully and independently: the file handler only exists once
	a dataset path is known (``setup_logging``), the GUI callback only when a GUI is
	attached, and if no logging handlers are configured at all we fall back to
	``print`` so the console is never silent. Prefer ``reporter.message(...)`` over
	bare ``print()`` / ``logging`` so output reaches every interface at once.
	"""

	def __init__(self, callback=None):
		self._callback = callback
		self._logger = logging.getLogger("focus")

	def _emit(self, msg: str, level: int = logging.INFO) -> None:
		"""Fan a line out to the console + log file (via the 'focus' logger).

		Falls back to ``print`` when the logger has no handlers (e.g. a bare
		StepReporter() in a script or test that never called ``setup_logging``),
		so the console is never silent regardless of how FOCUS is launched.
		"""
		self._logger.log(level, msg)
		if not self._logger.hasHandlers():
			print(msg)

	def step(self, desc: str, current: int = 0, total: int = 0) -> None:
		"""Announce a named step on every interface (console, log file, GUI)."""
		self._emit(desc)
		self._send(desc, current, total)

	def _send(self, desc: str, current: int, total: int) -> None:
		if self._callback:
			idx, n = _parse_step_label(desc)
			self._callback({
				"sub_step": desc,
				"sub_step_index": idx,
				"sub_step_total": n,
				"sub_step_progress": current,
				"sub_step_items_total": total,
			})

	def _update(self, desc: str, current: int, total: int) -> None:
		"""Update progress count without printing (called during tqdm iteration)."""
		self._send(desc, current, total)

	def update(self, desc: str, current: int, total: int) -> None:
		"""Update item-level progress without printing to stdout (e.g. mid-loop updates)."""
		self._send(desc, current, total)

	def tqdm(self, iterable, desc: str, total: int | None = None, **kwargs):
		"""tqdm replacement that also reports progress to the GUI."""
		if total is None and hasattr(iterable, '__len__'):
			total = len(iterable)
		n = total or 0
		self._send(desc, 0, n)  # Report step start; tqdm handles CLI display
		for i, item in enumerate(_tqdm_lib.tqdm(iterable, desc=desc, total=total, **kwargs)):
			yield item
			self._update(desc, i + 1, n)

	def message(self, msg: str, level: int = logging.INFO) -> None:
		"""Report a status line to every available interface at once.

		Writes to the console and the focus.log file (via the 'focus' logger) and,
		when a GUI is attached, to the web GUI message log (callback). This is the
		single unified reporting call — use it instead of print()/logging so a line
		reaches all sinks, degrading gracefully when one (e.g. the GUI in a headless
		run, or the log file before setup_logging) is unavailable. Pass ``level`` to
		emit at a different logging level (e.g. logging.WARNING)."""
		self._emit(msg, level)
		if self._callback:
			self._callback({"message": msg})

	def set_sample(self, sample_id: str, index: int, total: int) -> None:
		"""Set the current sample context and reset sub-step fields. Reports on every interface."""
		self._emit(f"[{index}/{total}] Processing sample: {sample_id}")
		if self._callback:
			self._callback({
				"current_sample": sample_id,
				"current_sample_index": index,
				"total_samples": total,
				"sub_step": None,
				"sub_step_index": 0,
				"sub_step_total": 0,
				"sub_step_progress": 0,
				"sub_step_items_total": 0,
			})


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


def _native_axis_cells(values: np.ndarray, ext_axis: float) -> int:
	"""Largest number of uniform cells along one axis whose width is still >= the data's native
	sample spacing, so no interior cell can fall *between* samples and render as an empty gap.

	For a regular raster of ``k`` equally-spaced lines spanning the extent this is ``k - 1`` (cell
	width == line spacing). Coordinates are quantized to ~1e-6 of the extent first so float jitter
	from coordinate transforms does not inflate the spacing estimate. Returns ``1`` for a
	degenerate (single-line / zero-extent) axis.
	"""
	if ext_axis <= 0:
		return 1
	levels = np.unique(np.round((values - values.min()) / ext_axis * 1e6))
	if levels.size < 2:
		return 1
	step = float(np.median(np.diff(levels)))   # native spacing in the 0..1e6 quantized space
	if step <= 0:
		return 1
	return max(1, int(np.floor(1e6 / step)))


def _spatial_bin_assignment(coords: np.ndarray, n_target: int) -> tuple[np.ndarray, int, np.ndarray, np.ndarray]:
	"""Assign each point to one of ``<= n_target`` occupied cells of a uniform spatial grid.

	The grid spans the bounding box of ``coords`` with cell counts along each axis chosen
	proportional to the box aspect ratio and sized so ``nx * ny <= n_target`` (the cap is a
	hard ceiling). Each point is mapped to the cell that *contains* it — which, for a regular
	grid, is exactly the cell whose center is nearest, so this realises the "closest bin" rule
	in O(n) without any nearest-neighbour search. Empty cells are dropped and the occupied ones
	are renumbered ``0 .. n_bins-1``.

	Fully deterministic: the result is a pure function of ``coords`` and ``n_target`` (no RNG),
	so the identical grid is reproduced wherever this is recomputed (preprocessing clustering and
	the alignment GUI both call it with the same cap).

	Returns
	-------
	bin_ids : np.ndarray
		(n_obs,) intp; the compacted occupied-bin index in ``[0, n_bins)`` for each point.
	n_bins : int
		Number of occupied bins (``<= n_target``).
	pitch : np.ndarray
		(2,) float64 cell size ``(ext_x / nx, ext_y / ny)`` — the "computed" spot size used to
		render bins gap-free. ``0`` on an axis with zero extent (caller supplies a fallback).
	centers : np.ndarray
		(n_bins, 2) float64 center of each occupied grid cell (``mins + (ix + 0.5) * pitch``).
		Bins drawn as ``pitch``-sized squares at these centers tile gap-free; the data centroid
		would not — it is pulled toward where the points sit in the cell, beating into a regular
		grid of gaps.
	"""
	coords = np.asarray(coords, dtype=np.float64)
	mins = coords.min(axis=0)
	raw_ext = coords.max(axis=0) - mins
	ext = np.maximum(raw_ext, 1e-9)
	# Size the grid so nx * ny <= n_target (floor, not ceil, to guarantee the cap is never exceeded).
	# Clamp nx to [1, n_target]: an extreme aspect ratio (e.g. a near-1D / zero-extent axis) would
	# otherwise drive nx past n_target while ny floors to 1, breaking the cap.
	nx = min(max(1, int(np.floor(np.sqrt(n_target * ext[0] / ext[1])))), n_target)
	ny = max(1, int(n_target // nx))
	# Never lay the grid finer than the data's native sampling: a uniform cell narrower than the
	# spot spacing can fall *between* samples and stay empty, so the occupied cells render as a
	# regular grid of gaps. Cap each axis at the number of cells whose width is >= that spacing
	# (for a regular raster of k lines, k-1). Only ever coarsens (still <= n_target), keeping the
	# tiling gap-free.
	nx = max(1, min(nx, _native_axis_cells(coords[:, 0], raw_ext[0])))
	ny = max(1, min(ny, _native_axis_cells(coords[:, 1], raw_ext[1])))
	pitch = raw_ext / np.array([nx, ny], dtype=np.float64)
	ix = np.clip(((coords[:, 0] - mins[0]) / ext[0] * nx).astype(int), 0, nx - 1)
	iy = np.clip(((coords[:, 1] - mins[1]) / ext[1] * ny).astype(int), 0, ny - 1)
	flat = ix * ny + iy

	# Compact to occupied cells; return_inverse already yields a 0..n_bins-1 labelling.
	uniq_flat, bin_ids = np.unique(flat, return_inverse=True)
	bin_ids = bin_ids.astype(np.intp)
	n_bins = int(uniq_flat.size)

	# Place each occupied bin at its grid-CELL CENTER (not the data centroid): decode the flat
	# index with the same encoding flat = ix*ny + iy, then center = mins + (idx + 0.5) * pitch.
	# Equal-pitch squares drawn at these centers tile edge-to-edge (adjacent cells differ by one
	# index, so their centers differ by exactly one pitch); the centroid, pulled toward wherever
	# points sit in the cell, does not tile and beats into a regular grid of gaps. On a zero-extent
	# axis pitch == 0 so center == mins (the shared coordinate); the caller substitutes the real
	# spot_size for the rendered square size there.
	cell_ix = uniq_flat // ny
	cell_iy = uniq_flat % ny
	centers = np.empty((n_bins, 2), dtype=np.float64)
	centers[:, 0] = mins[0] + (cell_ix + 0.5) * pitch[0]
	centers[:, 1] = mins[1] + (cell_iy + 0.5) * pitch[1]
	return bin_ids, n_bins, pitch, centers


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
	cap: int = _SPATIAL_CAP,
	n_pcs_cap: int = 50,
	random_state: int = 0,
) -> np.ndarray:
	"""Compute per-spot cluster labels for alignment-stage spot colouring, scalably.

	The labels are consumed only by the alignment GUI for categorical colouring (no
	downstream algorithm uses them numerically), so the goal is a fast, "Leiden-like"
	partition rather than an exact Leiden over every spot — which does not scale to
	millions of points. The bottleneck for ultra-high-resolution / sub-cellular modalities
	is the opposite of scale: each individual spot carries too little signal for PCA /
	Leiden to find structure. So instead of subsampling, large samples are *coarsened*:

	1. When ``n_obs > cap`` (and ``coordinates`` are given), lay a uniform spatial grid of
	   at most ``cap`` bins (:func:`_spatial_bin_assignment`) and SUM every spot that falls
	   in a bin into one pseudo-spot (:func:`_bin_sum_matrix`). This aggregates weak signal
	   so each pseudo-spot is coarser but informative, and caps the row count at ``cap``.
	   Smaller samples skip binning and run on every spot.
	2. Normalize the run matrix (see ``normalize_target_sum``) and run the standard
	   ``pca -> neighbors -> leiden`` on it — bounded by ``cap`` rows, so fast.
	3. Propagate each bin's Leiden label back to every spot that contributed to the bin
	   (identity when no binning happened). No MiniBatchKMeans / no per-spot projection.

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
		(n_obs, 2) spatial coordinates enabling spatial binning. When ``None``, no binning is
		done and Leiden runs on every spot regardless of ``cap``.
	cap : int
		Maximum number of rows Leiden runs on (= maximum number of spatial bins). Samples at
		or below this are clustered directly, without binning.
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

	# 1. Build the matrix Leiden runs on: coarse summed bins for large samples, else all spots.
	if n_obs > cap and coordinates is not None:
		bin_ids, n_bins, _, _ = _spatial_bin_assignment(coordinates, cap)
		X_run = _bin_sum_matrix(X, bin_ids, n_bins)   # (n_bins, n_vars); sparse-preserving, throwaway
		binned = True
	else:
		bin_ids, n_bins = None, n_obs
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

	# 3. Propagate: each spot inherits its bin's label (identity when no binning happened).
	labels = run_labels if bin_ids is None else run_labels[bin_ids]
	return labels.astype(str).astype(object)
