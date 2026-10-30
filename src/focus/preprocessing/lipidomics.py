import numpy as np
import scipy.sparse as sp
import mmap
import os, tqdm, psutil
from focus.preprocessing._utils import get_reporter, compute_cluster_labels, clustering_preset, find_imzml_pair
from collections import defaultdict
from sklearn.linear_model import LinearRegression
from sklearn.mixture import GaussianMixture
import anndata as ad
import pandas as pd
import xml.etree.ElementTree as ET
import concurrent.futures
from numba import njit
from joblib import Parallel, delayed
from functools import partial
import scanpy as sc
import gc
import warnings
from dataclasses import dataclass
from scipy.ndimage import binary_fill_holes, binary_opening, median_filter, label as label_components
from scipy.stats import norm as normal_distribution

import focus.utils as utils
from focus.constants import ImzMLFileParser, MsiIntensityNormalization, MsiSampleType, MsiMetadata, MsiIonMode, MsiPreprocessingParams, SpotResolution
from focus.constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality
from focus.provenance import cached_fingerprint, file_digest, fingerprint
import logging

logger = logging.getLogger("focus.preprocessing.lipidomics")

# MSI dataset preprocessing steps, in order; 1, 2, 4 and 7 run per sample.
_DATASET_STEPS = (
	"Loading MSI data",
	"Selecting tissue spots",
	"Selecting recalibration references",
	"Computing per-sample m/z backbones",
	"Building global m/z backbone",
	"Annotating features",
	"Interpolating intensities",
	"Merging samples",
)


def _step(index: int) -> tuple[int, int, str]:
	"""(index, total, name) of MSI dataset step ``index`` for StepReporter."""
	return index, len(_DATASET_STEPS), _DATASET_STEPS[index - 1]


# Part of the preprocessing fingerprint: bump whenever the same input and settings produce a
# different output, so cached results are rebuilt.
METHOD_VERSION = "msi-2"


def _parse_mz_by_mode(value, setting: str) -> dict[str, np.ndarray] | None:
	'''
	Read a per-ion-mode m/z list setting (`recalibration_reference`, `internal_standard_mz`).

	Accepts None or an empty string (not set), a mapping such as {"pos": [760.58, 798.54]} with list
	or array values (YAML config, Python API), or the GUI string "pos: 760.58, 798.54; neg: 885.55".
	Returns ascending float64 arrays keyed by ion mode, or None when no mode has a value.
	'''
	if value is None or (isinstance(value, str) and not value.strip()):
		return None
	if isinstance(value, str):
		mapping = {}
		for part in value.split(';'):
			if not part.strip():
				continue
			mode, separator, masses = part.partition(':')
			if not separator:
				raise ValueError(f"{setting}: expected 'mode: m/z, m/z; mode: m/z', got '{part.strip()}'.")
			mapping[mode] = masses.replace(',', ' ').split()
		value = mapping
	if not isinstance(value, dict):
		raise ValueError(f"{setting} must map ion modes {MsiIonMode.list()} to lists of m/z values.")

	parsed = {}
	for mode, masses in value.items():
		mode = str(mode).strip().lower()
		if mode not in MsiIonMode.list():
			raise ValueError(f"{setting}: invalid ion mode '{mode}'. Expected one of {MsiIonMode.list()}.")
		try:
			array = np.asarray(masses, dtype=np.float64).ravel()
		except (TypeError, ValueError):
			raise ValueError(f"{setting}: the m/z values for '{mode}' must be numbers.") from None
		if not np.all(np.isfinite(array)) or np.any(array <= 0):
			raise ValueError(f"{setting}: the m/z values for '{mode}' must be positive numbers.")
		if array.size:
			parsed[mode] = np.sort(array)
	return parsed or None


def normalize_intensities(raw: np.ndarray, method: str, pixel_coordinates: np.ndarray | None = None,
						  standard_columns: np.ndarray | None = None) -> np.ndarray:
	'''
	Normalize the binned intensities of one sample and ion mode (rows = spectra).

	Parameters
	----------
	raw : np.ndarray
		(n_spectra, n_features) binned intensities; not modified.
	method : str
		One of MsiIntensityNormalization.list().
	pixel_coordinates : np.ndarray | None
		(n_spectra, 2) raster coordinates; required by 'internal_standard' to fill empty divisors.
	standard_columns : np.ndarray | None
		Feature columns of the internal standards; required by 'internal_standard'.
	'''
	if method == MsiIntensityNormalization.NONE or raw.size == 0:
		return raw
	if method == MsiIntensityNormalization.TIC:
		tic = raw.sum(axis=1, keepdims=True)
		tic[tic == 0] = 1
		return raw / tic
	if method == MsiIntensityNormalization.LOG:
		return np.log1p(raw)
	if method == MsiIntensityNormalization.CLR:
		# Sparsity-preserving CLR: center the log-intensities over each row's nonzero (observed)
		# support only, leaving structural zeros at exactly 0 so the matrix stays sparse.
		nz = raw > 0
		log_x = np.log(raw, where=nz, out=np.zeros_like(raw))
		counts = nz.sum(axis=1, keepdims=True)
		counts[counts == 0] = 1
		row_mean = log_x.sum(axis=1, keepdims=True) / counts
		return np.where(nz, log_x - row_mean, 0.0)
	if method == MsiIntensityNormalization.TIC_MEAN_SCALED:
		# Rescale every spectrum so its total ion current equals the mean TIC over this sample's
		# spots (this ion mode): TIC normalization multiplied by a per-sample constant. The mean is
		# taken within this sample and ion mode only, so it does not harmonise samples.
		tic = raw.sum(axis=1, keepdims=True)
		mean_tic = tic.mean()
		if mean_tic == 0:
			mean_tic = 1.0
		scaling = tic / mean_tic
		scaling[scaling == 0] = 1.0  # empty spots stay all-zero
		return raw / scaling
	if method == MsiIntensityNormalization.MEDIAN:
		# Median over each spectrum's nonzero features: binned centroid spectra are sparse, so the
		# median over all features would be 0. Spectra without signal use divisor 1.
		median = np.ones((raw.shape[0], 1), dtype=np.float64)
		for start in range(0, raw.shape[0], 10_000):
			block = raw[start:start + 10_000]
			with warnings.catch_warnings():
				warnings.simplefilter("ignore", RuntimeWarning)  # all-zero spectra
				block_median = np.nanmedian(np.where(block > 0, block, np.nan), axis=1)
			median[start:start + block.shape[0], 0] = np.where(np.isfinite(block_median), block_median, 1.0)
		return raw / median
	if method == MsiIntensityNormalization.INTERNAL_STANDARD:
		if standard_columns is None or len(standard_columns) == 0 or pixel_coordinates is None:
			raise ValueError("internal_standard normalization needs the internal standard columns and pixel coordinates.")
		divisor = raw[:, standard_columns].sum(axis=1).astype(np.float64)
		missing = divisor <= 0
		if missing.all():
			raise ValueError("The internal standard is absent from every spectrum of this sample.")
		if missing.any():
			# Spectra without the standard take the 3 x 3 neighbourhood median, then the sample median.
			neighbourhood = neighbourhood_nanmedian(np.where(missing, np.nan, divisor), pixel_coordinates)
			divisor[missing] = neighbourhood[missing]
			still_missing = ~np.isfinite(divisor)
			divisor[still_missing] = np.median(divisor[~missing])
			get_reporter().detail(f"Internal standard absent from {int(missing.sum()):,} spectra: "
								  f"{int((missing & ~still_missing).sum()):,} filled from neighbours, "
								  f"{int(still_missing.sum()):,} from the sample median")
		return raw / divisor[:, None]
	raise ValueError(f"Invalid intensity normalization method '{method}'. Expected one of {MsiIntensityNormalization.list()}.")


@njit(cache=True)
def cluster_unique_mz_chunk(unique_mz, counts, mass_tolerance_ppm):
	"""
	Cluster m/z values within a chunk using sliding window clustering and weighted centroids.

	Parameters
	----------
	unique_mz : 1D np.ndarray (sorted)
		Sorted m/z values within the chunk.
	counts : 1D np.ndarray
		Counts associated with each m/z.
	mass_tolerance_ppm : int
		Mass tolerance in ppm.

	Returns
	-------
	consensus_mz : list of floats
		Clustered consensus m/z values.
	consensus_weights : list of floats
		Corresponding cluster weights.
	"""

	consensus_mz = []
	consensus_weights = []

	n = len(unique_mz)
	start_idx = 0

	while start_idx < n:
		centroid = unique_mz[start_idx]
		weight_sum = counts[start_idx]
		weighted_sum = unique_mz[start_idx] * counts[start_idx]
		end_idx = start_idx + 1

		while end_idx < n:
			candidate_mz = unique_mz[end_idx]
			ppm_diff = abs(candidate_mz - centroid) / centroid * 1e6
			if ppm_diff <= mass_tolerance_ppm:
				weight_sum += counts[end_idx]
				weighted_sum += candidate_mz * counts[end_idx]
				centroid = weighted_sum / weight_sum
				end_idx += 1
			else:
				break

		consensus_mz.append(centroid)
		consensus_weights.append(weight_sum)

		start_idx = end_idx

	return np.array(consensus_mz), np.array(consensus_weights)


@njit(cache=True)
def cluster_unique_mz_labels(unique_mz, counts, mass_tolerance_ppm):
	"""
	Group sorted unique m/z within a ppm tolerance and return the group index of every input value.

	Applies the SAME sliding-window weighted-centroid growth rule as `cluster_unique_mz_chunk` — a
	value joins the current group while it stays within `mass_tolerance_ppm` of the running weighted
	centroid — but emits per-value labels instead of the centroids. Callers need the labels to
	aggregate extra per-group statistics that the centroid form cannot express, in particular how
	many distinct samples contribute to a group. Keep the two growth rules in sync.

	Parameters
	----------
	unique_mz : 1D np.ndarray (sorted ascending)
		Distinct m/z values.
	counts : 1D np.ndarray (float64)
		Weight associated with each m/z.
	mass_tolerance_ppm : int
		Mass tolerance in ppm.

	Returns
	-------
	labels : 1D np.ndarray (int32)
		Group index per input value, ascending and gap-free (`labels[0] == 0`).
	"""

	n = len(unique_mz)
	labels = np.empty(n, dtype=np.int32)

	group = 0
	start_idx = 0

	while start_idx < n:
		centroid = unique_mz[start_idx]
		weight_sum = counts[start_idx]
		weighted_sum = unique_mz[start_idx] * counts[start_idx]
		labels[start_idx] = group
		end_idx = start_idx + 1

		while end_idx < n:
			candidate_mz = unique_mz[end_idx]
			ppm_diff = abs(candidate_mz - centroid) / centroid * 1e6
			if ppm_diff <= mass_tolerance_ppm:
				weight_sum += counts[end_idx]
				weighted_sum += candidate_mz * counts[end_idx]
				centroid = weighted_sum / weight_sum
				labels[end_idx] = group
				end_idx += 1
			else:
				break

		group += 1
		start_idx = end_idx

	return labels


@njit(cache=True)
def merge_chunks(prev_mz, prev_w, curr_mz, curr_w, mass_tolerance_ppm):
	"""
	Merge consensus clusters from two adjacent chunks resolving overlaps.

	Both prev_mz and curr_mz are sorted arrays.

	Returns merged consensus m/z and weights arrays.
	"""
	max_size = len(prev_mz) + len(curr_mz)
	merged_mz = np.empty(max_size, dtype=np.float64)
	merged_w = np.empty(max_size, dtype=np.float64)
	k = 0
	i, j = 0, 0

	while i < len(prev_mz) and j < len(curr_mz):
		ppm_diff = abs(curr_mz[j] - prev_mz[i]) / ((curr_mz[j] + prev_mz[i]) / 2) * 1e6
		if ppm_diff <= mass_tolerance_ppm:
			# Merge clusters by weighted average
			tot_w = prev_w[i] + curr_w[j]
			merged_mz[k] = (prev_mz[i] * prev_w[i] + curr_mz[j] * curr_w[j]) / tot_w
			merged_w[k] = tot_w
			k += 1
			i += 1
			j += 1
		elif prev_mz[i] < curr_mz[j]:
			merged_mz[k] = prev_mz[i]
			merged_w[k] = prev_w[i]
			k += 1
			i += 1
		else:
			merged_mz[k] = curr_mz[j]
			merged_w[k] = curr_w[j]
			k += 1
			j += 1

	# Append remaining
	while i < len(prev_mz):
		merged_mz[k] = prev_mz[i]
		merged_w[k] = prev_w[i]
		k += 1
		i += 1
	while j < len(curr_mz):
		merged_mz[k] = curr_mz[j]
		merged_w[k] = curr_w[j]
		k += 1
		j += 1

	return merged_mz[:k], merged_w[:k]


@njit(cache=True)
def interpolate_single(original_mz, original_intensity, reference_mz, mass_tolerance):
	"""
	Bin one spectrum onto the reference m/z grid.

	Each peak adds its whole intensity to the single nearest reference feature, provided that
	feature lies within `mass_tolerance` ppm of the peak; peaks with no feature in reach are
	dropped. On an exact distance tie the lower feature wins. Because a peak is never split
	between features, a small residual m/z error does not move intensity between neighbouring
	features.
	"""
	n_ref = len(reference_mz)
	result = np.zeros(n_ref, dtype=original_intensity.dtype)

	if len(original_mz) == 0 or n_ref == 0:
		return result

	for i in range(len(original_mz)):
		mz_peak = original_mz[i]
		right = np.searchsorted(reference_mz, mz_peak)
		best = -1
		best_ppm = float(mass_tolerance)
		if right > 0:
			ppm = abs(mz_peak - reference_mz[right - 1]) / mz_peak * 1e6
			if ppm <= best_ppm:
				best = right - 1
				best_ppm = ppm
		if right < n_ref:
			ppm = abs(reference_mz[right] - mz_peak) / mz_peak * 1e6
			if ppm < best_ppm or (best < 0 and ppm <= best_ppm):
				best = right
		if best >= 0:
			result[best] += original_intensity[i]

	return result


# --- m/z recalibration (docs/scientific/msi_methods.md §4) ---

# Line-level calibrant search half-width: this multiple of the mass tolerance, but at least
# _RECAL_MIN_SEARCH_PPM. It must exceed the largest expected drift.
_RECAL_SEARCH_FACTOR = 5
_RECAL_MIN_SEARCH_PPM = 50
# Matched (spectrum, reference) pairs a scan line needs before its own estimate is used.
_RECAL_MIN_LINE_MATCHES = 3
# The linear m/z term is fitted only when at least this many references, spanning at least this
# fraction of the reference m/z span, are matched on a line.
_RECAL_SLOPE_MIN_REFERENCES = 3
_RECAL_SLOPE_MIN_SPAN = 0.25
# Outlier cut-off of the slope fit, in robust standard deviations (1.4826 x MAD).
_RECAL_OUTLIER_SIGMA = 3.0
_MAD_TO_SIGMA = 1.4826
# Running-median window, in scan lines, applied to the line model.
_RECAL_LINE_SMOOTHING = 5
# Matched references a pixel needs before its own residual is used.
_RECAL_MIN_PIXEL_MATCHES = 2
# Spectra flattened per numba call; bounds the transient arrays.
_RECAL_BLOCK_SPECTRA = 20_000
# Minimum number of automatically selected recalibration references per ion mode.
_RECALIBRATION_REFERENCES = 10


def recalibration_search_ppm(mass_tolerance: float) -> float:
	"""Half-width, in ppm, of the line-level calibrant search window."""
	return float(max(_RECAL_SEARCH_FACTOR * mass_tolerance, _RECAL_MIN_SEARCH_PPM))


def _flatten_spectra(mz_list: list[np.ndarray], intensity_list: list[np.ndarray] | None = None):
	"""Concatenate spectra into float64 arrays plus CSR-style offsets (len = n_spectra + 1)."""
	lengths = np.fromiter((m.size for m in mz_list), dtype=np.int64, count=len(mz_list))
	offsets = np.zeros(lengths.size + 1, dtype=np.int64)
	np.cumsum(lengths, out=offsets[1:])
	if offsets[-1] == 0:
		empty = np.empty(0, dtype=np.float64)
		return empty, empty, offsets
	flat_mz = np.concatenate(mz_list).astype(np.float64, copy=False)
	flat_intensity = (np.concatenate(intensity_list).astype(np.float64, copy=False)
					  if intensity_list is not None else np.empty(0, dtype=np.float64))
	return flat_mz, flat_intensity, offsets


@njit(cache=True)
def match_references(flat_mz, flat_intensity, offsets, reference_mz, centre_ppm, window_ppm, min_intensity):
	"""
	Match every reference m/z in every spectrum.

	For spectrum s and reference j the search window is centred at
	`reference_mz[j] * (1 + centre_ppm[s, j] * 1e-6)` with half-width `window_ppm` ppm of the
	reference. The most intense peak in the window with intensity >= `min_intensity` is the match.

	Returns
	-------
	errors : (n_spectra, n_ref) float32
		ppm error `(m_match - reference) / reference * 1e6`; NaN where nothing matched.
	"""
	n_spectra = offsets.size - 1
	n_ref = reference_mz.size
	errors = np.full((n_spectra, n_ref), np.nan, dtype=np.float32)

	for s in range(n_spectra):
		start = offsets[s]
		end = offsets[s + 1]
		if end <= start:
			continue
		mz = flat_mz[start:end]
		intensity = flat_intensity[start:end]

		# imzML centroid spectra are normally stored in ascending m/z; sort only if they are not.
		ascending = True
		for k in range(1, mz.size):
			if mz[k] < mz[k - 1]:
				ascending = False
				break
		if not ascending:
			order = np.argsort(mz)
			mz = mz[order]
			intensity = intensity[order]

		for j in range(n_ref):
			reference = reference_mz[j]
			centre = reference * (1.0 + centre_ppm[s, j] * 1e-6)
			half_width = reference * window_ppm * 1e-6
			left = np.searchsorted(mz, centre - half_width)
			right = np.searchsorted(mz, centre + half_width, side='right')
			best = -1
			best_intensity = -1.0
			for k in range(left, right):
				if intensity[k] >= min_intensity and intensity[k] > best_intensity:
					best = k
					best_intensity = intensity[k]
			if best >= 0:
				errors[s, j] = (mz[best] - reference) / reference * 1e6

	return errors


def _match_all(mz_list, intensity_list, reference_mz, centre_ppm, window_ppm, min_intensity):
	"""Blocked `match_references` over a whole sample; `centre_ppm(start, stop)` gives each block's centres."""
	n_spectra = len(mz_list)
	errors = np.full((n_spectra, reference_mz.size), np.nan, dtype=np.float32)
	for start in range(0, n_spectra, _RECAL_BLOCK_SPECTRA):
		stop = min(start + _RECAL_BLOCK_SPECTRA, n_spectra)
		flat_mz, flat_intensity, offsets = _flatten_spectra(mz_list[start:stop], intensity_list[start:stop])
		if flat_mz.size == 0:
			continue
		errors[start:stop] = match_references(flat_mz, flat_intensity, offsets, reference_mz,
											  centre_ppm(start, stop), float(window_ppm), float(min_intensity))
	return errors


def _smooth_and_fill_lines(values: np.ndarray) -> np.ndarray:
	"""
	Running median over `_RECAL_LINE_SMOOTHING` scan lines (NaN = no estimate), then linear
	interpolation over lines that are still empty, holding the edge values. Returns zeros when no
	line has an estimate.
	"""
	if values.size == 0 or not np.isfinite(values).any():
		return np.zeros_like(values, dtype=np.float64)
	half = _RECAL_LINE_SMOOTHING // 2
	padded = np.pad(values.astype(np.float64), half, constant_values=np.nan)
	windows = np.lib.stride_tricks.sliding_window_view(padded, _RECAL_LINE_SMOOTHING)
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN windows
		smoothed = np.nanmedian(windows, axis=1)
	valid = np.isfinite(smoothed)
	index = np.arange(values.size)
	return np.interp(index, index[valid], smoothed[valid])


def neighbourhood_nanmedian(values: np.ndarray, pixel_coordinates: np.ndarray) -> np.ndarray:
	"""
	3 x 3 raster nan-median of a per-spectrum value (NaN = invalid), including the spectrum itself.

	`pixel_coordinates` is the (N, 2) integer raster grid of the spectra (any origin). The result is
	NaN where no spectrum of the neighbourhood holds a valid value.
	"""
	n_spectra = values.size
	result = np.full(n_spectra, np.nan, dtype=np.float64)
	if n_spectra == 0:
		return result

	rows = (pixel_coordinates[:, 0] - pixel_coordinates[:, 0].min()).astype(np.int64)
	cols = (pixel_coordinates[:, 1] - pixel_coordinates[:, 1].min()).astype(np.int64)
	height, width = int(rows.max()) + 1, int(cols.max()) + 1
	index_dtype = np.int32 if n_spectra < np.iinfo(np.int32).max else np.int64
	spectrum_at = np.full((height, width), -1, dtype=index_dtype)
	spectrum_at[rows, cols] = np.arange(n_spectra, dtype=index_dtype)
	values = values.astype(np.float64, copy=False)

	shifts = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1)]
	for start in range(0, n_spectra, 1_000_000):
		stop = min(start + 1_000_000, n_spectra)
		r, c = rows[start:stop], cols[start:stop]
		stack = np.full((len(shifts), stop - start), np.nan, dtype=np.float64)
		for k, (dr, dc) in enumerate(shifts):
			rr, cc = r + dr, c + dc
			inside = (rr >= 0) & (rr < height) & (cc >= 0) & (cc < width)
			neighbour = np.full(stop - start, -1, dtype=np.int64)
			neighbour[inside] = spectrum_at[rr[inside], cc[inside]]
			has = neighbour >= 0
			stack[k, has] = values[neighbour[has]]
		with warnings.catch_warnings():
			warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN neighbourhoods
			result[start:stop] = np.nanmedian(stack, axis=0)
	return result


@dataclass
class MsiRecalibration:
	'''
	Per-spectrum m/z recalibration of one sample and ion mode.

	The ppm error of spectrum i at mass m is `offset_ppm[i] + slope_ppm_per_da[i] * (m - centre_mz)`,
	and the corrected mass is `m / (1 + error * 1e-6)`.
	'''
	offset_ppm: np.ndarray        # (n_spectra,) float64: line offset + pixel residual at centre_mz
	slope_ppm_per_da: np.ndarray  # (n_spectra,) float64: line slope, 0 when not supported
	centre_mz: float              # mean reference m/z
	matches: np.ndarray           # (n_spectra,) int16: references matched in the pixel-level pass
	summary: dict

	@classmethod
	def identity(cls, n_spectra: int) -> 'MsiRecalibration':
		zeros = np.zeros(n_spectra, dtype=np.float64)
		return cls(zeros, zeros.copy(), 0.0, np.zeros(n_spectra, dtype=np.int16), {})

	def apply(self, mz_list: list[np.ndarray], indices=None) -> list[np.ndarray]:
		'''Recalibrated copies of `mz_list`; `indices[k]` is the spectrum index of `mz_list[k]` (default: k).'''
		indices = range(len(mz_list)) if indices is None else indices
		corrected = []
		for mz, i in zip(mz_list, indices):
			error_ppm = self.offset_ppm[i] + self.slope_ppm_per_da[i] * (mz.astype(np.float64) - self.centre_mz)
			corrected.append((mz / (1.0 + error_ppm * 1e-6)).astype(mz.dtype, copy=False))
		return corrected


def estimate_recalibration(
		mz_list: list[np.ndarray],
		intensity_list: list[np.ndarray],
		pixel_coordinates: np.ndarray,
		line_axis: int,
		reference_mz: np.ndarray,
		mass_tolerance: float,
		min_intensity: float = 0.0,
) -> MsiRecalibration:
	'''
	Estimate the per-spectrum m/z recalibration of one sample and ion mode.

	1. Line level. Every reference is matched in every spectrum within the wide window
	   `recalibration_search_ppm(mass_tolerance)`. Per scan line, the per-reference median ppm
	   errors are fitted by `a + b * (reference - centre_mz)`; `b` is kept only when enough
	   references spanning enough of the m/z range are matched, and is fitted robustly (one
	   outlier-rejection pass at `_RECAL_OUTLIER_SIGMA` robust standard deviations). `b` and `a`
	   are smoothed by a running median over lines and lines without an estimate are filled by
	   interpolation, so every line receives a correction.
	2. Pixel level. References are matched again within the same wide window, now centred on the
	   line prediction, so drift along a line (e.g. laser decay within the row) stays in reach of
	   the row ends. The references are isolated within that window (see
	   _CalibrationReferenceSelector), so the width does not add ambiguity. Each spectrum's
	   residual is the median residual over its matched references (at least
	   `_RECAL_MIN_PIXEL_MATCHES`), then the 3 x 3 raster nan-median of the residuals is taken;
	   spectra with no valid neighbour keep the line model alone.

	Parameters
	----------
	mz_list, intensity_list : list[np.ndarray]
		One array per spectrum.
	pixel_coordinates : np.ndarray
		(N, 2) integer raster coordinates of the spectra; any origin (0- or 1-based).
	line_axis : int
		Column of `pixel_coordinates` that indexes the scan lines.
	reference_mz : np.ndarray
		Ascending reference m/z values.
	mass_tolerance : float
		Mass tolerance in ppm; sets the search window (`recalibration_search_ppm`).
	min_intensity : float
		Minimum intensity of a calibrant match.
	'''
	n_spectra = len(mz_list)
	reference_mz = np.sort(np.asarray(reference_mz, dtype=np.float64))
	if n_spectra == 0 or reference_mz.size == 0:
		return MsiRecalibration.identity(n_spectra)

	n_ref = reference_mz.size
	centre_mz = float(reference_mz.mean())
	reference_offset = reference_mz - centre_mz
	reference_span = float(reference_mz[-1] - reference_mz[0])
	pixel_coordinates = np.asarray(pixel_coordinates)
	line = (pixel_coordinates[:, line_axis] - pixel_coordinates[:, line_axis].min()).astype(np.int64)
	n_lines = int(line.max()) + 1

	# Pass 1: wide window centred on the reference itself.
	search_ppm = recalibration_search_ppm(mass_tolerance)
	coarse = _match_all(mz_list, intensity_list, reference_mz,
						lambda start, stop: np.zeros((stop - start, n_ref), dtype=np.float64),
						search_ppm, min_intensity)

	# Per line and reference: median error and match count.
	line_median = np.full((n_lines, n_ref), np.nan, dtype=np.float64)
	line_matches = np.zeros(n_lines, dtype=np.int64)
	spectrum_idx, ref_idx = np.nonzero(np.isfinite(coarse))
	if spectrum_idx.size:
		point_line = line[spectrum_idx]
		point_error = coarse[spectrum_idx, ref_idx].astype(np.float64)
		key = point_line * n_ref + ref_idx
		order = np.argsort(key, kind='stable')
		key, point_error = key[order], point_error[order]
		unique_keys, starts = np.unique(key, return_index=True)
		bounds = np.append(starts, key.size)
		for k, flat_key in enumerate(unique_keys):
			line_median[flat_key // n_ref, flat_key % n_ref] = np.median(point_error[bounds[k]:bounds[k + 1]])
		line_matches = np.bincount(point_line, minlength=n_lines)
	del coarse

	# Line slope, where the matched references support it.
	slope = np.full(n_lines, np.nan, dtype=np.float64)
	for l in range(n_lines):
		matched = np.isfinite(line_median[l])
		if line_matches[l] < _RECAL_MIN_LINE_MATCHES or matched.sum() < _RECAL_SLOPE_MIN_REFERENCES:
			continue
		x, y = reference_offset[matched], line_median[l, matched]
		if reference_span <= 0 or (x.max() - x.min()) < _RECAL_SLOPE_MIN_SPAN * reference_span:
			continue
		b, a = np.polyfit(x, y, 1)
		residual = y - (a + b * x)
		spread = _MAD_TO_SIGMA * np.median(np.abs(residual - np.median(residual)))
		keep = np.abs(residual) <= _RECAL_OUTLIER_SIGMA * spread if spread > 0 else np.ones_like(residual, dtype=bool)
		if keep.sum() >= _RECAL_SLOPE_MIN_REFERENCES and (x[keep].max() - x[keep].min()) >= _RECAL_SLOPE_MIN_SPAN * reference_span:
			b, a = np.polyfit(x[keep], y[keep], 1)
		slope[l] = b
	slope_supported = int(np.isfinite(slope).sum())
	slope = _smooth_and_fill_lines(slope)

	# Line offset given the slope: median over references of the slope-corrected errors.
	offset = np.full(n_lines, np.nan, dtype=np.float64)
	for l in range(n_lines):
		matched = np.isfinite(line_median[l])
		if line_matches[l] >= _RECAL_MIN_LINE_MATCHES and matched.any():
			offset[l] = np.median(line_median[l, matched] - slope[l] * reference_offset[matched])
	lines_present = np.zeros(n_lines, dtype=bool)
	lines_present[line] = True
	lines_estimated = int((np.isfinite(offset) & lines_present).sum())
	offset = _smooth_and_fill_lines(offset)

	# Pass 2: the same window, centred on the line prediction.
	predicted = lambda start, stop: (offset[line[start:stop], None]
									 + slope[line[start:stop], None] * reference_offset[None, :])
	fine = _match_all(mz_list, intensity_list, reference_mz, predicted, search_ppm, min_intensity)
	matches = np.isfinite(fine).sum(axis=1).astype(np.int16)
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)  # spectra without matches
		residual = np.nanmedian(fine - predicted(0, n_spectra), axis=1)
	del fine
	residual[matches < _RECAL_MIN_PIXEL_MATCHES] = np.nan
	own = np.isfinite(residual)
	smoothed = neighbourhood_nanmedian(residual, pixel_coordinates)
	filled = np.isfinite(smoothed)
	smoothed[~filled] = 0.0

	line_offset = offset[line]
	summary = {
		"lines": int(lines_present.sum()),
		"lines_estimated": lines_estimated,
		"lines_with_slope": slope_supported,
		"line_offset_ppm": (float(line_offset.min()), float(line_offset.max())),
		"pixel_residual_ppm": float(_MAD_TO_SIGMA * np.median(np.abs(residual[own] - np.median(residual[own])))) if own.any() else 0.0,
		"pixels_matched": float(own.mean()),
		"pixels_filled": float((filled & ~own).mean()),
	}
	return MsiRecalibration(
		offset_ppm=line_offset + smoothed,
		slope_ppm_per_da=slope[line],
		centre_mz=centre_mz,
		matches=matches,
		summary=summary,
	)


def describe_recalibration(model: MsiRecalibration) -> str:
	'''One-line summary of a recalibration for the activity log.'''
	s = model.summary
	if not s:
		return "no recalibration references"
	low, high = s["line_offset_ppm"]
	return (f"line offset {low:+.1f} to {high:+.1f} ppm, pixel residual {s['pixel_residual_ppm']:.1f} ppm, "
			f"{s['lines_estimated']}/{s['lines']} lines estimated, slope on {s['lines_with_slope']} lines, "
			f"{100 * s['pixels_matched']:.0f}% pixels matched, {100 * s['pixels_filled']:.0f}% filled from neighbours")


class MsiSample(BaseSample):

	# Microgrid background detection (see _detect_microgrid_spots).
	# Fraction of spots an ion must occur in to be treated as background (matrix, standards, coating).
	_MICROGRID_BACKGROUND_OCCUPANCY = 0.5
	# Side, in spots, of the median window that models the local background. Cells must cover less than
	# half of it for the median to follow the background only.
	_MICROGRID_BACKGROUND_WINDOW = 9
	# Minimum robust z-score of a seed spot. Raised automatically on large slides (see _MICROGRID_SEED_ALPHA).
	_MICROGRID_SEED_Z = 4.0
	# Expected number of pure-noise seeds per slide; sets the multiple-testing seed threshold.
	_MICROGRID_SEED_ALPHA = 0.01
	# Robust z-score a spot needs to join a seed's connected component.
	_MICROGRID_GROW_Z = 2.0
	# Components above this size are reported as suspect (debris, matrix crystals) but kept.
	_MICROGRID_MAX_COMPONENT_SPOTS = 25
	# Fallback ppm tolerance for the background ion grid when no mass tolerance is given.
	_MICROGRID_DEFAULT_TOLERANCE_PPM = 10
	# Spectra concatenated per vectorised block.
	_MICROGRID_BLOCK_SPECTRA = 20_000

	def __init__(
			self,
			source_path: str,
			sample_id: str,
			modality_name: str,
			double_ion_mode: bool = False,
			ion_mode: MsiIonMode | None = None
	) -> None:
		'''
		Load and preprocess a MSI sample using ImzML and IBD files.

		Parameters
		----------
		source_path : str
			Path to the data source directory. If double_ion_mode is True, this should be the parent directory containing both ion mode subdirectories.
		sample_id : str
			Sample ID.
		modality_name : str
			Name of the modality.
		double_ion_mode : bool
			If True, the sample contains both positive and negative ion mode data in separate subdirectories with format input_path/pos and input_path/neg.
			Each of the two subdirectories must hold a complete .imzML + .ibd pair; an empty one raises FileNotFoundError.
			Prefer letting _create_msi_samples infer this from the files on disk rather than asserting it here.
		ion_mode : MsiIonMode | None
			If double_ion_mode is False, specify the ion mode of the sample. If double_ion_mode is True, this should be None.
		'''

		# Ensure consistency of input parameters
		if not double_ion_mode and ion_mode not in MsiIonMode.list():
			raise ValueError(
				f'Invalid ion_mode value. Expected one of {MsiIonMode.list()} when double_ion_mode is False.')

		super().__init__(source_path, sample_id, modality_name)

		# Which ion modes are present is decided by the imzML/IBD pairs on disk, not by directory
		# presence: the GUI scaffolds both pos/ and neg/ for every MSI modality, so an empty
		# ion mode directory only means that polarity was not acquired.
		self.input_paths = {}
		for mode in (MsiIonMode.list() if double_ion_mode else [ion_mode]):
			mode_path = os.path.join(source_path, sample_id, modality_name, mode)
			if find_imzml_pair(mode_path) is None:
				raise FileNotFoundError(
					f"No complete .imzML + .ibd file pair found in '{mode_path}' for ion mode "
					f"'{mode}' of sample '{sample_id}'."
				)
			self.input_paths[mode] = mode_path

		self.double_ion_mode = double_ion_mode
		self.ion_mode = ion_mode

		self.recalibration_reference: dict[
										  MsiIonMode, np.ndarray] | None = None  # To be set during dataset preprocessing
		self.min_intensity_threshold: float | None = None  # To be set during dataset preprocessing
		self.recalibration_model: dict[MsiIonMode, MsiRecalibration] = {}  # Estimated during dataset preprocessing
		self.filtered_idx: list[int] | None = None  # To be computed during dataset preprocessing
		self._sample_type: MsiSampleType = MsiSampleType.TISSUE  # To be set from process_dataset

		# Initialize the other variables
		self._metadata_files = {}  # For each ion mode, store the absolute path to the imzML file
		self._binary_files = {}  # For each ion mode, store the absolute path to the IBD file
		self._metadata = {}  # For each ion mode, store the metadata extracted from the imzML file
		self._aligned_mz = {}  # For each ion mode, store the aligned M/Z values (obtained from preprocessing)

	@property
	def ion_modes(self) -> list[MsiIonMode]:
		'''
		Get the list of ion modes available in this sample.

		Returns
		-------
		list[MsiIonMode]
			List of ion modes available in this sample.
		'''
		return list(self.input_paths.keys())

	@property
	def recalibration_reference(self) -> dict[MsiIonMode, np.ndarray] | None:
		'''
		Get the recalibration reference M/Z vector for this sample.

		Returns
		-------
		dict[MsiIonMode, np.ndarray] | None
			Dictionary containing the recalibration reference M/Z vector for each ion mode.
			If no recalibration reference is set, returns None.
		'''
		return self._recalibration_reference

	@recalibration_reference.setter
	def recalibration_reference(self, value: dict[MsiIonMode, np.ndarray] | None) -> None:
		'''
		Set the recalibration reference M/Z vector for this sample.

		Parameters
		----------
		value : dict[MsiIonMode, np.ndarray] | None
			Dictionary containing the recalibration reference M/Z vector for each ion mode.
			If no recalibration reference is to be set, provide None.
		'''
		if value is not None:
			if not isinstance(value, dict):
				raise TypeError("Recalibration reference must be a dictionary mapping ion modes to M/Z vectors.")
			for mode in value.keys():
				if not isinstance(value[mode], np.ndarray):
					raise TypeError(f"Recalibration reference for ion mode {mode} must be a numpy array.")

		self._recalibration_reference = value

	@property
	def min_intensity_threshold(self) -> float | None:
		'''
		Get the minimum intensity threshold for this sample.

		Returns
		-------
		float | None
			Dictionary containing the minimum intensity threshold for each ion mode.
		'''
		return self._min_intensity_threshold

	@property
	def foreground_mask(self) -> np.ndarray | None:
		'''
		Get the foreground mask for this sample.

		Returns
		-------
		np.ndarray | None
			Boolean array indicating which spots are foreground (True) and which are background (False).
		'''
		foreground_mask = np.zeros(self._metadata[self.ion_modes[0]][MsiMetadata.PIXEL_COORDINATES].shape[0],
								   dtype=bool)
		if self.filtered_idx is not None:
			foreground_mask[self.filtered_idx] = True

		# If no foreground spots are defined, invert the mask to consider every spot as foreground
		if not np.any(foreground_mask):
			foreground_mask = ~foreground_mask

		return foreground_mask

	@min_intensity_threshold.setter
	def min_intensity_threshold(self, value: float | None) -> None:
		'''
		Set the minimum intensity threshold for this sample.

		Parameters
		----------
		value : float | None
			Dictionary containing the minimum intensity threshold for each ion mode.
		'''

		if value is not None:
			if type(value) not in [float, int]:
				raise TypeError("Minimum intensity threshold must be a float value.")

		self._min_intensity_threshold = value

	def _spectra_to_dict(self, spectra: ET.Element) -> dict:
		'''
		Convert the spectra element to a dictionary.

		Parameters
		----------
		spectra : ET.Element
			The spectra element from the imzML file.

		Returns
		-------
		dict
			A dictionary with the spectrum data.
		'''

		# Check input parameter type
		if not isinstance(spectra, ET.Element):
			raise TypeError('Invalid input type. Expected ET.Element.')

		x, y, mzs, intensities = None, None, None, None
		physical_x, physical_y = None, None

		scan_list = spectra.find(ImzMLFileParser.SCAN_LIST)
		scan = scan_list.find(ImzMLFileParser.SCAN)

		for cv_param in scan.iter(ImzMLFileParser.CV_PARAM):
			if cv_param.attrib['name'] == "position x":
				x = int(cv_param.attrib['value'])
			elif cv_param.attrib['name'] == "position y":
				y = int(cv_param.attrib['value'])
		for user_param in scan.iter(ImzMLFileParser.USER_PARAM):
			if user_param.attrib['name'] == "3DPositionX":
				physical_x = float(user_param.attrib['value'])
			if user_param.attrib['name'] == "3DPositionY":
				physical_y = float(user_param.attrib['value'])

		# Record whether true physical coordinates were present before applying fallback
		has_physical_coordinates = physical_x is not None and physical_y is not None
		if physical_x is None:
			physical_x = float(x)
		if physical_y is None:
			physical_y = float(y)

		bdal = spectra.find(ImzMLFileParser.BINARY_DATA_ARRAY_LIST)

		for element in bdal.findall(ImzMLFileParser.BINARY_DATA_ARRAY):
			for cv_param in element.iter(ImzMLFileParser.CV_PARAM):
				if cv_param.attrib['name'] == "external array length":
					length = int(cv_param.attrib['value'])
				if cv_param.attrib['name'] == "external encoded length":
					encoded_length = int(cv_param.attrib['value'])
				if cv_param.attrib['name'] == "external offset":
					offset = int(cv_param.attrib['value'])

			if element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] in ['mzArray']:
				mzs = {
					'length': length,
					'encoded_length': encoded_length,
					'offset': offset
				}
			elif element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] in ['intensities',
																							   "intensityArray"]:
				intensities = {
					'length': length,
					'encoded_length': encoded_length,
					'offset': offset
				}

		return {'pixel_x': x, 'pixel_y': y, 'mzs': mzs, 'intensities': intensities, 'physical_x': physical_x,
				'physical_y': physical_y, 'has_physical_coordinates': has_physical_coordinates}

	def _correct_rotation_error(self, physical_coords: np.ndarray[np.float32], pixel_coords: np.ndarray[np.int32]) -> \
			np.ndarray[np.float32]:
		"""
		Compute and correct for rotation error between physical coordinates and pixel coordinates.

		Parameters
		----------
		physical_coords : np.ndarray
			Array of shape (N, 2) with physical coordinates (x, y).
		pixel_coords : np.ndarray
			Array of shape (N, 2) with pixel coordinates (x, y).

		Returns
		-------
		rotated_physical : np.ndarray
			Array of shape (N, 2) with rotated physical coordinates.
		"""

		# Select the X coordinate from the pixel set based on the line with the most points
		x_coords, count = np.unique(pixel_coords[:, 0], return_counts=True)
		fixed_pixel_x = x_coords[np.argmax(count)]

		# Select points corresponding to this fixed pixel X
		mask = pixel_coords[:, 0] == fixed_pixel_x
		points_line = physical_coords[mask]

		# Fit a line y = ax + b
		model = LinearRegression().fit(points_line[:, 0].reshape(-1, 1), points_line[:, 1])
		slope = model.coef_[0]
		angle_rad = np.arctan(slope)  # angle of line from horizontal

		# Define helper function (for code clarity)
		def rotate(points, angle, center):
			R = np.array([[np.cos(angle), -np.sin(angle)],
						  [np.sin(angle), np.cos(angle)]])
			shifted = points - center
			rotated = shifted.dot(R.T) + center
			return rotated

		# Rotate all physical points by -angle_rad around centroid
		centroid = physical_coords.mean(axis=0)
		rotated_physical = rotate(physical_coords, -angle_rad, centroid)
		return rotated_physical

	def _parse_imzml(self, filename: str) -> dict:
		'''
		Parse the imzML files to extract the relevant metadata and the M/Z values.

		Returns
		-------
		tuple[dict, list[dict]]
			A tuple containing two dictionaries:
			- The first element is a dictionary that contains the metadata associated with the experiment
			- The second element is a list of dictionaries, each containing the parsed M/Z values for each ion mode
		'''

		# Check that the file can be read
		if not os.path.exists(filename):
			raise FileNotFoundError(f"ImzML file {filename} does not exist.")
		if not os.access(filename, os.R_OK):
			raise PermissionError(f"ImzML file {filename} is not readable.")

		# Parse the imzML file
		tree = ET.parse(filename)
		root = tree.getroot()

		# First determine the data types
		mz_dtype, intensities_dtype = None, None

		# Define utility to convert string to dtype
		str_to_dtype = lambda s: np.float32 if s == "32-bit float" else np.float64

		# Extract the data types for mz and intensities
		for rpg in root.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST):
			if rpg.attrib['id'] in ['mzArray']:
				for cv_param in rpg:
					if "float" in cv_param.attrib['name']:
						mz_dtype = str_to_dtype(cv_param.attrib['name'])
			elif rpg.attrib['id'] in ['intensities', "intensityArray"]:
				for cv_param in rpg:
					if "float" in cv_param.attrib['name']:
						intensities_dtype = str_to_dtype(cv_param.attrib['name'])

		if mz_dtype is None or intensities_dtype is None:
			raise KeyError(
				"Could not find the data types for mz and intensities in the imzML file. Check the metadata name")

		# Extract the raster size in micrometers
		raster_size = np.array([0, 0], dtype=np.int16)
		# Scan lines run along x for a horizontal line scan (the imzML default assumed here) and along
		# y for a vertical one. PIXEL_COORDINATES stores (y, x), so its column 0 indexes horizontal
		# lines and column 1 vertical ones.
		line_axis = 0
		for scan_settings_list in root.find(ImzMLFileParser.SCAN_SETTINGS_LIST):
			for scan_settings in scan_settings_list:
				name = scan_settings.attrib.get('name')
				if name == "pixel size x":
					raster_size[0] = int(scan_settings.attrib['value'])
				elif name == "pixel size y":
					raster_size[1] = int(scan_settings.attrib['value'])
				elif name == "vertical line scan":
					line_axis = 1
				elif name == "horizontal line scan":
					line_axis = 0

		# If raster size is absent from the file, assume unit spacing so that pixel
		# coordinates can be used directly as physical coordinates without overlap.
		if raster_size[0] == 0 or raster_size[1] == 0:
			raster_size = np.array([1, 1], dtype=np.int16)

		run = root.find(ImzMLFileParser.RUN_KEY)
		spectrum_list = run.find(ImzMLFileParser.SPECTRUM_LIST_KEY)
		spectra = spectrum_list.findall(ImzMLFileParser.SPECTRUM_KEY)

		# Decode the binary data from the imzML file
		parsed_spectra = [self._spectra_to_dict(spectrum) for spectrum in spectra]

		# Convert the parsed spectra to a set of Numpy arrays to facilitate further processing
		pixel_coordinates = np.array([(metadata["pixel_y"], metadata['pixel_x']) for metadata in parsed_spectra],
									 dtype=np.int32)  #NOTE: The X and Y axes are inverted between pixel and physical coordinates
		physical_coordinates = np.array(
			[(metadata["physical_x"], metadata['physical_y']) for metadata in parsed_spectra], dtype=np.float32)
		mz_binary_metadata = np.array(
			[(metadata["mzs"]["length"], metadata["mzs"]["encoded_length"], metadata["mzs"]["offset"]) for metadata in
			 parsed_spectra])
		intensities_binary_metadata = np.array([(metadata["intensities"]["length"],
												 metadata["intensities"]["encoded_length"],
												 metadata["intensities"]["offset"]) for metadata in parsed_spectra])

		# Where physical coordinates were absent, the fallback stored raw pixel indices.
		# Scale those entries by raster_size so the inter-spot spacing is in micrometers,
		# consistent with the downstream normalization and raster_size/2 centering shift.
		has_physical = np.array([m['has_physical_coordinates'] for m in parsed_spectra])
		if not has_physical.any():
			physical_coordinates = pixel_coordinates.astype(np.float32) * raster_size
		elif not has_physical.all():
			physical_coordinates[~has_physical] = pixel_coordinates[~has_physical].astype(np.float32) * raster_size

		# Align MSI laser grid to the axes using linear regression
		physical_coordinates = self._correct_rotation_error(physical_coordinates, pixel_coordinates)

		# Define the metadata dictionary
		experiment_metadata = {
			MsiMetadata.INTENSITIES_DTYPE: intensities_dtype,
			MsiMetadata.MZ_DTYPE: mz_dtype,
			MsiMetadata.RASTER_SIZE: raster_size,
			MsiMetadata.LINE_AXIS: line_axis,
			MsiMetadata.PIXEL_COORDINATES: pixel_coordinates,
			MsiMetadata.PHYSICAL_COORDINATES: physical_coordinates,
			MsiMetadata.MZ_BINARY_METADATA: mz_binary_metadata,
			MsiMetadata.INTENSITIES_BINARY_METADATA: intensities_binary_metadata
		}

		return experiment_metadata

	def _validate_binary_data(self, mode: 'MsiIonMode') -> set[int]:
		"""Validate the IBD binary file for *mode* before any processing begins.

		Performs two checks:

		1. **File-size check** – raises ``ValueError`` if the IBD file is smaller
		   than the byte range implied by all (offset + count × itemsize) pairs in
		   the imzML.  A truncated file is unrecoverable.

		2. **Full NaN / Inf scan** – reads every spectrum via memory-mapped I/O and
		   flags those containing non-finite m/z or intensity values.  Corrupted
		   spectra are returned as a set of indices so the caller can filter them
		   out rather than aborting.

		Returns
		-------
		set[int]
		    Indices of spectra that contain NaN/Inf values.  Empty when all
		    spectra are clean.
		"""
		ibd_path = self._binary_files[mode]
		meta = self._metadata[mode]
		mz_meta = meta[MsiMetadata.MZ_BINARY_METADATA]           # shape (n_spectra, 3)
		int_meta = meta[MsiMetadata.INTENSITIES_BINARY_METADATA]  # shape (n_spectra, 3)
		# columns: [0] = n_values (count), [1] = encoded_length (bytes), [2] = byte offset
		mz_dtype: np.dtype = meta[MsiMetadata.MZ_DTYPE]
		int_dtype: np.dtype = meta[MsiMetadata.INTENSITIES_DTYPE]
		n_spectra: int = mz_meta.shape[0]
		mz_itemsize: int = np.dtype(mz_dtype).itemsize
		int_itemsize: int = np.dtype(int_dtype).itemsize

		# ── 1. File-size check ─────────────────────────────────────────────────
		actual_size = os.path.getsize(ibd_path)

		# The minimum required file size is the highest byte address that any
		# spectrum occupies: max over all spectra of (offset + count × itemsize).
		last_mz_byte  = int(np.max(mz_meta[:, 2]  + mz_meta[:, 0]  * mz_itemsize))
		last_int_byte = int(np.max(int_meta[:, 2] + int_meta[:, 0] * int_itemsize))
		required_size = max(last_mz_byte, last_int_byte)

		if actual_size < required_size:
			missing = required_size - actual_size
			raise ValueError(
				f"[Sample '{self.sample_id}' – {str(mode).upper()} mode] "
				f"IBD file is truncated: {actual_size:,} bytes on disk, "
				f"{required_size:,} bytes required for {n_spectra:,} spectra "
				f"({missing:,} bytes missing, {missing / required_size * 100:.1f}% absent).\n"
				f"  File: {ibd_path}"
			)

		# ── 2. Full NaN / Inf scan ─────────────────────────────────────────────
		# Read every spectrum via memory-mapped I/O and validate with np.isfinite.
		# Each worker thread opens its own mmap so file reads are truly parallel.
		# np.frombuffer creates zero-copy views — no extra allocation per spectrum.

		n_workers = utils.available_cpus() or 1
		chunk_size = max(1, -(-n_spectra // n_workers))  # ceiling division
		chunks = [range(i, min(i + chunk_size, n_spectra))
				  for i in range(0, n_spectra, chunk_size)]

		def _check_chunk(spec_range):
			chunk_bad_mz: dict[int, int] = {}
			chunk_bad_int: dict[int, int] = {}
			with open(ibd_path, 'rb') as fh:
				mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
				try:
					for idx in spec_range:
						count_mz = int(mz_meta[idx, 0])
						if count_mz > 0:
							arr = np.frombuffer(mm, dtype=mz_dtype,
												count=count_mz,
												offset=int(mz_meta[idx, 2]))
							n_bad = int(np.sum(~np.isfinite(arr)))
							del arr  # release mmap reference before next read
							if n_bad:
								chunk_bad_mz[idx] = n_bad

						count_int = int(int_meta[idx, 0])
						if count_int > 0:
							arr = np.frombuffer(mm, dtype=int_dtype,
												count=count_int,
												offset=int(int_meta[idx, 2]))
							n_bad = int(np.sum(~np.isfinite(arr)))
							del arr  # release mmap reference before next read
							if n_bad:
								chunk_bad_int[idx] = n_bad
				finally:
					mm.close()
			return chunk_bad_mz, chunk_bad_int

		bad_mz: dict[int, int] = {}
		bad_int: dict[int, int] = {}

		with tqdm.tqdm(total=n_spectra,
					   desc=f"Validating IBD ({str(mode).upper()}, {self.sample_id})",
					   unit="spec") as pbar:
			with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
				futures = {executor.submit(_check_chunk, chunk): chunk
						   for chunk in chunks}
				for future in concurrent.futures.as_completed(futures):
					bm, bi = future.result()
					bad_mz.update(bm)
					bad_int.update(bi)
					pbar.update(len(futures[future]))

		return set(bad_mz.keys()) | set(bad_int.keys())

	def _compute_raster_coordinates(physical_coords: np.ndarray, raster_size: np.ndarray[np.int16]) -> np.ndarray[
		np.int32]:
		'''
		Convert physical coordinates (in microns) to raster pixel coordinates. This method assumes a raster pixel size of 1 micron.
		
		Parameters
		----------
		physical_coords : np.ndarray
			An (N, 2) array of physical coordinates in microns.
		raster_size : np.ndarray[np.int16]
			A (2,) array specifying the size of the raster in pixels (width, height).

		Returns
		-------
		np.ndarray[np.int32]
			An (N, 2, 2) array representing, for each physical point, the top-left and bottom-right pixel coordinates of the corresponding rectangle in the raster.
			All the raster pixels covered by the rectangle refers to the same physical point.
		'''

		# Find which block each coordinate belongs to
		# tile_x, tile_y are the block indices
		tile_x = np.floor_divide(physical_coords[:, 0], raster_size[0]).astype(int)
		tile_y = np.floor_divide(physical_coords[:, 1], raster_size[1]).astype(int)

		# Top-left coordinate of the block in raster
		x1 = tile_x * raster_size[0]
		y1 = tile_y * raster_size[1]

		# Bottom-right coordinate
		x2 = x1 + raster_size[0]
		y2 = y1 + raster_size[1]

		# Output shape: (N,2,2): [[x1,y1],[x2,y2]] for each coord
		pixel_coords = np.zeros((physical_coords.shape[0], 2, 2), dtype=np.int32)
		pixel_coords[:, 0, :] = np.stack([x1, y1], axis=1)  # top-left
		pixel_coords[:, 1, :] = np.stack([x2, y2], axis=1)  # bottom-right

		return pixel_coords

	def _filter_unpaired_spots(self) -> None:
		'''
		Analyze the metadata of both ion modes and remove unpaired spots (experimental artifacts).
		Only pixels present in both ion modes are retained.
		'''

		if not self.double_ion_mode:
			return

		# Extract pixel and physical coordinates for both ion modes
		pos_pixel_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PIXEL_COORDINATES]
		neg_pixel_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PIXEL_COORDINATES]
		pos_phys_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES]
		neg_phys_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES]

		# Debug: show pixel and physical extremes before any correction
		for mode_label, pix, phys in [
			("POSITIVE", pos_pixel_coords, pos_phys_coords),
			("NEGATIVE", neg_pixel_coords, neg_phys_coords),
		]:
			def _fmt(idx, pix=pix, phys=phys):
				return f"pixel=({pix[idx, 0]},{pix[idx, 1]}) phys=({phys[idx, 0]:.1f},{phys[idx, 1]:.1f})"

			logger.debug(
				f"[filter_unpaired_spots] {mode_label} extremes:\n"
				f"  min_x  {_fmt(int(np.argmin(pix[:, 0])))}\n"
				f"  max_x  {_fmt(int(np.argmax(pix[:, 0])))}\n"
				f"  min_y  {_fmt(int(np.argmin(pix[:, 1])))}\n"
				f"  max_y  {_fmt(int(np.argmax(pix[:, 1])))}"
			)

		# Detect pixel axis orientation mismatch by fitting a linear pixel->physical map for each mode.
		# T has shape (2, 2): row i contains how pixel axis i drives (phys_x, phys_y).
		# Flipping pixel axis i negates row i of T. We test all 4 flip combinations and pick the one
		# that makes the negative map best align (minimum Frobenius distance) with the positive map.
		def _fit_pixel_transform(pix, phys):
			A = np.column_stack([pix.astype(float), np.ones(len(pix))])
			return np.linalg.lstsq(A, phys.astype(float), rcond=None)[0][:2]

		T_pos = _fit_pixel_transform(pos_pixel_coords, pos_phys_coords)
		T_neg = _fit_pixel_transform(neg_pixel_coords, neg_phys_coords)
		T_pos_norm = T_pos / np.linalg.norm(T_pos, 'fro')
		T_neg_norm = T_neg / np.linalg.norm(T_neg, 'fro')

		flip_candidates = {
			(False, False): np.array([ 1.,  1.]),
			(True,  False): np.array([-1.,  1.]),
			(False, True):  np.array([ 1., -1.]),
			(True,  True):  np.array([-1., -1.]),
		}
		flip_x, flip_y = min(
			flip_candidates,
			key=lambda k: np.linalg.norm(T_neg_norm * flip_candidates[k][:, None] - T_pos_norm, 'fro')
		)

		if flip_x or flip_y:
			neg_pixel_coords = neg_pixel_coords.copy()
			min_x, max_x = int(neg_pixel_coords[:, 0].min()), int(neg_pixel_coords[:, 0].max())
			min_y, max_y = int(neg_pixel_coords[:, 1].min()), int(neg_pixel_coords[:, 1].max())
			if flip_x:
				neg_pixel_coords[:, 0] = max_x + min_x - neg_pixel_coords[:, 0]
			if flip_y:
				neg_pixel_coords[:, 1] = max_y + min_y - neg_pixel_coords[:, 1]
			self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PIXEL_COORDINATES] = neg_pixel_coords
			get_reporter().detail(f"Pixel axis flip applied to the NEGATIVE mode (flip_x={flip_x}, flip_y={flip_y})")
		else:
			logger.debug("[filter_unpaired_spots] No pixel axis flip needed")

		# Build structured arrays AFTER the flip so comparisons use corrected coordinates
		dtype = [('x', pos_pixel_coords.dtype), ('y', pos_pixel_coords.dtype)]
		pos_pixel_coords_struct = pos_pixel_coords.view(dtype)
		neg_pixel_coords_struct = neg_pixel_coords.view(dtype)

		# Find the intersection: keep pixels present in BOTH modes
		pos_mask = np.isin(pos_pixel_coords_struct, neg_pixel_coords_struct)
		neg_mask = np.isin(neg_pixel_coords_struct, pos_pixel_coords_struct)

		pos_indices = np.nonzero(pos_mask)[0]
		neg_indices = np.nonzero(neg_mask)[0]

		# After the pixel flip the negative array is still in its original scan order, which is
		# the reverse of positive when a flip was applied. Pairing by array index would therefore
		# match opposite ends of the scan, producing a reflected affine transform and wavy output.
		# Reorder neg_indices so that neg_pixel_coords[neg_indices[k]] == pos_pixel_coords[pos_indices[k]].
		neg_pix_to_idx = {
			(int(neg_pixel_coords[ni, 0]), int(neg_pixel_coords[ni, 1])): ni
			for ni in neg_indices
		}
		neg_indices = np.array([
			neg_pix_to_idx[(int(pos_pixel_coords[pi, 0]), int(pos_pixel_coords[pi, 1]))]
			for pi in pos_indices
		])

		pos_shape_filtered = (int(pos_pixel_coords[pos_indices, 0].max()), int(pos_pixel_coords[pos_indices, 1].max()))
		neg_shape_filtered = (int(neg_pixel_coords[neg_indices, 0].max()), int(neg_pixel_coords[neg_indices, 1].max()))
		logger.debug(f"[filter_unpaired_spots] POSITIVE grid shape after intersection: {pos_shape_filtered}")
		logger.debug(f"[filter_unpaired_spots] NEGATIVE grid shape after intersection: {neg_shape_filtered}")

		# Filter positive mode metadata
		self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PIXEL_COORDINATES] = pos_pixel_coords[pos_indices]
		self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES] = (
			self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES][pos_indices]
		)
		self._metadata[MsiIonMode.POSITIVE][MsiMetadata.MZ_BINARY_METADATA] = (
			self._metadata[MsiIonMode.POSITIVE][MsiMetadata.MZ_BINARY_METADATA][pos_indices]
		)
		self._metadata[MsiIonMode.POSITIVE][MsiMetadata.INTENSITIES_BINARY_METADATA] = (
			self._metadata[MsiIonMode.POSITIVE][MsiMetadata.INTENSITIES_BINARY_METADATA][pos_indices]
		)

		# Filter negative mode metadata
		self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PIXEL_COORDINATES] = neg_pixel_coords[neg_indices]
		self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES] = (
			self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES][neg_indices]
		)
		self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.MZ_BINARY_METADATA] = (
			self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.MZ_BINARY_METADATA][neg_indices]
		)
		self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.INTENSITIES_BINARY_METADATA] = (
			self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.INTENSITIES_BINARY_METADATA][neg_indices]
		)

	def initialize_sample(self) -> None:
		'''
		Initialize this object by loading the relevant metadata from the ImzML file,
		correcting the coordinates and load the M/Z values to perform dataset alignment.
		'''

		# Load the imzML files for each ion mode
		for mode, input_path in self.input_paths.items():
			# Same resolver that decided which ion modes this sample has, so discovery and loading
			# can never disagree. It returns the real on-disk .ibd name, no extension guesswork.
			pair = find_imzml_pair(input_path)
			if pair is None:
				raise FileNotFoundError(f"No complete .imzML + .ibd file pair found in '{input_path}'.")
			self._metadata_files[mode], self._binary_files[mode] = pair

			# Parse the imzML file to extract the metadata and the parsed spectra
			self._metadata[mode] = self._parse_imzml(self._metadata_files[mode])

			# Validate the binary payload before any downstream processing.
			# Raises ValueError on truncation; returns indices of NaN/Inf spectra.
			corrupt_indices = self._validate_binary_data(mode)
			if corrupt_indices:
				n_total = self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA].shape[0]
				keep = np.array([i for i in range(n_total) if i not in corrupt_indices], dtype=np.intp)
				self._metadata[mode][MsiMetadata.PIXEL_COORDINATES] = (
					self._metadata[mode][MsiMetadata.PIXEL_COORDINATES][keep]
				)
				self._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES] = (
					self._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES][keep]
				)
				self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA] = (
					self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA][keep]
				)
				self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA] = (
					self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA][keep]
				)
				get_reporter().warning(
					f"{str(mode).upper()} mode: filtered {len(corrupt_indices):,} corrupted datapoint(s) "
					f"({len(corrupt_indices) / n_total * 100:.1f}% of {n_total:,} spectra)"
				)

		# If there are both ion modes, correct the physical coordinates offset between the two
		if self.double_ion_mode:

			# Filter out unpaired points (experimental artifacts)
			self._filter_unpaired_spots()

			# Compute the offset between the two physical coordinates sets
			pos_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES]
			neg_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES]

			# Use an affine transformation to account for the translation
			def affine_transform(points):
				aug = np.hstack([points, np.ones((points.shape[0], 1))])
				x_new = model_x.predict(aug)
				y_new = model_y.predict(aug)
				return np.stack([x_new, y_new], axis=1)

			A = np.hstack([pos_coords, np.ones((pos_coords.shape[0], 1))])
			model_x = LinearRegression().fit(A, neg_coords[:, 0])
			model_y = LinearRegression().fit(A, neg_coords[:, 1])

			# Apply the transformation to the positive ion mode physical coordinates
			pos_coords_transformed = affine_transform(pos_coords)

			# Depending on the raster size, move the coordinates to the center
			if self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] == \
					self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]:
				# The raster is square, offset along both axes
				offset = (self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] // 2,
						  self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] // 2)
			elif self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] > \
					self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]:
				# The raster is rectangular, offset along the X axis
				offset = (self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] // 2, 0)
			else:
				# The raster is rectangular, offset along the Y axis
				offset = (0, self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] // 2)

			# Apply the offset to the transformed coordinates and compute the average between pos and neg to have the coordinate of the raster's center
			pos_coords_transformed -= np.array(offset)
			final_physical_coords = np.mean([pos_coords_transformed, neg_coords],
											axis=0)  # Midpoint between the two physical sets
		else:
			final_physical_coords = self._metadata[self.ion_mode][MsiMetadata.PHYSICAL_COORDINATES]

		# Normalize the physical coordinates to start from (0,0)
		final_physical_coords -= final_physical_coords.min(axis=0)

		# Shift them to be the center of the raster
		offset = self._metadata[mode][MsiMetadata.RASTER_SIZE].astype(np.float64) / 2
		final_physical_coords += offset

		# Replace the physical coordinates in the metadata
		for mode in self._metadata.keys():
			self._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES] = final_physical_coords

			# Compute the raster pixel coordinates for each physical point
			self._metadata[mode][MsiMetadata.RASTER_COORDINATES] = MsiSample._compute_raster_coordinates(
				final_physical_coords,
				self._metadata[mode][MsiMetadata.RASTER_SIZE]
			)

	def _detect_tissue_spots(
			self,
			mz_vectors: list[np.ndarray],
			intensity_vectors: list[np.ndarray],
			ion_mode: MsiIonMode,
			database: pd.DataFrame | None = None,
			mass_tolerance: int | None = None,
	) -> list[int]:
		"""
		Identify tissue/cell spots from background using spectral complexity features.

		Tissue spots have richer, more diverse spectra than background spots (which are
		dominated by uniformly sprayed standards and slide coating). Three parameter-free
		features capture this: Shannon entropy, number of detected peaks, and log(TIC).
		An optional annotation DB hit ratio is added as a 4th feature when a database and
		mass_tolerance are provided.

		Behavior adapts based on self._sample_type:
		  "tissue"   -- contiguous tissue section. A 2-component GMM is fit; BIC selects
					   between 1-component (unimodal → keep all) and 2-component (bimodal →
					   classify by posterior). This handles class-imbalanced distributions
					   (e.g. 95 % tissue / 5 % background) where Otsu splits the dominant
					   tissue mode instead of separating it from background. Morphological
					   cleanup (hole filling + opening) is applied afterwards.
		  "microgrid" -- isolated single cells on a mostly empty slide. Delegated to
					   _detect_microgrid_spots, which models the background explicitly and
					   detects cells as sparse local outliers.

		Returns
		-------
		list[int]
			Indices of spots classified as foreground (tissue/cell).
		"""
		n_spots = len(mz_vectors)
		if len(intensity_vectors) != n_spots:
			raise ValueError("intensity_vectors must have same length as mz_vectors")
		if n_spots == 0:
			return []

		if self._sample_type == MsiSampleType.MICROGRID:
			return self._detect_microgrid_spots(
				mz_vectors=mz_vectors,
				intensity_vectors=intensity_vectors,
				ion_mode=ion_mode,
				database=database,
				mass_tolerance=mass_tolerance,
			)

		# ------------------------------------------------------------------
		# 1. Per-spot spectral complexity features
		# ------------------------------------------------------------------
		n_peaks = np.array([len(mz) for mz in mz_vectors], dtype=np.int32)
		tic = np.zeros(n_spots, dtype=np.float64)
		entropy = np.zeros(n_spots, dtype=np.float64)

		for i in range(n_spots):
			if n_peaks[i] == 0:
				continue
			iv = np.asarray(intensity_vectors[i], dtype=np.float64)
			tic[i] = iv.sum()
			if n_peaks[i] < 2 or tic[i] <= 0:
				continue
			p = iv / tic[i]

			p = p[p > 0]
			entropy[i] = -np.sum(p * np.log2(p))

		valid = n_peaks > 0
		if not np.any(valid):
			return []

		# ------------------------------------------------------------------
		# 2. Optional annotation DB hit-ratio feature
		# ------------------------------------------------------------------
		features: list[np.ndarray] = [entropy, n_peaks.astype(np.float64), np.log1p(tic)]

		if database is not None and mass_tolerance is not None:
			db_subset = database[database["ion_mode"] == ion_mode]
			if not db_subset.empty:
				masses = np.sort(db_subset["ionized_mass"].to_numpy(dtype=np.float64))
				hit_ratio = np.zeros(n_spots, dtype=np.float64)
				for i in range(n_spots):
					if n_peaks[i] == 0:
						continue
					mz = np.asarray(mz_vectors[i], dtype=np.float64)
					idx = np.searchsorted(masses, mz)
					left = np.clip(idx - 1, 0, masses.size - 1)
					right = np.clip(idx, 0, masses.size - 1)
					tol_l = masses[left] * mass_tolerance * 1e-6
					tol_r = masses[right] * mass_tolerance * 1e-6
					within_l = np.abs(mz - masses[left]) <= tol_l
					within_r = np.abs(mz - masses[right]) <= tol_r
					within = within_l | within_r
					if np.any(within):
						w = np.where(within)[0]
						chosen = np.where(within_l[w], left[w], right[w])
						hit_ratio[i] = len(np.unique(chosen)) / n_peaks[i]
				features.append(hit_ratio)

		# ------------------------------------------------------------------
		# 3. Min-max normalise each feature and average into a single score
		# ------------------------------------------------------------------
		score = np.zeros(n_spots, dtype=np.float64)
		for feat in features:
			vals = feat[valid]
			lo, hi = vals.min(), vals.max()
			if hi - lo > 1e-12:
				norm = np.zeros(n_spots, dtype=np.float64)
				norm[valid] = (feat[valid] - lo) / (hi - lo)
				score += norm
		score /= len(features)

		# ------------------------------------------------------------------
		# 4. Threshold: GMM + BIC model selection
		# ------------------------------------------------------------------
		valid_scores = score[valid]
		lo, hi = valid_scores.min(), valid_scores.max()

		tissue_mask = np.zeros(n_spots, dtype=bool)

		# Tissue section: use GMM + BIC to decide between unimodal (all tissue)
		# and bimodal (tissue + background).  This handles imbalanced distributions
		# (e.g. 95 % tissue / 5 % background) where Otsu tends to split the
		# dominant tissue mode rather than separating it from background.
		n_valid = valid_scores.shape[0]
		if n_valid < 4 or hi - lo < 1e-12:
			get_reporter().detail(f"Tissue detection (tissue): degenerate score distribution -- treating all {n_spots} spots as foreground.")
			return np.where(valid)[0].tolist()

		vs2d = valid_scores.reshape(-1, 1)
		gmm1 = GaussianMixture(n_components=1, random_state=0).fit(vs2d)
		gmm2 = GaussianMixture(n_components=2, random_state=0, n_init=3).fit(vs2d)
		bic1, bic2 = gmm1.bic(vs2d), gmm2.bic(vs2d)

		if bic1 <= bic2:
			# BIC prefers unimodal model -- keep all valid spots as tissue
			get_reporter().detail(
				f"Tissue detection (tissue): GMM BIC prefers 1-component "
				f"(BIC1={bic1:.1f} <= BIC2={bic2:.1f}) -- treating all {n_spots} spots as foreground, "
				f"n_features={len(features)}, ion_mode={ion_mode}"
			)
			return np.where(valid)[0].tolist()

		# 2-component model wins: classify by posterior on the higher-mean component
		tissue_comp = int(np.argmax(gmm2.means_.ravel()))
		posteriors = gmm2.predict_proba(vs2d)[:, tissue_comp]
		tissue_mask[valid] = posteriors >= 0.5
		mu0, mu1 = gmm2.means_.ravel()
		pi0, pi1 = gmm2.weights_
		_log_info = (
			f"GMM 2-comp (BIC1={bic1:.1f}, BIC2={bic2:.1f}), "
			f"means=[{mu0:.3f},{mu1:.3f}], weights=[{pi0:.2f},{pi1:.2f}]"
		)

		# ------------------------------------------------------------------
		# 5. Spatial morphological cleanup (tissue section only)
		# ------------------------------------------------------------------
		if self._sample_type == MsiSampleType.TISSUE:
			pixel_coords = self._metadata[ion_mode][MsiMetadata.PIXEL_COORDINATES]  # (N, 2): (y, x)
			if pixel_coords is not None and len(pixel_coords) == n_spots:
				grid_h = int(pixel_coords[:, 0].max()) + 1
				grid_w = int(pixel_coords[:, 1].max()) + 1
				raster = np.zeros((grid_h, grid_w), dtype=bool)
				for i in range(n_spots):
					if tissue_mask[i]:
						raster[pixel_coords[i, 0], pixel_coords[i, 1]] = True
				raster = binary_fill_holes(raster)
				raster = binary_opening(raster, structure=np.ones((3, 3), dtype=bool))
				for i in range(n_spots):
					tissue_mask[i] = raster[pixel_coords[i, 0], pixel_coords[i, 1]]

		kept = np.where(tissue_mask)[0]
		get_reporter().detail(
			f"Tissue detection ({self._sample_type}): kept {kept.size} / {n_spots} spots "
			f"({_log_info}, n_features={len(features)}, ion_mode={ion_mode})"
		)
		return kept.tolist()

	def _detect_microgrid_spots(
			self,
			mz_vectors: list[np.ndarray],
			intensity_vectors: list[np.ndarray],
			ion_mode: MsiIonMode,
			database: pd.DataFrame | None = None,
			mass_tolerance: int | None = None,
	) -> list[int]:
		"""
		Identify cell spots on a microgrid slide as sparse local outliers against the background.

		A microgrid acquisition covers the whole slide, so background spots are the large majority and
		cells cover one or a few spots each. The detection therefore models the background and flags the
		spots that depart from it, instead of splitting the score distribution into two classes.

		1. Background ions: m/z bins (log-ppm grid, one mass tolerance wide) present in at least
		   _MICROGRID_BACKGROUND_OCCUPANCY of the spots, dilated by one bin. Ions found on most of the
		   slide come from matrix, sprayed standards and slide coating.
		2. Per-spot features: log intensity of the non-background (cell) ions, cell-ion fraction of the
		   TIC and, when a database is given, log intensity of the DB-matched cell ions.
		3. Local background correction: each feature is rasterised on the pixel grid and the median over a
		   _MICROGRID_BACKGROUND_WINDOW window is subtracted. Cells cover less than half of the window, so
		   the median follows the background and removes deposition gradients and row drift. The residual
		   is converted to a robust z-score (median / MAD).
		4. Hysteresis threshold on the mean z-score: seeds need z >= max(_MICROGRID_SEED_Z, the Gaussian
		   quantile giving _MICROGRID_SEED_ALPHA expected noise seeds on the slide). Spots with
		   z >= _MICROGRID_GROW_Z that are 8-connected to a seed join it, so a cell spanning a few spots is
		   kept whole.

		Returns
		-------
		list[int]
			Indices of spots classified as foreground (cell).
		"""
		n_spots = len(mz_vectors)
		n_peaks = np.fromiter((len(mz) for mz in mz_vectors), dtype=np.int64, count=n_spots)
		valid = n_peaks > 0
		n_valid = int(valid.sum())
		if n_valid == 0:
			return []

		tolerance_ppm = mass_tolerance if mass_tolerance is not None else self._MICROGRID_DEFAULT_TOLERANCE_PPM
		log_bin_width = tolerance_ppm * 1e-6
		block_spectra = self._MICROGRID_BLOCK_SPECTRA

		# ------------------------------------------------------------------
		# 1. Background ion set
		# ------------------------------------------------------------------
		occupied_bins, bin_counts, _ = _CalibrationReferenceSelector._reduce_spectra(
			mz_vectors, log_bin_width, block_spectra)
		background_bins = occupied_bins[bin_counts >= self._MICROGRID_BACKGROUND_OCCUPANCY * n_valid].astype(np.int64)
		n_background_ions = background_bins.size
		# Dilate by one bin: a background ion near a bin edge spreads over two adjacent bins.
		background_bins = np.unique(np.concatenate([background_bins - 1, background_bins, background_bins + 1]))

		db_masses = None
		if database is not None and mass_tolerance is not None:
			db_subset = database[database["ion_mode"] == ion_mode]
			if not db_subset.empty:
				db_masses = np.sort(db_subset["ionized_mass"].to_numpy(dtype=np.float64))

		# ------------------------------------------------------------------
		# 2. Per-spot features
		# ------------------------------------------------------------------
		tic = np.zeros(n_spots, dtype=np.float64)
		cell_intensity = np.zeros(n_spots, dtype=np.float64)
		db_cell_intensity = np.zeros(n_spots, dtype=np.float64) if db_masses is not None else None

		for start in range(0, n_spots, block_spectra):
			stop = min(start + block_spectra, n_spots)
			block_len = stop - start
			if n_peaks[start:stop].sum() == 0:
				continue
			mz = np.concatenate(mz_vectors[start:stop]).astype(np.float64, copy=False)
			intensity = np.concatenate(intensity_vectors[start:stop]).astype(np.float64, copy=False)
			spot = np.repeat(np.arange(block_len), n_peaks[start:stop])

			tic[start:stop] = np.bincount(spot, weights=intensity, minlength=block_len)

			positive = mz > 0.0
			is_background = np.zeros(mz.size, dtype=bool)
			bins = np.floor(np.log(mz[positive]) / log_bin_width).astype(np.int64)
			if background_bins.size > 0:
				idx = np.clip(np.searchsorted(background_bins, bins), 0, background_bins.size - 1)
				is_background[positive] = background_bins[idx] == bins
			is_cell = positive & ~is_background

			cell_intensity[start:stop] = np.bincount(spot[is_cell], weights=intensity[is_cell], minlength=block_len)

			if db_cell_intensity is not None:
				cell_mz = mz[is_cell]
				idx = np.searchsorted(db_masses, cell_mz)
				left = db_masses[np.clip(idx - 1, 0, db_masses.size - 1)]
				right = db_masses[np.clip(idx, 0, db_masses.size - 1)]
				within = (np.abs(cell_mz - left) <= left * mass_tolerance * 1e-6) | \
						 (np.abs(cell_mz - right) <= right * mass_tolerance * 1e-6)
				db_cell_intensity[start:stop] = np.bincount(
					spot[is_cell][within], weights=intensity[is_cell][within], minlength=block_len)

			del mz, intensity, spot, positive, is_background, is_cell, bins

		cell_fraction = np.divide(cell_intensity, tic, out=np.zeros(n_spots, dtype=np.float64), where=tic > 0)
		# TIC itself is left out: on a microgrid it is dominated by matrix ions, and ion suppression can
		# make it lower on cells than on the surrounding background.
		features: list[np.ndarray] = [np.log1p(np.clip(cell_intensity, 0.0, None)), cell_fraction]
		if db_cell_intensity is not None:
			features.append(np.log1p(np.clip(db_cell_intensity, 0.0, None)))

		# ------------------------------------------------------------------
		# 3. Local background correction and robust z-scores
		# ------------------------------------------------------------------
		window = self._MICROGRID_BACKGROUND_WINDOW
		pixel_coords = self._metadata[ion_mode].get(MsiMetadata.PIXEL_COORDINATES)  # (N, 2): (y, x)
		grid_yx = None
		if pixel_coords is not None and len(pixel_coords) == n_spots:
			grid_yx = np.asarray(pixel_coords, dtype=np.int64)
			grid_yx = grid_yx - grid_yx.min(axis=0)
			grid_shape = tuple(int(v) for v in grid_yx.max(axis=0) + 1)
		spatial = grid_yx is not None and min(grid_shape) >= window

		def robust_z(values: np.ndarray) -> np.ndarray | None:
			center = np.median(values)
			scale = 1.4826 * np.median(np.abs(values - center))
			if scale <= 1e-12:
				scale = values.std()
			if scale <= 1e-12:
				return None
			return (values - center) / scale

		valid_yx = grid_yx[valid] if grid_yx is not None else None
		z_sum = np.zeros(n_valid, dtype=np.float64)
		n_used = 0
		for feat in features:
			values = feat[valid]
			if spatial:
				raster = np.full(grid_shape, np.median(values), dtype=np.float64)
				raster[valid_yx[:, 0], valid_yx[:, 1]] = values
				local_background = median_filter(raster, size=window, mode="reflect")
				values = values - local_background[valid_yx[:, 0], valid_yx[:, 1]]
			z = robust_z(values)
			if z is not None:
				z_sum += z
				n_used += 1

		if n_used == 0:
			get_reporter().detail(f"Tissue detection (microgrid): constant features -- no cell spots detected among {n_spots} spots.")
			return []
		score = robust_z(z_sum / n_used)
		if score is None:
			get_reporter().detail(f"Tissue detection (microgrid): constant score -- no cell spots detected among {n_spots} spots.")
			return []

		# ------------------------------------------------------------------
		# 4. Hysteresis threshold
		# ------------------------------------------------------------------
		seed_z = max(self._MICROGRID_SEED_Z, float(normal_distribution.isf(self._MICROGRID_SEED_ALPHA / n_valid)))
		grow_z = min(self._MICROGRID_GROW_Z, seed_z)
		valid_idx = np.flatnonzero(valid)
		seeds = score >= seed_z

		if grid_yx is not None:
			grow_raster = np.zeros(grid_shape, dtype=bool)
			grow_raster[valid_yx[:, 0], valid_yx[:, 1]] = score >= grow_z
			components, _ = label_components(grow_raster, structure=np.ones((3, 3), dtype=bool))
			spot_components = components[valid_yx[:, 0], valid_yx[:, 1]]
			seed_components = np.unique(spot_components[seeds])
			seed_components = seed_components[seed_components > 0]
			cell = np.isin(spot_components, seed_components)
			component_sizes = np.bincount(spot_components[cell])[seed_components] if seed_components.size else np.array([], dtype=np.int64)
		else:
			cell = seeds
			component_sizes = np.ones(int(seeds.sum()), dtype=np.int64)

		kept = valid_idx[cell]
		n_components = component_sizes.size
		n_large = int((component_sizes > self._MICROGRID_MAX_COMPONENT_SPOTS).sum())
		size_info = f"sizes={int(component_sizes.min())}-{int(component_sizes.max())}" if n_components else "sizes=n/a"
		get_reporter().detail(
			f"Tissue detection (microgrid): kept {kept.size} / {n_spots} spots in {n_components} cell(s) "
			f"(seed_z={seed_z:.2f}, grow_z={grow_z:.2f}, {size_info}, background_ions={n_background_ions}, "
			f"local_background={'on' if spatial else 'off'}, n_features={len(features)}, ion_mode={ion_mode})"
		)
		if n_large:
			get_reporter().warning(
				f"Tissue detection (microgrid): {n_large} component(s) exceed "
				f"{self._MICROGRID_MAX_COMPONENT_SPOTS} spots; check for debris or matrix crystals."
			)
		if kept.size == 0:
			get_reporter().detail("Tissue detection (microgrid): no cell spots detected; every spot will be treated as foreground.")
		return kept.tolist()

	def load_payload(self, mass_tolerance: int | None = None, annotation_db: pd.DataFrame | None = None,
					 detect_background: bool = False) -> tuple[dict[list[list[np.float32 | np.float64]]]]:
		'''
		Load the M/Z vectors and the corresponding intensities from the binary IBD files.
		If the sample contains both ion modes, load the data for both modes.
		Before loading, each datapoint is evaluated against the lipid annotation database to filter spots
		that do not contain at least min_annotated_lipids_per_spot annotated lipids. If the database or the minimum number is not provided, the filter is skipped.

		If the recalibration reference and mass_tolerance are set, the M/Z values are recalibrated with
		one per-spectrum model per ion mode (see estimate_recalibration), estimated on first use from all
		spectra and applied to both the raw and the filtered vectors. Otherwise, the raw M/Z values are
		loaded.

		Parameters
		----------
		annotation_db: pd.DataFrame
			Database containing the annotated ionized_masses and the ion_mode
		mass_tolerance : int
			Mass tollerance in ppm to match the M/Z values against the lipid annotation database.
			Must be provided if annotation_db is provided.
		detect_background : bool
			If True, detect background spots and exclude them from the loaded data.

		Returns
		----------

		mz_vectors:	dict[list[list[np.float32]]]
			A dictionary with the M/Z vectors for each ion mode.
			Each ion mode contains a list of M/Z vectors, one for each spectrum. Each M/Z vector is a list of variable length.
		intensity_vectors:	dict[list[list[np.float32]]]
			A dictionary with the intensity vectors for each ion mode.
		'''

		mz_vectors = {}
		intensity_vectors = {}
		filtered_mz, filtered_intensity = {}, {}

		if annotation_db is not None and not isinstance(annotation_db, pd.DataFrame):
			raise ValueError("If provided, annotation_db must be a pd.DataFrame")
		if annotation_db is not None and mass_tolerance is None:
			raise ValueError("Mass tolerance must be provided if annotation_db is provided")

		# For each ion mode
		for mode in self._metadata.keys():
			# Define an utility to read the M/Z values and the intensityfrom the binary file
			read_mzs = lambda count, offset: np.fromfile(self._binary_files[mode],
														 dtype=self._metadata[mode][MsiMetadata.MZ_DTYPE], count=count,
														 offset=offset)
			read_intensities = lambda count, offset: np.fromfile(self._binary_files[mode], dtype=self._metadata[mode][
				MsiMetadata.INTENSITIES_DTYPE], count=count, offset=offset)

			# Read the M/Z values for each spectrum
			mz_vectors[mode] = [read_mzs(
				self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA][i, 0],
				self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA][i, 2]
			) for i in range(self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA].shape[0])]

			# Read the intensity values for each spectrum
			intensity_vectors[mode] = [read_intensities(
				self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA][i, 0],
				self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA][i, 2]
			) for i in range(self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA].shape[0])]

		if annotation_db is not None:
			if self.filtered_idx is None:
				keep_indices: set[int] = set()

				for mode in self._metadata.keys():
					if detect_background:
						keep_indices.update(self._detect_tissue_spots(
							mz_vectors=mz_vectors[mode],
							intensity_vectors=intensity_vectors[mode],
							ion_mode=mode,
							database=annotation_db,
							mass_tolerance=mass_tolerance,
						))
					else:
						# Keep all the datapoints
						keep_indices.update(range(len(mz_vectors[mode])))

				# Save the filtered indices
				self.filtered_idx = sorted(list(keep_indices))

		# Recalibrate every spectrum with one model per ion mode. The model is estimated once from all
		# spectra and reused, so the backbone (built from the filtered spectra) and the matrix (built
		# from all spectra) see exactly the same correction.
		if self.recalibration_reference is not None and mass_tolerance is not None:
			for mode in self._metadata.keys():
				reference = self.recalibration_reference.get(mode)
				if reference is None or reference.size == 0:
					continue
				if mode not in self.recalibration_model:
					self.recalibration_model[mode] = estimate_recalibration(
						mz_list=mz_vectors[mode],
						intensity_list=intensity_vectors[mode],
						pixel_coordinates=self._metadata[mode][MsiMetadata.PIXEL_COORDINATES],
						line_axis=self._metadata[mode].get(MsiMetadata.LINE_AXIS, 0),
						reference_mz=reference,
						mass_tolerance=mass_tolerance,
						min_intensity=self.min_intensity_threshold or 0.0,
					)
					get_reporter().detail(f"Recalibration ({str(mode).upper()}): "
										  f"{describe_recalibration(self.recalibration_model[mode])}")
				mz_vectors[mode] = self.recalibration_model[mode].apply(mz_vectors[mode])

		# Always populate filtered vectors: use filtered_idx when available, otherwise keep all spectra
		for mode in self._metadata.keys():
			indices = self.filtered_idx if self.filtered_idx is not None else range(len(mz_vectors[mode]))
			filtered_mz[mode] = [mz_vectors[mode][i] for i in indices]
			filtered_intensity[mode] = [intensity_vectors[mode][i] for i in indices]

		return mz_vectors, intensity_vectors, filtered_mz, filtered_intensity


@njit(cache=True)
def crowded_peaks(flat_mz, flat_intensity, offsets, window_ppm, intensity_ratio):
	"""
	Flag peaks that share their spectrum with a competitor: another peak within `window_ppm` ppm
	whose intensity is at least `intensity_ratio` times their own.

	Returns a bool array aligned with `flat_mz`.
	"""
	crowded = np.zeros(flat_mz.size, dtype=np.bool_)
	for s in range(offsets.size - 1):
		start = offsets[s]
		end = offsets[s + 1]
		n = end - start
		if n < 2:
			continue
		order = np.argsort(flat_mz[start:end]) + start
		low = 0
		high = 0
		for k in range(n):
			peak = order[k]
			mz = flat_mz[peak]
			half_width = mz * window_ppm * 1e-6
			while flat_mz[order[low]] < mz - half_width:
				low += 1
			while high < n and flat_mz[order[high]] <= mz + half_width:
				high += 1
			threshold = intensity_ratio * flat_intensity[peak]
			for q in range(low, high):
				if q != k and flat_intensity[order[q]] >= threshold:
					crowded[peak] = True
					break
	return crowded


class _CalibrationReferenceSelector:
	'''
	Deterministic, streaming selection of the reference M/Z values used for m/z recalibration.

	Every spectrum of every sample contributes -- there is no subsampling and no RNG, so repeated
	runs on the same input return byte-identical references.

	Usage: construct with the ppm tolerance, feed one sample's payload at a time with
	:meth:`add_sample` (the payload can be released immediately afterwards), then call
	:meth:`select`.

	How it stays cheap
	------------------
	Each sample is reduced on arrival to an m/z *occurrence histogram* on a fixed logarithmic
	grid, and only the histogram survives. Peak memory therefore never scales with the number of
	spectra: what is retained per sample is one int32 array of occupied bin indices, whose length
	is bounded by the sample's mass span divided by the bin width (a few hundred thousand entries
	at the default 10 ppm), not by its peak count. This is what makes the pass safe on the
	datasets that previously exhausted RAM while holding every sample's raw peaks at once.

	Why a logarithmic grid
	----------------------
	For small relative differences ln(m2) - ln(m1) = ln(1 + (m2 - m1) / m1) ~= (m2 - m1) / m1, so a
	constant ppm tolerance is a constant width in log space. A single uniform grid therefore
	resolves the tolerance identically across the whole mass range, with no per-mass bookkeeping.
	Bins are deliberately narrower than the tolerance (see ``_BIN_SUBDIVISION``), so two peaks
	within tolerance land at most that many bins apart; the ppm grouping in :meth:`select` then
	re-merges them across bin boundaries. The grid only ever *indexes* peaks -- the mass reported
	for a bin is the exact mean of the raw m/z that fell in it, never a bin centre.

	Isolation
	---------
	Alongside each bin's occurrence count the histogram counts *crowded* occurrences: peaks that
	share their spectrum with a competitor (another peak of at least ``_ISOLATION_INTENSITY_RATIO``
	of their intensity within the calibrant search window, see :func:`crowded_peaks`). A calibrant
	whose occurrences are often crowded can be confused with its neighbour during matching, so
	:meth:`select` rejects it. The test is per spectrum on purpose: m/z drift spreads one calibrant
	over neighbouring masses across spectra, but never puts two copies of it in one spectrum.
	'''

	# Bin width as a fraction of the ppm tolerance. 2 => bins are half a tolerance wide.
	_BIN_SUBDIVISION = 2
	# Spectra concatenated per reduction block. Bounds the transient arrays independently of how
	# many spectra a sample has.
	_BLOCK_SPECTRA = 20_000
	# A competitor counts when its intensity is at least this fraction of the peak's own.
	_ISOLATION_INTENSITY_RATIO = 0.1
	# A candidate is isolated when at most this fraction of its occurrences is crowded.
	_ISOLATION_MAX_CROWDED = 0.1
	# Size of the candidate pool, as a multiple of the number of references requested.
	_POOL_FACTOR = 20
	# Pool members need at least this fraction of the best candidate's score, so the spread step
	# cannot fill an empty m/z segment with a rare (noise) peak.
	_POOL_MIN_RELATIVE_SCORE = 0.1

	def __init__(self, mass_tolerance: int) -> None:
		'''
		Parameters
		----------
		mass_tolerance : int
			Mass tolerance in ppm, used both for the grid width and for the final ppm grouping.
		'''
		self._mass_tolerance = mass_tolerance
		self._log_bin_width = (mass_tolerance * 1e-6) / self._BIN_SUBDIVISION
		self._search_ppm = recalibration_search_ppm(mass_tolerance)

		# Dataset-wide histogram per ion mode, plus the bins each contributing sample occupies.
		# Only samples that actually have a mode are tracked, so coverage is later measured against
		# the samples that could contribute -- mixed single-/dual-mode datasets score correctly.
		self._bins: dict[MsiIonMode, np.ndarray] = {}
		self._counts: dict[MsiIonMode, np.ndarray] = {}
		self._mz_sums: dict[MsiIonMode, np.ndarray] = {}
		self._crowded: dict[MsiIonMode, np.ndarray] = {}
		self._sample_bins: dict[MsiIonMode, list[np.ndarray]] = {}

	def add_sample(self, sample_mz: dict[MsiIonMode, list[np.ndarray]],
				   sample_intensity: dict[MsiIonMode, list[np.ndarray]]) -> None:
		'''
		Fold one sample's M/Z payload into the running histogram.

		The payload is only read, never retained: the caller is free to release it as soon as this
		returns.

		Parameters
		----------
		sample_mz : dict[MsiIonMode, list[np.ndarray]]
			One M/Z array per spectrum, per ion mode present in this sample.
		sample_intensity : dict[MsiIonMode, list[np.ndarray]]
			The matching intensity arrays, used by the isolation statistic.
		'''

		for mode, spectra in sample_mz.items():
			bins, counts, mz_sums, crowded = self._reduce_spectra(
				spectra, sample_intensity[mode], self._log_bin_width, self._BLOCK_SPECTRA,
				self._search_ppm, self._ISOLATION_INTENSITY_RATIO)
			if bins.size == 0:
				continue

			if mode not in self._bins:
				self._bins[mode], self._counts[mode] = bins, counts
				self._mz_sums[mode], self._crowded[mode] = mz_sums, crowded
				self._sample_bins[mode] = [bins]
				continue

			# Merge into the running histogram, then drop this sample's counts: only its
			# occupied-bin index set is kept, for the distinct-sample coverage statistic.
			union = np.union1d(self._bins[mode], bins)
			previous = np.searchsorted(union, self._bins[mode])
			current = np.searchsorted(union, bins)
			merged = []
			for running, new, dtype in ((self._counts[mode], counts, np.int64),
										(self._mz_sums[mode], mz_sums, np.float64),
										(self._crowded[mode], crowded, np.int64)):
				total = np.zeros(union.size, dtype=dtype)
				total[previous] = running
				total[current] += new
				merged.append(total)

			self._bins[mode] = union
			self._counts[mode], self._mz_sums[mode], self._crowded[mode] = merged
			self._sample_bins[mode].append(bins)

	def select(self, number_of_references: int,
			   required_mz: dict[MsiIonMode, np.ndarray] | None = None) -> dict[MsiIonMode, np.ndarray]:
		'''
		Select the reference M/Z values from everything added so far.

		Per ion mode the merged histogram bins are grouped within ``mass_tolerance`` ppm by the
		same weighted sliding window used to build the m/z backbone (:func:`cluster_unique_mz_labels`).
		Every group then gets statistics pooled over the calibrant search window around it
		(±``recalibration_search_ppm``): occurrences, crowded occurrences, mass sum and sample
		coverage. Pooling over the window gathers the copies of one calibrant that drift has spread
		over several neighbouring groups. A group is a candidate when it is isolated (see the class
		docstring) and is scored by

		    score = windowed occurrences * (samples covered by the window / samples having this ion mode)

		Selection, in order:

		1. `required_mz` (e.g. sprayed internal standards): the group nearest each mass within the
		   search window, regardless of isolation.
		2. A pool of at most ``_POOL_FACTOR * number_of_references`` candidates scoring at least
		   ``_POOL_MIN_RELATIVE_SCORE`` of the best candidate, taken greedily in descending score; a
		   group closer than twice the search window to an already chosen one is skipped, so no two
		   references can ever match the same peak.
		3. Spread: the pool's log-m/z span is cut into `number_of_references` equal segments and
		   the best pool member of each segment is taken.
		4. Fill from the pool by score until `number_of_references` are chosen.
		5. Coverage: while a sample has no chosen reference, add the best group (isolated first)
		   that covers it.

		Ties are broken by ascending m/z, giving a total order. Each reference is reported at the
		windowed mean mass of its group, i.e. the mean of every occurrence within the search window.
		These means carry the dataset's average drift. When required masses (known standards) are
		observed, the references are moved onto the standards' mass scale: every reference m is
		divided by (1 + d(m) * 1e-6), where d is the ppm offset of the observed standards from their
		given masses. d is a least-squares line in m when at least two standards span
		``_RECAL_SLOPE_MIN_SPAN`` of the reference m/z span, and their median offset (a constant)
		otherwise.

		Parameters
		----------
		number_of_references : int
			Minimum number of references per ion mode. More are added when needed for coverage.
		required_mz : dict[MsiIonMode, np.ndarray] | None
			Masses that must be references when observed.

		Returns
		-------
		dict[MsiIonMode, np.ndarray]
			Ascending reference M/Z values per ion mode; an empty array for a mode that no sample
			contributed.
		'''

		recalibration_reference: dict[MsiIonMode, np.ndarray] = {}
		window = self._search_ppm * 1e-6

		for mode in MsiIonMode.list():
			if mode not in self._bins or self._bins[mode].size == 0:
				recalibration_reference[mode] = np.array([], dtype=np.float64)
				continue

			bins = self._bins[mode]
			counts = self._counts[mode].astype(np.float64)
			mz_sums = self._mz_sums[mode]

			# Exact mean mass of each bin. Bin indices ascend and ln is monotone, so these ascend
			# too, which is what the sliding-window grouping requires.
			bin_mz = mz_sums / counts

			labels = cluster_unique_mz_labels(bin_mz, counts, self._mass_tolerance)
			n_groups = int(labels[-1]) + 1

			group_weight = np.bincount(labels, weights=counts, minlength=n_groups)
			group_mz_sum = np.bincount(labels, weights=mz_sums, minlength=n_groups)
			group_crowded = np.bincount(labels, weights=self._crowded[mode].astype(np.float64), minlength=n_groups)
			group_mz = group_mz_sum / group_weight

			# Distinct-sample presence per group. Counting per bin and summing would double-count a
			# sample whose measurement jitter spreads one peak over several bins of the same group,
			# so presence is collapsed to (sample, group) pairs first.
			rows = self._sample_bins[mode]
			n_samples = len(rows)
			present = np.zeros((n_samples, n_groups), dtype=bool)
			for sample_idx, row in enumerate(rows):
				present[sample_idx, labels[np.searchsorted(bins, row)]] = True

			# Statistics pooled over the search window around each group (prefix sums).
			low = np.searchsorted(group_mz, group_mz * (1 - window), side='left')
			high = np.searchsorted(group_mz, group_mz * (1 + window), side='right')
			def windowed(values: np.ndarray) -> np.ndarray:
				prefix = np.concatenate(([0.0], np.cumsum(values)))
				return prefix[high] - prefix[low]

			window_weight = windowed(group_weight)
			window_mz = windowed(group_mz_sum) / window_weight
			isolated = windowed(group_crowded) <= self._ISOLATION_MAX_CROWDED * window_weight
			prefix_present = np.zeros((n_samples, n_groups + 1), dtype=np.int32)
			np.cumsum(present, axis=1, out=prefix_present[:, 1:])
			window_present = (prefix_present[:, high] - prefix_present[:, low]) > 0
			del prefix_present

			scores = window_weight * (window_present.sum(axis=0) / n_samples)
			order = np.lexsort((group_mz, -scores))

			selected: list[int] = []

			def separated(group: int, chosen: list[int]) -> bool:
				return all(abs(group_mz[group] - group_mz[c]) > 2 * window * group_mz[c] for c in chosen)

			# 1. Required masses.
			anchors: list[tuple[int, float]] = []
			if required_mz is not None and mode in required_mz:
				for mass in np.sort(np.asarray(required_mz[mode], dtype=np.float64)):
					nearest = int(np.argmin(np.abs(group_mz - mass)))
					if abs(group_mz[nearest] - mass) > window * mass:
						get_reporter().warning(f"Internal standard m/z {mass:.4f} ({str(mode).upper()}) is not observed; "
											   f"it is not used as a recalibration reference")
					elif separated(nearest, selected):
						selected.append(nearest)
						anchors.append((nearest, float(mass)))

			# 2. Candidate pool.
			pool: list[int] = []
			best_score = scores[isolated].max() if isolated.any() else 0.0
			for group in order:
				if len(pool) >= self._POOL_FACTOR * number_of_references or scores[group] < self._POOL_MIN_RELATIVE_SCORE * best_score:
					break
				if isolated[group] and separated(group, selected + pool):
					pool.append(int(group))

			# 3. Spread over the pool's log-m/z span.
			if pool:
				log_mz = np.log(group_mz[pool])
				edges = np.linspace(log_mz.min(), log_mz.max(), number_of_references + 1)
				segment = np.clip(np.searchsorted(edges, log_mz, side='right') - 1, 0, number_of_references - 1)
				for s in range(number_of_references):
					members = [group for group, seg in zip(pool, segment) if seg == s]
					if members and separated(members[0], selected):
						selected.append(members[0])

			# 4. Fill from the pool by score.
			for group in pool:
				if len(selected) >= number_of_references:
					break
				if group not in selected and separated(group, selected):
					selected.append(group)

			# 5. Coverage: isolated groups first, then any group.
			covered = window_present[:, selected].any(axis=1) if selected else np.zeros(n_samples, dtype=bool)
			for require_isolation in (True, False):
				for group in order:
					if covered.all():
						break
					if require_isolation and not isolated[group]:
						continue
					if group in selected or not (window_present[:, group] & ~covered).any() or not separated(group, selected):
						continue
					selected.append(int(group))
					covered |= window_present[:, group]

			references = window_mz[np.array(selected, dtype=np.intp)]
			if anchors:
				masses = np.array([mass for _, mass in anchors])
				offsets = np.array([(window_mz[group] - mass) / mass * 1e6 for group, mass in anchors])
				span = references.max() - references.min()
				if len(anchors) >= 2 and span > 0 and np.ptp(masses) >= _RECAL_SLOPE_MIN_SPAN * span:
					slope, intercept = np.polyfit(masses, offsets, 1)
					shift_ppm = intercept + slope * references
					description = f"{shift_ppm.min():+.1f} to {shift_ppm.max():+.1f} ppm over the references"
				else:
					shift_ppm = float(np.median(offsets))
					description = f"{shift_ppm:+.1f} ppm"
				references = references / (1 + shift_ppm * 1e-6)
				get_reporter().detail(f"Recalibration references ({str(mode).upper()}) anchored to "
									  f"{len(anchors)} internal standard(s): {description}")
			recalibration_reference[mode] = np.sort(references)

		return recalibration_reference

	@staticmethod
	def _reduce_spectra(spectra: list[np.ndarray], intensities: list[np.ndarray], log_bin_width: float,
						block_spectra: int, search_ppm: float,
						intensity_ratio: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
		'''
		Reduce one sample + ion mode to a compact m/z occurrence histogram.

		Spectra are consumed in blocks, and the dense accumulator spans only the masses this
		sample actually contains (it is grown on demand), so peak memory is bounded by the block
		size plus the sample's own mass span -- never by its spectrum count. Only occupied bins are
		returned. Binning uses ``np.bincount``, which is a single linear pass with no sort.

		Parameters
		----------
		spectra : list[np.ndarray]
			One M/Z array per spectrum.
		intensities : list[np.ndarray]
			One intensity array per spectrum.
		log_bin_width : float
			Bin width in natural-log units.
		block_spectra : int
			Number of spectra concatenated at a time.
		search_ppm : float
			Calibrant search half-width in ppm, for the crowded-peak statistic.
		intensity_ratio : float
			Relative intensity a competing peak needs to make a peak crowded.

		Returns
		-------
		bins : np.ndarray[np.int32]
			Ascending occupied bin indices, ``floor(ln(m) / log_bin_width)``.
		counts : np.ndarray[np.int64]
			Peak occurrences per bin.
		mz_sums : np.ndarray[np.float64]
			Sum of the raw m/z values per bin; divide by `counts` for the bin's exact mean mass.
		crowded : np.ndarray[np.int64]
			Crowded occurrences per bin (see :func:`crowded_peaks`).
		'''

		empty = (np.array([], dtype=np.int32),
				 np.array([], dtype=np.int64),
				 np.array([], dtype=np.float64),
				 np.array([], dtype=np.int64))
		if not spectra:
			return empty

		bins_lo = 0
		counts = None
		mz_sums = None
		crowded_counts = None

		for start in range(0, len(spectra), block_spectra):
			block_mz, block_intensity, offsets = _flatten_spectra(
				spectra[start:start + block_spectra], intensities[start:start + block_spectra])
			block_crowded = crowded_peaks(block_mz, block_intensity, offsets, search_ppm, intensity_ratio)
			# ln is undefined at or below zero, and such m/z are invalid for MSI anyway.
			positive = block_mz > 0.0
			block_mz, block_crowded = block_mz[positive], block_crowded[positive]
			if block_mz.size == 0:
				continue

			bin_idx = np.floor(np.log(block_mz) / log_bin_width).astype(np.int64)
			block_lo, block_hi = int(bin_idx.min()), int(bin_idx.max())

			if counts is None:
				bins_lo = block_lo
				counts = np.zeros(block_hi - block_lo + 1, dtype=np.int64)
				mz_sums = np.zeros(block_hi - block_lo + 1, dtype=np.float64)
				crowded_counts = np.zeros(block_hi - block_lo + 1, dtype=np.int64)
			elif block_lo < bins_lo or block_hi > bins_lo + counts.size - 1:
				# Grow the accumulator to span the widened mass range, preserving alignment.
				new_lo = min(bins_lo, block_lo)
				new_size = max(bins_lo + counts.size - 1, block_hi) - new_lo + 1
				offset = bins_lo - new_lo
				grown = []
				for values in (counts, mz_sums, crowded_counts):
					larger = np.zeros(new_size, dtype=values.dtype)
					larger[offset:offset + values.size] = values
					grown.append(larger)
				counts, mz_sums, crowded_counts = grown
				bins_lo = new_lo

			bin_idx -= bins_lo
			counts += np.bincount(bin_idx, minlength=counts.size)
			mz_sums += np.bincount(bin_idx, weights=block_mz, minlength=mz_sums.size)
			crowded_counts += np.bincount(bin_idx, weights=block_crowded, minlength=crowded_counts.size).astype(np.int64)
			del block_mz, block_intensity, block_crowded, bin_idx

		if counts is None:
			return empty

		occupied = np.flatnonzero(counts)
		return ((occupied + bins_lo).astype(np.int32), counts[occupied], mz_sums[occupied],
				crowded_counts[occupied])


class MsiDataset(BaseDataset):

	_LEIDEN_RESOLUTION = 0.5
	_H5AD_COMPRESSION = "gzip"
	_H5AD_INTERMEDIATE_COMPRESSION = "gzip"  # per-sample files; matches project-wide convention

	def __init__(self, path: str, samples: list[MsiSample], lipid_annotation_db: str | None = None) -> None:
		'''
		MSI dataset containing multiple samples. This class provide utilities to preprocess the raw experiments
		and generate an aligned and corrected AnnData object.

		Parameters
		----------
		path : str
			The root path where the samples are stored.
		samples : list[MsiSample]
			List of MsiSample objects.
		lipid_annotation_db : str | None
			Path to the lipid annotation database. If None, no lipid annotation will be performed.
			The file can be a CSV or a JSON and it must contain three columns: db_name, ionized_mass, ion_mode.
		'''

		super().__init__(path, samples)
		self._lipid_annotation_db_path = lipid_annotation_db

		if not all(isinstance(sample, MsiSample) for sample in samples):
			raise TypeError('Invalid input type. Expected list of MsiSample objects.')

		self.reference_mz: dict[MsiIonMode, np.ndarray] = {}
		self.interpolated: dict[str, dict[MsiIonMode, np.ndarray]] = {}
		self.normalized: dict[str, dict[MsiIonMode, np.ndarray]] = {}
		self.foreground_masks: dict[str, np.ndarray] = {}

		# If the lipid DB is provided, check if it exists and we can read it
		if lipid_annotation_db is not None:
			if not os.path.exists(lipid_annotation_db):
				raise FileNotFoundError(f"Lipid annotation database {lipid_annotation_db} does not exist.")
			if not os.access(lipid_annotation_db, os.R_OK):
				raise PermissionError(f"Lipid annotation database {lipid_annotation_db} is not readable.")

		# Based on the data type, load the lipid annotation database
		if lipid_annotation_db is not None:
			if lipid_annotation_db.endswith('.csv'):
				self.lipid_annotation_db = pd.read_csv(lipid_annotation_db)
			elif lipid_annotation_db.endswith('.json'):
				self.lipid_annotation_db = pd.read_json(lipid_annotation_db)
			else:
				raise ValueError("Invalid lipid annotation database format. Supported formats are CSV and JSON.")

			# Check that the required columns are present
			required_columns = ['db_name', 'ionized_mass', 'ion_mode']
			if not all(column in self.lipid_annotation_db.columns for column in required_columns):
				raise ValueError(f"Lipid annotation database must contain the following columns: {required_columns}")
		else:
			self.lipid_annotation_db = None

	def _calculate_chunks_for_consensus_estimation(self, unique_mz_len, item_size_bytes=8, safety_factor=1.5):
		"""
		Estimate number of chunks constrained by memory and CPU.

		Parameters
		----------
		unique_mz_len : int
			Total number of unique m/z values.
		item_size_bytes : int
			Approximate bytes per item in arrays (float64 ~ 8 bytes).
		safety_factor : float
			Factor to account for overhead and auxiliary arrays.

		Returns
		-------
		n_chunks : int
			Number of chunks limited by CPU cores and available memory.
		"""

		# Estimate memory needed for one chunk as a fraction of total data
		total_data_bytes = unique_mz_len * item_size_bytes

		# Get available RAM in bytes (use psutil)
		available_mem = psutil.virtual_memory().available

		# Estimate max chunk size in bytes based on memory and safety factor
		max_chunk_bytes = available_mem / safety_factor

		# Max chunks by memory constraint
		max_chunks_mem = int(np.ceil(total_data_bytes / max_chunk_bytes))

		# Number of logical CPU cores
		n_cores = utils.available_cpus() or 1

		# Choose number of chunks limited by cores and memory
		n_chunks = max(n_cores, max_chunks_mem)
		n_chunks = min(n_chunks, 32)  # Optional: cap max chunks to avoid overhead

		return n_chunks

	def _compute_reference_mz(self, spectra_list: list[np.ndarray], mass_tolerance: int,
							  frequency_threshold: float) -> np.ndarray:
		"""
		Create consensus reference m/z vector using adaptive mass tolerance with parallel CPU clustering.

		Parameters:
		-----------
		spectra_list : list of np.ndarray
			List of m/z arrays from different spectra.
		mass_tolerance : int
			Mass tolerance in ppm for grouping m/z values.
		frequency_threshold : float
			Minimum frequency threshold for m/z values to be included in the consensus.

		Returns:
		-----------
		consensus_mz : np.ndarray
			Consensus m/z values after grouping and filtering.
		"""

		# Guard: return empty array if there are no spectra or all spectra are empty
		if not spectra_list:
			return np.array([], dtype=np.float64)
		all_mz = np.concatenate(spectra_list).astype(np.float64)
		if all_mz.size == 0:
			return np.array([], dtype=np.float64)

		# Concatenate and round m/z values
		all_mz = np.round(all_mz, decimals=6)
		all_mz.sort()

		# Unique m/z and counts
		unique_mz, counts = np.unique(all_mz, return_counts=True)

		n = len(unique_mz)
		# Never more chunks than values, so no chunk is empty.
		n_chunks = min(self._calculate_chunks_for_consensus_estimation(n, item_size_bytes=8, safety_factor=2.0), n)

		chunk_size = n // n_chunks
		overlap = int(chunk_size * 0.05)  # 5% overlap dynamically scaled

		chunk_indices = []
		for i in range(n_chunks):
			start = max(0, i * chunk_size - overlap)
			# The last chunk runs to the end: n // n_chunks floors, and the remainder holds the
			# highest m/z values.
			end = n if i == n_chunks - 1 else min(n, (i + 1) * chunk_size + overlap)
			chunk_indices.append((start, end))

		# Prepare chunk arrays with overlap
		chunks = [(unique_mz[start:end], counts[start:end]) for start, end in chunk_indices]

		n_workers = utils.available_cpus() or 1
		executor = concurrent.futures.ProcessPoolExecutor(max_workers=n_workers)
		try:
			futures = [executor.submit(cluster_unique_mz_chunk, chunk[0], chunk[1], mass_tolerance) for chunk in chunks]
			results = [f.result() for f in futures]
		except Exception:
			executor.shutdown(wait=False, cancel_futures=True)
			raise
		else:
			executor.shutdown(wait=True)

		# Merge clusters across chunks
		merged_mz, merged_w = results[0]
		for i in range(1, len(results)):
			merged_mz, merged_w = merge_chunks(merged_mz, merged_w, results[i][0], results[i][1], mass_tolerance)

		# merge_chunks can leave adjacent centroids closer than mass_tolerance at chunk
		# boundaries / overlap regions. Re-run the weighted sliding-window consolidation
		# on the merged result so consecutive reference peaks are guaranteed >= tolerance.
		if merged_mz.size > 1:
			order = np.argsort(merged_mz)
			merged_mz, merged_w = cluster_unique_mz_chunk(merged_mz[order], merged_w[order], mass_tolerance)

		# Guard: return empty array if clustering produced no results
		if merged_w.size == 0:
			return np.array([], dtype=np.float64)

		# Filter by frequency threshold
		max_count = merged_w.max()
		threshold_count = max_count * frequency_threshold
		filtered_idx = merged_w >= threshold_count

		return merged_mz[filtered_idx]

	@staticmethod
	def _interpolate_intensities(original_mzs_list: list[np.ndarray], original_intensities_list: list[np.ndarray],
								 reference_mz: np.ndarray, mass_tolerance: float) -> np.ndarray:
		"""
		Rebin intensities to reference M/Z vector. This method processes a chunk of spectra.
		Each original peak adds its whole intensity to the nearest reference M/Z bin within the mass tolerance
		(see interpolate_single). If a peak does not fall within the mass tolerance of any reference bin, it is ignored.

		Parameters
		----------
		original_mzs_list : list of np.ndarray
			List of original M/Z arrays for each spectrum in the chunk.
		original_intensities_list : list of np.ndarray
			List of original intensity arrays for each spectrum in the chunk.
		reference_mz : np.ndarray
			The reference M/Z vector to interpolate to.
		mass_tolerance : float
			Mass tolerance in ppm for matching peaks to reference bins.

		Returns
		-------
		np.ndarray
			A 2D array of shape (n_datapoints, n_ref) containing the interpolated intensities.
		"""
		n_datapoints = len(original_mzs_list)
		n_ref = len(reference_mz)

		if n_datapoints == 0 or n_ref == 0:
			fallback_dtype = original_intensities_list[0].dtype if original_intensities_list else np.float32
			return np.zeros((n_datapoints, n_ref), dtype=fallback_dtype)

		out_dtype = original_intensities_list[0].dtype
		result_matrix = np.zeros((n_datapoints, n_ref), dtype=out_dtype)

		for idx, (original_mz, original_intensity) in enumerate(zip(original_mzs_list, original_intensities_list)):
			if original_mz.size == 0:
				continue

			mz = original_mz.astype(reference_mz.dtype, copy=False)
			intens = original_intensity

			if mz.size != intens.size:
				raise ValueError("mz and intensity must have same length")

			# Sort once (your existing logic)
			if not np.all(mz[:-1] <= mz[1:]):
				order = np.argsort(mz)
				mz = mz[order]
				intens = intens[order]

			# THE KEY CHANGE: Use searchsorted version
			result_matrix[idx] = interpolate_single(mz, intens, reference_mz, mass_tolerance)

		return result_matrix

	def _annotate_reference_mz(self, mz_vector: np.ndarray[np.float32], ion_mode: MsiIonMode,
							   mass_tolerance: int) -> np.ndarray:
		'''
		Annotate the reference M/Z vector using the lipid annotation database.

		Parameters
		----------
		mz_vector : np.ndarray[np.float32]
			The reference M/Z vector to annotate.
		ion_mode : MsiIonMode
			The ion mode of the M/Z vector.
		mass_tolerance : int
			The mass tolerance in ppm for matching the M/Z values.

		Returns
		-------
		pd.DataFrame
			A DataFrame containing the annotations for each M/Z value.
		'''

		if self.lipid_annotation_db is None:
			raise ValueError("Lipid annotation database is not provided.")

		annotations = []

		for mz in mz_vector:
			# Compute the mass tolerance in Da
			tolerance_da = mz * mass_tolerance / 1e6

			# Filter the lipid DB for matching entries
			matches = self.lipid_annotation_db[
				(self.lipid_annotation_db['ionized_mass'] >= mz - tolerance_da) &
				(self.lipid_annotation_db['ionized_mass'] <= mz + tolerance_da) &
				(self.lipid_annotation_db['ion_mode'] == ion_mode)
				]

			if matches.shape[0] > 0:
				annotation_str = '; '.join(matches['db_name'].tolist())
			else:
				annotation_str = 'Unannotated'

			annotations.append(annotation_str)

		return np.array(annotations, dtype=str)

	@staticmethod
	def _read_cached_sample(path: str) -> tuple[pd.DataFrame, list[float], dict[str, np.ndarray]] | None:
		'''
		Read .var (mz, mz_mode, lipid_annotation), .uns["spot_size"] and .uns["recalibration_reference"]
		from a per-sample h5ad without materializing X. Returns None if the file is missing or unreadable.
		'''
		try:
			adata = ad.read_h5ad(path, backed='r')
			try:
				var_subset = adata.var[['mz', 'mz_mode', 'lipid_annotation']].copy()
				spot_size = list(adata.uns['spot_size'])
				reference = {str(mode): np.asarray(masses, dtype=np.float64)
							 for mode, masses in adata.uns['recalibration_reference'].items()}
			finally:
				if adata.file is not None:
					adata.file.close()
			return var_subset, spot_size, reference
		except Exception:
			return None

	def _dataset_fingerprint(self, settings: dict) -> str:
		'''
		Fingerprint of the whole dataset run: method version, settings, lipid database and every
		sample's inputs (imzML SHA-256 and .ibd size; the .ibd itself is too large to hash). Every
		sample's output depends on all samples (shared recalibration references and m/z backbone), so
		the same key is stored in every per-sample file and in the merged file.
		'''
		inputs = {}
		for sample in sorted(self.samples, key=lambda s: s.sample_id):
			inputs[sample.sample_id] = {}
			for mode, mode_path in sorted(sample.input_paths.items()):
				imzml, ibd = find_imzml_pair(mode_path)
				inputs[sample.sample_id][str(mode)] = {"imzml_sha256": file_digest(imzml), "ibd_size": os.path.getsize(ibd)}
		database = self._lipid_annotation_db_path
		return fingerprint({
			"method": METHOD_VERSION,
			"modality": self.samples[0].modality_name if self.samples else None,
			"settings": settings,
			"lipid_annotation_db_sha256": file_digest(database) if database is not None else None,
			"inputs": inputs,
		})

	def process_dataset(self,
						mass_tolerance: int = 10,
						frequency_threshold: float = 0.01,
						intensity_normalization: str = MsiIntensityNormalization.NONE,
						recalibration_reference: dict[MsiIonMode, np.ndarray] | str | None = None,
						min_intensity_threshold: float = 10000.0,
						internal_standard_mz: dict[MsiIonMode, np.ndarray] | str | None = None,
						detect_background: bool = False,
						sample_type: str = MsiSampleType.TISSUE,
						resolution: str = SpotResolution.STANDARD,
						force_recomputing: bool = False,
						step_reporter=None
						) -> dict[str, str]:
		'''
		Process the dataset by aligning the M/Z values across all samples and interpolating the intensities.

		Parameters
		----------
		mass_tolerance : int
			Adaptive mass tolerance in ppm for grouping M/Z values.
		frequency_threshold : float
			Frequency threshold for filtering M/Z values.
		intensity_normalization : MsiIntensityNormalization
			Type of intensity normalization to apply, one of 'none', 'tic', 'log',
			'clr', 'tic_mean_scaled', 'median' or 'internal_standard'. All methods are applied
			independently per sample and ion mode. 'tic' divides each spectrum by its total ion
			current (rows sum to 1); 'tic_mean_scaled' rescales each spectrum to the mean total
			ion current over the sample's spots, preserving absolute intensity scale
			(equivalent to 'tic' times a per-sample constant; not comparable across samples);
			'median' divides each spectrum by the median of its nonzero features;
			'internal_standard' divides each spectrum by the summed intensity of the
			internal standard features (requires internal_standard_mz).
		recalibration_reference : dict[MsiIonMode, np.ndarray] | str | None
			Reference M/Z values for recalibration per ion mode (see _parse_mz_by_mode for the
			accepted forms). Selected from the dataset when None.
		min_intensity_threshold : float
			Minimum intensity threshold to consider a peak valid in the recalibration process.
		internal_standard_mz : dict[MsiIonMode, np.ndarray] | str | None
			M/Z values of the internal standards sprayed over the sections, per ion mode. They are
			always included in the automatically selected recalibration references, and are
			required by 'internal_standard' normalization.
		detect_background : bool
			If True, detects and stores the foreground mask for each sample. If False, the foreground mask covers all the spots.
			Only effective when a lipid annotation database is set.
		sample_type : str
			Background detection strategy. "tissue" fits a GMM + BIC model on spectral complexity features and applies
			morphological cleanup. "microgrid" detects sparse cells as local outliers against a background ion model
			(see MsiSample._detect_microgrid_spots).
		resolution : str
			SpotResolution preset for the alignment clustering: 'standard' (multicellular pixels)
			or 'sub_cellular' (weak high-resolution pixels: wider neighbourhood smoothing).
		force_recomputing : bool
			If True, forces recomputation of the reference M/Z vectors and interpolation even if they were already computed.
			If False, the computation is skipped if the merged dataset already exists.

		Returns
		-------
		processed_samples : dict[str, str]
			A dictionary mapping sample IDs to the paths of their processed files.
		'''

		# Check if the required normalization method is implemented
		if intensity_normalization not in MsiIntensityNormalization.list():
			raise ValueError(
				f'Invalid intensity normalization method. Expected one of {MsiIntensityNormalization.list()}.')

		# Check the input parameters
		if type(mass_tolerance) is not int or mass_tolerance <= 0:
			raise ValueError('mass_tolerance must be a positive integer representing ppm.')
		if type(frequency_threshold) is not float or frequency_threshold < 0.0 or frequency_threshold > 1.0:
			raise ValueError('frequency_threshold must be a float between 0.0 and 1.0.')
		if type(min_intensity_threshold) not in [int, float] or min_intensity_threshold < 0.0:
			raise ValueError('min_intensity_threshold must be a non-negative number.')
		recalibration_reference = _parse_mz_by_mode(recalibration_reference, MsiPreprocessingParams.RECALIBRATION_REFERENCE)
		internal_standard_mz = _parse_mz_by_mode(internal_standard_mz, MsiPreprocessingParams.INTERNAL_STANDARD_MZ)
		if intensity_normalization == MsiIntensityNormalization.INTERNAL_STANDARD:
			sample_modes = {mode for sample in self.samples for mode in sample.ion_modes}
			missing_modes = sorted(sample_modes - set(internal_standard_mz or {}))
			if missing_modes:
				raise ValueError(f"internal_standard normalization requires internal_standard_mz for ion mode(s) {missing_modes}.")

		processed_samples = {}
		reporter = step_reporter or get_reporter()

		as_lists = lambda by_mode: None if by_mode is None else {mode: masses.tolist() for mode, masses in sorted(by_mode.items())}
		dataset_key = self._dataset_fingerprint({
			"mass_tolerance": mass_tolerance, "frequency_threshold": frequency_threshold,
			"intensity_normalization": intensity_normalization,
			"recalibration_reference": as_lists(recalibration_reference),
			"internal_standard_mz": as_lists(internal_standard_mz),
			"min_intensity_threshold": float(min_intensity_threshold), "detect_background": detect_background,
			"sample_type": sample_type, "resolution": resolution,
		})

		# STEP 0: Check if the computation is complete and can be skipped
		if not force_recomputing:
			all_sample_computed = True

			# Check if all the samples have already been processed with the same inputs and settings
			for sample in self.samples:
				sample_path = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')
				if cached_fingerprint(sample_path) != dataset_key:
					all_sample_computed = False
					break
				processed_samples[sample.sample_id] = sample_path

			# Check if the merged dataset already exists and has the correct sample composition
			merged_path = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')
			if cached_fingerprint(merged_path) != dataset_key:
				all_sample_computed = False
			else:
				active_ids = {s.sample_id for s in self.samples}
				merged_ids = utils.read_merged_sample_ids(merged_path)
				if merged_ids != active_ids:
					all_sample_computed = False
				else:
					processed_samples["merged"] = merged_path

			# If the merged dataset already exists and force_recompute is False, skip the computation
			if all_sample_computed:
				for index, name in enumerate(_DATASET_STEPS, 1):
					reporter.step(index, len(_DATASET_STEPS), name, cached=True)
				return processed_samples

		# STEP 0b: Partial-resume cache discovery (an interrupted run with unchanged inputs and settings)
		# Walk self.samples in order looking for per-sample h5ad files carrying the current dataset
		# fingerprint. Take the first valid one as ground truth: its .var (mz/mz_mode/lipid_annotation)
		# IS the global reference and its .uns['recalibration_reference'] the recalibration references of
		# that run. Samples whose file is missing, stale OR whose var disagrees with the ground truth (and
		# all subsequent samples) are queued for reprocessing. We then skip steps 3-6 entirely (we have
		# the cached global reference and calibrants) and run steps 1-2, 7 only on the queued subset.
		# Step 8 always runs to rebuild the merged dataset.
		samples_to_process: list = list(self.samples)
		spot_sizes: dict[str, list[float]] = {}
		processed_samples = {}
		has_partial_cache = False

		if not force_recomputing:
			ground_truth_var: pd.DataFrame | None = None
			gt_mz = gt_mode = gt_ann = None
			invalid_from_index: int | None = None

			for i, sample in enumerate(self.samples):
				sample_path = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id,
													 sample.modality_name, 'h5ad')
				result = MsiDataset._read_cached_sample(sample_path) if cached_fingerprint(sample_path) == dataset_key else None
				if result is None:
					invalid_from_index = i
					break

				var_subset, spot_size, cached_reference = result
				this_mz = var_subset['mz'].to_numpy(dtype=np.float32)
				this_mode = var_subset['mz_mode'].astype(str).to_numpy()
				this_ann = var_subset['lipid_annotation'].astype(str).to_numpy()

				if ground_truth_var is None:
					ground_truth_var = var_subset
					gt_mz, gt_mode, gt_ann = this_mz, this_mode, this_ann
					recalibration_reference = cached_reference
				else:
					if (not np.array_equal(this_mz, gt_mz)
							or not np.array_equal(this_mode, gt_mode)
							or not np.array_equal(this_ann, gt_ann)):
						invalid_from_index = i
						break

				processed_samples[sample.sample_id] = sample_path
				spot_sizes[sample.sample_id] = spot_size

			has_partial_cache = ground_truth_var is not None

			if has_partial_cache:
				if invalid_from_index is None:
					samples_to_process = []
				else:
					samples_to_process = list(self.samples[invalid_from_index:])
					for s in samples_to_process:
						processed_samples.pop(s.sample_id, None)
						spot_sizes.pop(s.sample_id, None)

				# Reconstruct the global reference m/z and lipid annotations from the cached var
				mode_str_array = ground_truth_var['mz_mode'].astype(str).to_numpy()
				self.reference_mz = {}
				self.lipid_annotations = {}
				for mode_enum in [MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE]:
					mask = mode_str_array == str(mode_enum)
					if mask.any():
						self.reference_mz[mode_enum] = ground_truth_var.loc[mask, 'mz'].to_numpy(dtype=np.float32)
						self.lipid_annotations[mode_enum] = ground_truth_var.loc[mask, 'lipid_annotation'].astype(str).to_numpy()
					else:
						self.reference_mz[mode_enum] = np.array([], dtype=np.float32)
						self.lipid_annotations[mode_enum] = np.array([], dtype=object)

				reporter.detail(f"Partial resume: {len(processed_samples)} cached sample file(s) reused, "
								f"{len(samples_to_process)} sample(s) to process")

		iter_samples = samples_to_process

		reference_mz_samples: dict[MsiIonMode, list[np.float32]] = {MsiIonMode.POSITIVE: [], MsiIonMode.NEGATIVE: []}

		# STEP 1: Initialize each sample to load the metadata
		for sample in reporter.tqdm(iter_samples, *_step(1), unit="sample"):
			sample._step_reporter = reporter
			sample.initialize_sample()
			sample._sample_type = sample_type

		# STEP 2: Determine the recalibration reference M/Z values. If one is provided (or recovered
		# from a partial-resume cache) it is used directly; otherwise it is selected from the tissue
		# spectra of the dataset (see _CalibrationReferenceSelector), always including the internal
		# standards when configured. Recalibration runs with or without a lipid DB.
		if recalibration_reference is None:
			# Every spectrum contributes, but each sample is folded into a compact m/z histogram as
			# soon as it is loaded and its peaks are released immediately. Accumulating all samples'
			# raw spectra first is what used to be both slow and a source of OOM.
			selector = _CalibrationReferenceSelector(mass_tolerance)

			for sample in reporter.tqdm(iter_samples, *_step(2), unit="sample"):
				raw_mz, _, filtered_mz, filtered_intensity = sample.load_payload(annotation_db=self.lipid_annotation_db,
																				 mass_tolerance=mass_tolerance,
																				 detect_background=detect_background)

				# filtered_* holds the tissue spectra when background detection ran, all spectra otherwise
				selector.add_sample(filtered_mz, filtered_intensity)

				del raw_mz, filtered_mz, filtered_intensity  # Free memory
				gc.collect()

			# Compute the recalibration reference from the dataset
			reporter.step(*_step(3))
			recalibration_reference = selector.select(number_of_references=_RECALIBRATION_REFERENCES,
													  required_mz=internal_standard_mz)
			del selector  # Free memory
			gc.collect()
			found = [f"{len(masses)} ({str(mode).upper()})" for mode, masses in recalibration_reference.items() if len(masses)]
			if found:
				reporter.detail("Recalibration references: " + ", ".join(found))
			else:
				reporter.warning("No recalibration reference found; m/z values are not recalibrated")
		else:
			# Dummy call to still filter the datapoints in each sample
			for sample in reporter.tqdm(iter_samples, *_step(2), unit="sample"):
				_, _, _, _ = sample.load_payload(annotation_db=self.lipid_annotation_db, mass_tolerance=mass_tolerance,
												 detect_background=detect_background)
				gc.collect()

			reporter.step(*_step(3), cached=has_partial_cache)
			if not has_partial_cache:
				reporter.detail("Using the provided recalibration reference m/z values")

		# Store the recalibration reference in each sample to reprocess
		for sample in iter_samples:
			sample.recalibration_reference = recalibration_reference
			sample.min_intensity_threshold = min_intensity_threshold

		if not has_partial_cache:
			# STEP 3: For each ion mode in each sample, compute the reference M/Z vector
			for sample in reporter.tqdm(iter_samples, *_step(4), unit="sample"):
				raw_mz, _, filtered_mz, _ = sample.load_payload(annotation_db=self.lipid_annotation_db,
																mass_tolerance=mass_tolerance)
				for mode in sample.ion_modes:
					reference_mz_samples[mode].append(
						self._compute_reference_mz(
							filtered_mz[mode],
							mass_tolerance=mass_tolerance,
							frequency_threshold=frequency_threshold
						)
					)
				del raw_mz, filtered_mz  # Free memory
				gc.collect()

			# STEP 4: Compute the global reference M/Z vector for each ion mode. No frequency thresholding is applied here
			reporter.step(*_step(5))
			for mode in reference_mz_samples.keys():
				if len(reference_mz_samples[mode]) > 0:
					self.reference_mz[mode] = self._compute_reference_mz(
						reference_mz_samples[mode],
						mass_tolerance=mass_tolerance,
						frequency_threshold=0.0
					)
				else:
					self.reference_mz[mode] = np.array([], dtype=np.float32)

			del reference_mz_samples  # Free memory
			gc.collect()

			reporter.detail(
				f"Reference backbone: {len(self.reference_mz.get(MsiIonMode.POSITIVE, []))} m/z values (POSITIVE), "
				f"{len(self.reference_mz.get(MsiIonMode.NEGATIVE, []))} m/z values (NEGATIVE)")

			# STEP 5: For each ion mode, annotate the reference M/Z vector if a lipid annotation database is provided
			reporter.step(*_step(6))
			self.lipid_annotations: dict[MsiIonMode, np.ndarray] = {}
			if self.lipid_annotation_db is not None:
				for mode in self.reference_mz.keys():
					self.lipid_annotations[mode] = self._annotate_reference_mz(
						self.reference_mz[mode],
						mode,
						mass_tolerance=mass_tolerance
					)
		else:
			# The backbone and annotations come from the cached per-sample files.
			for index in (4, 5, 6):
				reporter.step(*_step(index), cached=True)

		# Build var metadata (shared across all samples); reads self.reference_mz and self.lipid_annotations,
		# which are either freshly computed above or pulled from the partial-resume cache.
		positive_cols = self.reference_mz[MsiIonMode.POSITIVE].shape[0] if MsiIonMode.POSITIVE in self.reference_mz else 0
		negative_cols = self.reference_mz[MsiIonMode.NEGATIVE].shape[0] if MsiIonMode.NEGATIVE in self.reference_mz else 0
		total_cols = positive_cols + negative_cols

		mz_parts, mode_parts, annotation_parts = [], [], []
		for mode in [MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE]:
			if mode not in self.reference_mz or self.reference_mz[mode].size == 0:
				continue
			mz_parts.append(self.reference_mz[mode])
			mode_parts.extend([mode] * self.reference_mz[mode].shape[0])
			if self.lipid_annotation_db is not None and mode in self.lipid_annotations:
				annotation_parts.append(self.lipid_annotations[mode])
			else:
				annotation_parts.append(np.array(['Unannotated'] * self.reference_mz[mode].shape[0]))

		merged_reference_mz = np.concatenate(mz_parts).astype(np.float32) if mz_parts else np.array([], dtype=np.float32)
		merged_mode_labels = mode_parts
		merged_annotations = np.concatenate(annotation_parts) if annotation_parts else np.array(['Unannotated'] * total_cols)

		var_df = pd.DataFrame({
			"mz": merged_reference_mz,
			"mz_mode": pd.Categorical(merged_mode_labels),
			"lipid_annotation": pd.Categorical(merged_annotations),
		}, index=pd.Index([str(i) for i in range(total_cols)], dtype=object))

		# Backbone columns of the internal standards, for 'internal_standard' normalization.
		standard_columns: dict[str, np.ndarray] = {}
		if intensity_normalization == MsiIntensityNormalization.INTERNAL_STANDARD:
			for mode, masses in internal_standard_mz.items():
				backbone = np.asarray(self.reference_mz.get(mode, np.array([])), dtype=np.float64)
				if backbone.size == 0:
					continue
				nearest = np.abs(backbone[None, :] - masses[:, None]).argmin(axis=1)
				distance_ppm = np.abs(backbone[nearest] - masses) / masses * 1e6
				if np.any(distance_ppm > mass_tolerance):
					absent = ", ".join(f"{m:.4f}" for m in masses[distance_ppm > mass_tolerance])
					raise ValueError(f"Internal standard m/z {absent} ({str(mode).upper()}) has no m/z backbone feature "
									 f"within {mass_tolerance} ppm; check internal_standard_mz.")
				standard_columns[mode] = np.unique(nearest)

		# Ion modes with features: each gets its recalibration QC columns in every sample, so all
		# per-sample files share one obs schema for the on-disk merge.
		qc_modes = [mode for mode in (MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE)
					if mode in self.reference_mz and self.reference_mz[mode].size > 0]
		stored_reference = {str(mode): np.asarray(masses, dtype=np.float64)
							for mode, masses in (recalibration_reference or {}).items()}

		# STEP 6+7: Process one sample at a time — interpolate, normalize, build AnnData, write, free.
		# This keeps peak RAM proportional to a single sample rather than all samples simultaneously.
		# spot_sizes was initialized in the partial-resume block above and may already contain entries
		# from cached samples; this loop only appends entries for samples in iter_samples.
		write_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
		write_futures = []

		try:
			for sample in reporter.tqdm(iter_samples, *_step(7), unit="sample"):
				sample_id = sample.sample_id
				ref_mode = MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in sample.ion_modes else MsiIonMode.NEGATIVE

				# Load raw spectra for this sample
				original_mzs, intensities, _, _ = sample.load_payload(annotation_db=self.lipid_annotation_db,
																	  mass_tolerance=mass_tolerance)
				self.foreground_masks[sample_id] = sample.foreground_mask

				# Interpolate and normalize each ion mode; keep local copies only
				interp_by_mode: dict = {}
				norm_by_mode: dict = {}

				for mode in sample.ion_modes:
					merged_intensities = np.zeros((len(intensities[mode]), len(self.reference_mz[mode])),
												  dtype=sample._metadata[mode][MsiMetadata.INTENSITIES_DTYPE])

					intensities_mode = intensities[mode]
					original_mzs_mode = original_mzs[mode]
					datapoints = len(intensities_mode)

					num_workers = min(utils.available_cpus() or 1, datapoints)
					chunk_size = (datapoints + num_workers - 1) // num_workers  # ceil division

					chunks = [
						(original_mzs_mode[start:end], intensities_mode[start:end])
						for start, end in
						[(i * chunk_size, min((i + 1) * chunk_size, datapoints)) for i in range(num_workers)]
					]

					# Reference via the class (not self) so joblib does not pickle the entire dataset.
					worker_func = partial(MsiDataset._interpolate_intensities,
										  reference_mz=self.reference_mz[mode],
										  mass_tolerance=mass_tolerance)

					results = Parallel(n_jobs=num_workers,
									   backend='loky',
									   verbose=0)(
						delayed(worker_func)(orig_chunk, intens_chunk)
						for orig_chunk, intens_chunk in chunks
					)

					current_idx = 0
					for chunk_result in results:
						chunk_len = chunk_result.shape[0]
						merged_intensities[current_idx:current_idx + chunk_len, :] = chunk_result
						current_idx += chunk_len

					# Store raw before normalization overwrites the reference
					interp_by_mode[mode] = merged_intensities
					merged_intensities = normalize_intensities(
						merged_intensities, intensity_normalization,
						pixel_coordinates=sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES],
						standard_columns=standard_columns.get(mode),
					)

					norm_by_mode[mode] = merged_intensities

				del intensities, original_mzs
				gc.collect()

				# Build combined pos+neg matrices for this sample
				physical_coords = sample._metadata[ref_mode][MsiMetadata.PHYSICAL_COORDINATES].astype(np.float32)
				raster_coords = sample._metadata[ref_mode][MsiMetadata.RASTER_COORDINATES]
				raster_size = sample._metadata[ref_mode][MsiMetadata.RASTER_SIZE]
				spot_size = np.array([float(raster_size[0]), float(raster_size[1])], dtype=np.float32)
				spot_sizes[sample_id] = spot_size.tolist()

				rows = interp_by_mode[ref_mode].shape[0]
				raw_matrix = np.zeros((rows, total_cols), dtype=np.float32)
				norm_matrix = np.zeros((rows, total_cols), dtype=np.float32)

				if MsiIonMode.POSITIVE in sample.ion_modes and positive_cols > 0:
					raw_matrix[:, :positive_cols] = interp_by_mode[MsiIonMode.POSITIVE].astype(np.float32)
					norm_matrix[:, :positive_cols] = norm_by_mode[MsiIonMode.POSITIVE].astype(np.float32)
				if MsiIonMode.NEGATIVE in sample.ion_modes and negative_cols > 0:
					raw_matrix[:, positive_cols:] = interp_by_mode[MsiIonMode.NEGATIVE].astype(np.float32)
					norm_matrix[:, positive_cols:] = norm_by_mode[MsiIonMode.NEGATIVE].astype(np.float32)

				del interp_by_mode, norm_by_mode
				gc.collect()

				X_sparse = sp.csr_matrix(norm_matrix)
				raw_sparse = sp.csr_matrix(raw_matrix)
				del raw_matrix, norm_matrix

				n_obs = rows
				obs_index = pd.Index([f"{sample_id}_{i}" for i in range(n_obs)], dtype=object)
				obs_df = pd.DataFrame({
					'sample_id': pd.Categorical([sample_id] * n_obs),
					'foreground': pd.Categorical(self.foreground_masks[sample_id]),
				}, index=obs_index)
				# Applied recalibration per spot: ppm correction at the mean reference m/z and the
				# number of references matched in the narrow pass.
				for mode in qc_modes:
					model = sample.recalibration_model.get(mode)
					obs_df[f'recalibration_ppm_{mode}'] = (model.offset_ppm.astype(np.float32) if model is not None
														   else np.full(n_obs, np.nan, dtype=np.float32))
					obs_df[f'recalibration_matches_{mode}'] = (model.matches if model is not None
															   else np.zeros(n_obs, dtype=np.int16))

				adata = ad.AnnData(
					X=X_sparse,
					layers={'raw': raw_sparse},
					obs=obs_df,
					obsm={
						'spatial': physical_coords,
						'raster_coordinates': raster_coords
					},
					var=var_df.copy(),
					uns={'spot_size': spot_size.tolist(),
						 'recalibration_reference': stored_reference,
						 'focus_preprocessing_fingerprint': dataset_key}
				)

				# Per-sample cluster labels for alignment colouring (unit aggregation + Leiden).
				# .X is already normalized here; large samples are coarsened into k x k blocks of the
				# acquisition raster (Voronoi supercells if no raster is recovered): the spots of a
				# unit are summed, then re-normalized internally so occupancy washes out. Only
				# obs['cluster'] is produced; the summed/normalized matrix and PCA / neighbour
				# intermediates are throwaway, never written to the AnnData.
				adata.obs['cluster'] = pd.Categorical(compute_cluster_labels(
					adata.X,
					leiden_resolution=self._LEIDEN_RESOLUTION,
					normalize_target_sum=None,
					coordinates=adata.obsm.get('spatial'),
					smoothing_rings=clustering_preset(resolution)['smoothing_rings'],
				))

				output_file = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')
				for f in write_futures:
					f.result()
				write_futures.clear()
				write_futures.append(write_executor.submit(utils.write_h5ad_compat, adata, output_file, compression=self._H5AD_INTERMEDIATE_COMPRESSION))
				processed_samples[sample.sample_id] = output_file

			for f in write_futures:
				f.result()
		finally:
			write_executor.shutdown(wait=True)
		gc.collect()

		# STEP 9: Merge all samples into a single dataset (memory-efficient on-disk concat)
		reporter.step(*_step(8))
		merged_file = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')

		if processed_samples:
			os.makedirs(os.path.dirname(merged_file), exist_ok=True)
			# Filter to only per-sample files (exclude any "merged" key)
			sample_files = {k: v for k, v in processed_samples.items() if k != "merged"}

			utils.concat_on_disk_compat(
				sample_files,
				merged_file,
				axis=0,
				join="inner",
				merge="same",
				uns_merge="first",
			)

			# Update the merged file's .uns["spot_size"] to be a per-sample dict
			merged_adata = ad.read_h5ad(merged_file)
			merged_adata.uns["spot_size"] = spot_sizes
			merged_adata.uns["recalibration_reference"] = stored_reference
			merged_adata.uns["focus_preprocessing_fingerprint"] = dataset_key
			utils.write_h5ad_compat(merged_adata, merged_file, compression=self._H5AD_COMPRESSION)
			del merged_adata
			gc.collect()

			processed_samples["merged"] = merged_file
		return processed_samples


# --- Modality Registration ---

def _create_msi_samples(path, sample_ids, modality_name, settings):
	samples = []
	for sample_id in sample_ids:
		modality_path = os.path.join(path, sample_id, modality_name)

		# The GUI scaffolds both pos/ and neg/ for every MSI modality, so directory presence says
		# nothing about which ion modes were acquired. Decide from the imzML/IBD pairs on disk and
		# treat an ion mode directory holding neither file kind as "not acquired".
		available_modes = [
			mode for mode in MsiIonMode.list()
			if find_imzml_pair(os.path.join(modality_path, mode)) is not None
		]

		if not available_modes:
			raise FileNotFoundError(
				f"No complete .imzML + .ibd file pair found for sample '{sample_id}' in "
				f"'{modality_path}'. Expected one in at least one of the ion mode "
				f"subdirectories: {MsiIonMode.list()}."
			)

		samples.append(
			MsiSample(
				source_path=path,
				sample_id=sample_id,
				modality_name=modality_name,
				double_ion_mode=len(available_modes) > 1,
				ion_mode=None if len(available_modes) > 1 else available_modes[0],
			)
		)
	return samples

def _create_msi_dataset(path, samples, settings):
	lipid_annotation_db = settings.get(MsiPreprocessingParams.LIPID_ANNOTATION_DB, None)
	return MsiDataset(path=path, samples=samples, lipid_annotation_db=lipid_annotation_db)

def _extract_msi_settings(settings):
	return {
		'mass_tolerance': settings.get(MsiPreprocessingParams.MASS_TOLERANCE, 10),
		'frequency_threshold': settings.get(MsiPreprocessingParams.FREQUENCY_THRESHOLD, 0.01),
		'intensity_normalization': settings.get(MsiPreprocessingParams.INTENSITY_NORMALIZATION, MsiIntensityNormalization.NONE),
		'recalibration_reference': _parse_mz_by_mode(settings.get(MsiPreprocessingParams.RECALIBRATION_REFERENCE, None),
													 MsiPreprocessingParams.RECALIBRATION_REFERENCE),
		'min_intensity_threshold': settings.get(MsiPreprocessingParams.MIN_INTENSITY_THRESHOLD, 1e4),
		'internal_standard_mz': _parse_mz_by_mode(settings.get(MsiPreprocessingParams.INTERNAL_STANDARD_MZ, None),
												  MsiPreprocessingParams.INTERNAL_STANDARD_MZ),
		'detect_background': settings.get(MsiPreprocessingParams.DETECT_BACKGROUND, False),
		'sample_type': settings.get(MsiPreprocessingParams.SAMPLE_TYPE, MsiSampleType.TISSUE),
		'resolution': settings.get(MsiPreprocessingParams.RESOLUTION, SpotResolution.STANDARD),
		'force_recomputing': settings.get(MsiPreprocessingParams.FORCE_RECOMPUTING, False),
	}

register_modality('msi', ModalityHandler(
	create_samples=_create_msi_samples,
	create_dataset=_create_msi_dataset,
	extract_settings=_extract_msi_settings,
))
