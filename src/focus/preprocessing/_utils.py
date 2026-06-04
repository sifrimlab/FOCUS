import os
import re
import gc
import logging

import numpy as np
import scipy.sparse as sp
import tqdm as _tqdm_lib

from focus.constants import FocusOutputDirectories

# Spots above this count are clustered on a spatially-uniform subset (Leiden runs only
# on the subset; MiniBatchKMeans then labels every spot). Below it, the full sample is used.
_CLUSTERING_SUBSET_CAP = 100_000


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


def _spatial_uniform_subsample(coords: np.ndarray, n_target: int, rng: np.random.Generator) -> np.ndarray:
	"""Pick ~``n_target`` point indices spread uniformly across the 2D spatial domain.

	Points are binned into a grid sized so the number of cells is ~``n_target`` (cell
	counts along each axis proportional to the bounding-box aspect ratio). Indices are
	then collected by round-robin — one point per occupied cell per pass, in cell order,
	with a seeded shuffle deciding which point a cell yields — until ``n_target`` are
	gathered. Because every occupied cell contributes equally regardless of its density,
	sparse / localized regions are upweighted relative to a uniform random draw, which
	protects rare or spatially-restricted spot types from being dropped.

	Returns a sorted int array of the chosen indices (length <= ``n_target``).
	"""
	coords = np.asarray(coords, dtype=np.float64)
	mins = coords.min(axis=0)
	ext = np.maximum(coords.max(axis=0) - mins, 1e-9)
	nx = max(1, int(round(np.sqrt(n_target * ext[0] / ext[1]))))
	ny = max(1, int(np.ceil(n_target / nx)))
	ix = np.clip(((coords[:, 0] - mins[0]) / ext[0] * nx).astype(int), 0, nx - 1)
	iy = np.clip(((coords[:, 1] - mins[1]) / ext[1] * ny).astype(int), 0, ny - 1)
	cell = ix * ny + iy

	# Seeded shuffle so the within-cell pick order (and thus the result) is deterministic.
	buckets: dict[int, list[int]] = {}
	for idx in rng.permutation(coords.shape[0]):
		buckets.setdefault(int(cell[idx]), []).append(int(idx))

	chosen: list[int] = []
	keys = sorted(buckets)
	while len(chosen) < n_target and keys:
		keys = [k for k in keys if buckets[k]]
		for k in keys:
			chosen.append(buckets[k].pop())
			if len(chosen) >= n_target:
				break
	return np.sort(np.array(chosen[:n_target], dtype=np.intp))


def compute_cluster_labels(
	X,
	*,
	leiden_resolution: float,
	normalize_target_sum: float | None,
	coordinates: np.ndarray | None = None,
	subset_cap: int = _CLUSTERING_SUBSET_CAP,
	n_pcs_cap: int = 50,
	random_state: int = 0,
) -> np.ndarray:
	"""Compute per-spot cluster labels for alignment-stage spot colouring, scalably.

	The labels are consumed only by the alignment GUI for categorical colouring (no
	downstream algorithm uses them numerically), so the goal is a fast, "Leiden-like"
	partition rather than an exact Leiden over every spot — which does not scale to
	millions of points. A single uniform path is used for any sample size:

	1. Take a subset of ``min(subset_cap, n_obs)`` spots (the whole sample when it is
	   already small enough). Subsampling is spatially uniform via
	   :func:`_spatial_uniform_subsample` when ``coordinates`` are given, else a seeded
	   random draw.
	2. Run the standard ``pca -> neighbors -> leiden`` on the subset (bounded, so fast),
	   capturing the PCA basis, the subset embedding, and the adaptive cluster count ``K``.
	3. Project every spot into that PCA space (chunked; the subset embedding is reused
	   when the subset is the full sample), applying the same normalization the basis was
	   fit under.
	4. Label all spots with ``MiniBatchKMeans(n_clusters=K)`` seeded from the Leiden
	   cluster centroids, which keeps the partition close to what Leiden would produce
	   while scaling linearly.

	``X`` is never mutated (clustering runs on throwaway copies); intermediates (PCA,
	neighbour graph) are not persisted. Fully deterministic for a fixed ``random_state``.

	Parameters
	----------
	X : scipy.sparse matrix | np.ndarray
		Expression / intensity matrix, shape (n_obs, n_vars).
	leiden_resolution : float
		Resolution for the Leiden run on the subset.
	normalize_target_sum : float | None
		When a float, ``X`` holds raw counts and is total-count normalized to this target
		and log1p-transformed on a throwaway copy before PCA (spatial transcriptomics).
		When ``None``, ``X`` is assumed already normalized (MSI) and used as-is.
	coordinates : np.ndarray | None
		(n_obs, 2) spatial coordinates enabling spatially-uniform subsampling. Falls back
		to random subsampling when ``None``.
	subset_cap : int
		Maximum number of spots Leiden runs on. Samples at or below this use all spots.
	n_pcs_cap : int
		Upper bound on the number of principal components.
	random_state : int
		Seed for the subsample, PCA, and MiniBatchKMeans.

	Returns
	-------
	np.ndarray
		Length-n_obs array of string cluster labels (dtype object). All ``'0'`` when the
		sample is too small to cluster or resolves to a single cluster.
	"""
	import anndata as ad
	import scanpy as sc
	from sklearn.cluster import MiniBatchKMeans

	n_obs, n_vars = X.shape
	n_pcs = min(n_pcs_cap, n_obs - 1, n_vars - 1)
	if n_obs < 2 or n_pcs < 2:
		return np.array(['0'] * n_obs, dtype=object)

	# 1. Choose the subset (full sample when at/below the cap), spatially uniform if possible.
	rng = np.random.default_rng(random_state)
	subset_size = min(subset_cap, n_obs)
	if subset_size == n_obs:
		sub_idx = np.arange(n_obs, dtype=np.intp)
	elif coordinates is not None:
		sub_idx = _spatial_uniform_subsample(coordinates, subset_size, rng)
	else:
		sub_idx = np.sort(rng.choice(n_obs, size=subset_size, replace=False))

	# 2. Leiden on the subset; capture the PCA basis, the embedding, and the adaptive K.
	sub = ad.AnnData(X=X[sub_idx].copy())
	if normalize_target_sum is not None:
		sc.pp.normalize_total(sub, target_sum=normalize_target_sum, inplace=True)
		sc.pp.log1p(sub)
	sc.pp.pca(sub, n_comps=n_pcs)                                # random_state=0 by default
	components = np.asarray(sub.varm['PCs'], dtype=np.float32)   # (n_vars, n_pcs)
	mean_ = np.asarray(sub.X.mean(axis=0), dtype=np.float32).ravel()  # centering used by sc.pp.pca
	emb_sub = np.asarray(sub.obsm['X_pca'], dtype=np.float32)    # (subset_size, n_pcs)
	sc.pp.neighbors(sub, n_neighbors=min(15, sub.n_obs - 1))
	sc.tl.leiden(sub, resolution=leiden_resolution, key_added='leiden',
				 flavor='igraph', n_iterations=2, directed=False)
	sub_labels = sub.obs['leiden'].to_numpy()
	del sub
	gc.collect()

	uniq = np.unique(sub_labels)
	K = uniq.shape[0]
	if K == 1:
		return np.array(['0'] * n_obs, dtype=object)
	leiden_centroids = np.vstack([emb_sub[sub_labels == lab].mean(axis=0) for lab in uniq]).astype(np.float32)

	# 3. Project every spot into the same PCA basis (chunked). Reuse the subset embedding
	# when the subset already covers the whole sample.
	if subset_size == n_obs:
		emb_all = emb_sub
	else:
		emb_all = np.empty((n_obs, n_pcs), dtype=np.float32)
		chunk = 100_000
		for start in range(0, n_obs, chunk):
			stop = min(start + chunk, n_obs)
			X_chunk = X[start:stop]
			if normalize_target_sum is not None:
				tmp = ad.AnnData(X=X_chunk.copy())
				sc.pp.normalize_total(tmp, target_sum=normalize_target_sum, inplace=True)
				sc.pp.log1p(tmp)
				X_chunk = tmp.X
			X_chunk = X_chunk.toarray() if sp.issparse(X_chunk) else np.asarray(X_chunk)
			emb_all[start:stop] = (X_chunk.astype(np.float32) - mean_) @ components

	# 4. Label all spots with MiniBatchKMeans seeded from the Leiden cluster centroids.
	labels = MiniBatchKMeans(
		n_clusters=K, init=leiden_centroids, n_init=1, random_state=random_state,
	).fit_predict(emb_all)
	return labels.astype(str).astype(object)
