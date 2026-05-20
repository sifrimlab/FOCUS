import numpy as np
import scipy.sparse as sp
import mmap
import os, tqdm, psutil
from focus.preprocessing._utils import StepReporter
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
from scipy.ndimage import binary_fill_holes, binary_opening

import focus.utils as utils
from focus.constants import ImzMLFileParser, MsiIntensityNormalization, MsiSampleType, MsiMetadata, MsiIonMode, MsiPreprocessingParams
from focus.constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality


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
	"""Numba-optimized function to interpolate a single spectrum onto a reference M/Z grid using weighted averaging."""
	n_ref = len(reference_mz)
	result = np.zeros(n_ref, dtype=original_intensity.dtype)
	mz_tol = mass_tolerance * 1e-6

	if len(original_mz) == 0:
		return result

	# Single pass over peaks (no ref_batch_size needed)
	for i in range(len(original_mz)):
		mz_peak = original_mz[i]
		intens = original_intensity[i]
		mz_low = mz_peak * (1 - mz_tol)
		mz_high = mz_peak * (1 + mz_tol)

		left = np.searchsorted(reference_mz, mz_low)
		right = np.searchsorted(reference_mz, mz_high)

		if left < right:
			slice_mz = reference_mz[left:right]
			ppm_diff = np.abs(slice_mz - mz_peak) / mz_peak * 1e6
			weights_raw = 1.0 / (ppm_diff + 1e-9)
			weights_sum = np.sum(weights_raw)
			if weights_sum > 0:
				result[left:right] += intens * (weights_raw / weights_sum)

	return result


class MsiSample(BaseSample):

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
		ion_mode : MsiIonMode | None
			If double_ion_mode is False, specify the ion mode of the sample. If double_ion_mode is True, this should be None.
		'''

		# Ensure consistency of input parameters
		if not double_ion_mode and ion_mode not in MsiIonMode.list():
			raise ValueError(
				f'Invalid ion_mode value. Expected one of {MsiIonMode.list()} when double_ion_mode is False.')

		super().__init__(source_path, sample_id, modality_name)

		# If double ion mode is True, check that the input path contains both pos and neg subdirectories
		pos_path = os.path.join(source_path, sample_id, modality_name, 'pos')
		neg_path = os.path.join(source_path, sample_id, modality_name, 'neg')
		if double_ion_mode:
			if not os.path.exists(pos_path):
				raise FileNotFoundError(f"Positive ion mode path {pos_path} does not exist.")
			if not os.path.exists(neg_path):
				raise FileNotFoundError(f"Negative ion mode path {neg_path} does not exist.")
			self.input_paths = {MsiIonMode.POSITIVE: pos_path, MsiIonMode.NEGATIVE: neg_path}
		else:
			self.input_paths = {ion_mode: os.path.join(source_path, sample_id, modality_name, ion_mode)}

		self.double_ion_mode = double_ion_mode
		self.ion_mode = ion_mode

		self.recalibration_reference: dict[
										  MsiIonMode, np.ndarray] | None = None  # To be set during dataset preprocessing
		self.min_intensity_threshold: float | None = None  # To be set during dataset preprocessing
		self.raw_recalibration_offset: dict[MsiIonMode, np.ndarray] = {}  # To be computed during dataset preprocessing
		self.filtered_recalibration_offset: dict[
			MsiIonMode, np.ndarray] = {}  # To be computed during dataset preprocessing
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
		for scan_settings_list in root.find(ImzMLFileParser.SCAN_SETTINGS_LIST):
			for scan_settings in scan_settings_list:
				if scan_settings.attrib['name'] == "pixel size x":
					raster_size[0] = int(scan_settings.attrib['value'])
				if scan_settings.attrib['name'] == "pixel size y":
					raster_size[1] = int(scan_settings.attrib['value'])

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
		_reporter = getattr(self, '_step_reporter', None)
		if _reporter:
			_reporter.message(f"Verifying file integrity for sample {self.sample_id}")

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

			print(
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
			print(f"[filter_unpaired_spots] Applied pixel axis flip to NEGATIVE: flip_x={flip_x}, flip_y={flip_y}")
		else:
			print(f"[filter_unpaired_spots] No pixel axis flip needed")

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
		print(f"[filter_unpaired_spots] POSITIVE grid shape after intersection: {pos_shape_filtered}")
		print(f"[filter_unpaired_spots] NEGATIVE grid shape after intersection: {neg_shape_filtered}")

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
			# List the files in the given directory and extract the absolute path for the first imzML file
			files = os.listdir(input_path)
			imzml_files = [f for f in files if f.endswith('.imzML')]
			if len(imzml_files) == 0:
				raise FileNotFoundError(f"No imzML files found in {input_path}.")
			self._metadata_files[mode] = os.path.join(input_path, imzml_files[0])

			# Obtain the IBD file using the same filename and swapping the extension
			self._binary_files[mode] = self._metadata_files[mode].replace('.imzML', '.ibd')
			if not os.path.exists(self._binary_files[mode]):
				raise FileNotFoundError(f"IBD file {self._binary_files[mode]} not found in {input_path}.")

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
				_reporter = getattr(self, '_step_reporter', None)
				msg = (
					f"[Sample '{self.sample_id}' – {str(mode).upper()} mode] "
					f"Filtered {len(corrupt_indices):,} corrupted datapoint(s) "
					f"({len(corrupt_indices) / n_total * 100:.1f}% of {n_total:,} spectra)"
				)
				if _reporter:
					_reporter.message(msg)
				else:
					print(msg)

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
		  "microgrid" -- isolated single cells in a grid pattern. Spatial cleanup is
					   disabled (it would fill gaps and erase real cells). Otsu is used with
					   a 25th-percentile floor to avoid discarding weak single-cell signals.

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
		# 4. Threshold: Otsu (microgrid) or GMM + BIC model selection (tissue)
		# ------------------------------------------------------------------
		valid_scores = score[valid]
		lo, hi = valid_scores.min(), valid_scores.max()

		tissue_mask = np.zeros(n_spots, dtype=bool)

		if self._sample_type == MsiSampleType.MICROGRID:
			# Microgrid: mostly background with isolated single cells.  Otsu with a
			# 25th-percentile floor ensures at most 75 % of spots are discarded,
			# protecting weak single-cell signals.
			if hi - lo < 1e-12:
				print(f"Tissue detection (microgrid): unimodal score distribution -- treating all {n_spots} spots as foreground.")
				return np.where(valid)[0].tolist()

			scaled = ((valid_scores - lo) / (hi - lo) * 255).astype(np.uint8)
			hist, _ = np.histogram(scaled, bins=256, range=(0, 255))
			hist = hist.astype(np.float64)
			total = hist.sum()
			sum_total = np.dot(np.arange(256, dtype=np.float64), hist)
			sum_bg, w_bg, max_between_var, best_t = 0.0, 0.0, 0.0, 0
			for t in range(256):
				w_bg += hist[t]
				if w_bg == 0:
					continue
				w_fg = total - w_bg
				if w_fg == 0:
					break
				sum_bg += t * hist[t]
				mu_bg = sum_bg / w_bg
				mu_fg = (sum_total - sum_bg) / w_fg
				between_var = (w_bg / total) * (w_fg / total) * (mu_bg - mu_fg) ** 2
				if between_var > max_between_var:
					max_between_var = between_var
					best_t = t
			otsu_threshold = best_t / 255.0 * (hi - lo) + lo
			floor_threshold = float(np.percentile(valid_scores, 25))
			effective_threshold = min(otsu_threshold, floor_threshold)
			tissue_mask[valid] = score[valid] >= effective_threshold
			_log_info = f"otsu_thr={otsu_threshold:.4f}, floor_thr={floor_threshold:.4f}, effective_thr={effective_threshold:.4f}"

		else:
			# Tissue section: use GMM + BIC to decide between unimodal (all tissue)
			# and bimodal (tissue + background).  This handles imbalanced distributions
			# (e.g. 95 % tissue / 5 % background) where Otsu tends to split the
			# dominant tissue mode rather than separating it from background.
			n_valid = valid_scores.shape[0]
			if n_valid < 4 or hi - lo < 1e-12:
				print(f"Tissue detection (tissue): degenerate score distribution -- treating all {n_spots} spots as foreground.")
				return np.where(valid)[0].tolist()

			vs2d = valid_scores.reshape(-1, 1)
			gmm1 = GaussianMixture(n_components=1, random_state=0).fit(vs2d)
			gmm2 = GaussianMixture(n_components=2, random_state=0, n_init=3).fit(vs2d)
			bic1, bic2 = gmm1.bic(vs2d), gmm2.bic(vs2d)

			if bic1 <= bic2:
				# BIC prefers unimodal model -- keep all valid spots as tissue
				print(
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
		print(
			f"Tissue detection ({self._sample_type}): kept {kept.size} / {n_spots} spots "
			f"({_log_info}, n_features={len(features)}, ion_mode={ion_mode})"
		)
		return kept.tolist()

	def _recalibrate_mz_vector(
			self,
			mz_vector: list[np.ndarray],
			intensity_vector: list[np.ndarray],
			coordinates: np.ndarray,
			reference_mz: np.ndarray,
			mass_tolerance: int,
			ion_mode: MsiIonMode,
			recalibration_offset: dict[MsiIonMode, np.ndarray]
	) -> tuple[list[np.ndarray], dict[MsiIonMode, np.ndarray]]:
		'''
		Recalibrate each element of the M/Z vector list by applying a per-row offset computed using the reference M/Z peaks.
		To compute the offset, the highest peak within the mass tolerance for each reference M/Z in each spectrum is used.
		The offset is computed for each row of the matrix (mean of the row-wise differences between the observed and reference M/Z values).

		Parameters
		----------
		mz_vector : list[np.ndarray]
			List of M/Z vectors to recalibrate.
		intensity_vector : list[np.ndarray]
			List of intensity vectors corresponding to the M/Z vectors.
		coordinates : np.ndarray
			Pixel coordinates for the ion mode.
		reference_mz : np.ndarray
			Reference M/Z values to use for recalibration.
		mass_tolerance : int
			Mass tolerance in ppm to match the M/Z values against the reference M/Z values.
		ion_mode: MsiIonMode
			Ion mode of the sample.
		recalibration_offset : dict[MsiIonMode, np.ndarray] | None
			Precomputed recalibration offsets for each ion mode. If provided, these offsets are used instead of computing them.

		Returns
		-------
		recalibrated_mz_vector : list[np.ndarray]
			List of recalibrated M/Z vectors.
		recalibration_offset : dict[MsiIonMode, np.ndarray]
			Dictionary containing the recalibration offsets for each ion mode.
		'''

		# Get the size of the M/Z matrix
		size_x = np.max(coordinates[:, 0])
		size_y = np.max(coordinates[:, 1])

		if ion_mode not in recalibration_offset:
			# Prepare a matrix to store the offsets computed for each spectrum. Shape (X, Y, n_ref_mz)
			offsets = np.full((size_x, size_y, len(reference_mz)), np.nan, dtype=np.float32)

			# Iterate over each spectrum to compute the offsets
			for mz, intensity, pixel_coord in zip(mz_vector, intensity_vector, coordinates):
				x, y = pixel_coord
				mz_arr = np.asarray(mz, dtype=float).ravel()
				intensity_arr = np.asarray(intensity, dtype=float).ravel()

				# For each reference M/Z, find the closest peak within the mass tolerance
				for j, ref_mz in enumerate(reference_mz):
					tol = ref_mz * mass_tolerance * 1e-6

					# Find peaks within tolerance
					mask = np.abs(mz_arr - ref_mz) <= tol
					if np.any(mask):
						# Filter the peaks within tolerance with intensity lower than the minimum threshold (if set)
						if self.min_intensity_threshold is not None:
							min_intensity = self.min_intensity_threshold
							intensity_mask = intensity_arr >= min_intensity
							mask = np.logical_and(mask, intensity_mask)
							if not np.any(mask):
								continue

						# Get the index of the highest peak within the tolerance
						peak_indices = np.where(mask)[0]
						peak_intensities = intensity_arr[peak_indices]
						max_peak_idx = peak_indices[np.argmax(peak_intensities)]

						# Compute the offset
						offsets[x - 1, y - 1, j] = mz_arr[max_peak_idx] - ref_mz

			# Compute the mean offset for each row. Ingore RuntimeWarnings for rows with all NaN values
			with np.errstate(invalid="ignore"):
				recalibration_offset[ion_mode] = np.nanmean(offsets, axis=(1, 2))  # Shape (X,)

		# Apply the offsets to each M/Z vector
		recalibrated_mz_vector = []
		for mz, pixel_coord in zip(mz_vector, coordinates):
			x, y = pixel_coord
			offset = recalibration_offset[ion_mode][x - 1]
			if np.isnan(offset):
				recalibrated_mz_vector.append(mz)
			else:
				recalibrated_mz_vector.append(mz - offset)

		return recalibrated_mz_vector, recalibration_offset

	def load_payload(self, mass_tolerance: int | None = None, annotation_db: pd.DataFrame | None = None,
					 detect_background: bool = False) -> tuple[dict[list[list[np.float32 | np.float64]]]]:
		'''
		Load the M/Z vectors and the corresponding intensities from the binary IBD files.
		If the sample contains both ion modes, load the data for both modes.
		Before loading, each datapoint is evaluated against the lipid annotation database to filter spots
		that do not contain at least min_annotated_lipids_per_spot annotated lipids. If the database or the minimum number is not provided, the filter is skipped.

		If the recalibration reference has been set, the M/Z values are recalibrated accordingly.
		Otherwise, the raw M/Z values are loaded.

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

		# Always populate filtered vectors: use filtered_idx when available, otherwise keep all spectra
		for mode in self._metadata.keys():
			indices = self.filtered_idx if self.filtered_idx is not None else range(len(mz_vectors[mode]))
			filtered_mz[mode] = [mz_vectors[mode][i] for i in indices]
			filtered_intensity[mode] = [intensity_vectors[mode][i] for i in indices]

		# Apply recalibration if configured (requires mass_tolerance, only available when annotation_db is provided)
		if annotation_db is not None:
			for mode in self._metadata.keys():
				# If the recalibration reference is set, apply it to the M/Z vectors
				if self.recalibration_reference is not None and mode in self.recalibration_reference:
					# Apply the recalibration to both raw and filtered M/Z vectors. It must be done separately to keep the correct coordinates
					mz_vectors[mode], self.raw_recalibration_offset = self._recalibrate_mz_vector(
						mz_vector=mz_vectors[mode],
						intensity_vector=intensity_vectors[mode],
						coordinates=self._metadata[mode][MsiMetadata.PIXEL_COORDINATES],
						reference_mz=self.recalibration_reference[mode],
						mass_tolerance=mass_tolerance,
						ion_mode=mode,
						recalibration_offset=self.raw_recalibration_offset
					)

					filtered_mz[mode], self.filtered_recalibration_offset = self._recalibrate_mz_vector(
						mz_vector=filtered_mz[mode],
						intensity_vector=filtered_intensity[mode],
						coordinates=self._metadata[mode][MsiMetadata.PIXEL_COORDINATES][self.filtered_idx],
						reference_mz=self.recalibration_reference[mode],
						mass_tolerance=mass_tolerance,
						ion_mode=mode,
						recalibration_offset=self.filtered_recalibration_offset
					)

		return mz_vectors, intensity_vectors, filtered_mz, filtered_intensity


class MsiDataset(BaseDataset):

	_LEIDEN_RESOLUTION = 0.5
	_H5AD_COMPRESSION = "lzf"
	_H5AD_INTERMEDIATE_COMPRESSION = None  # No compression for per-sample files (fastest I/O for concat_on_disk)

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
			The file can be a CSV or a JSON and it must contain three columns: name, ionized_mass, ion_mode.
		'''

		super().__init__(path, samples)

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
		n_chunks = self._calculate_chunks_for_consensus_estimation(n, item_size_bytes=8, safety_factor=2.0)

		chunk_size = n // n_chunks
		overlap = int(chunk_size * 0.05)  # 5% overlap dynamically scaled

		chunk_indices = []
		for i in range(n_chunks):
			start = max(0, i * chunk_size - overlap)
			end = min(n, (i + 1) * chunk_size + overlap)
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
		The interpolation distributes the intensity of each original peak to the reference M/Z bins that fall within the mass tolerance,
		using inverse distance weighting.
		If a peak does not fall within the mass tolerance of any reference bin, it is ignored.

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
			return np.zeros((n_datapoints, n_ref), dtype=original_intensities_list[0].dtype)

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

	def _find_calibration_reference(self, mz_vectors: list[dict[MsiIonMode, list[np.ndarray]]], mass_tolerance: int,
									number_of_references: int = 1) -> dict[MsiIonMode, np.ndarray]:
		'''
		Scan the dataset to select the M/Z values to use as reference for recalibration.
		Each ion mode is processed separately. For each ion mode, the M/Z values from all samples are concatenated,
		and the top 'number_of_references' most frequent M/Z values are selected as reference.
		To reduce memory usage, only a random subset (30%) of the M/Z values from each sample is used.

		Parameters
		----------
		mz_vectors : list[dict[MsiIonMode, list[np.ndarray]]]
			List of M/Z vectors for each sample and ion mode.
		mass_tolerance : int
			Mass tolerance in ppm for grouping M/Z values.
		number_of_references : int
			Number of reference M/Z values to select for each ion mode.

		Returns
		-------
		dict[MsiIonMode, np.ndarray]
			A dictionary mapping each ion mode to the selected reference M/Z values.
		'''

		# Per-mode per-sample rounded mz
		per_sample_mz: dict[MsiIonMode, list[np.ndarray]] = {
			MsiIonMode.POSITIVE: [],
			MsiIonMode.NEGATIVE: [],
		}

		rng = np.random.default_rng()

		# Downsample each sample and collect per-sample mz per mode. Evaluate only 30% of the spectra to reduce memory usage
		for sample_mz in mz_vectors:
			for mode, spectra in sample_mz.items():
				n = len(spectra)
				if n == 0:
					continue
				subset_idx = rng.choice(n, size=max(1, n * 3 // 10), replace=False)
				sample_mode_mz = np.concatenate([spectra[i] for i in subset_idx]).astype(np.float64)
				sample_mode_mz = np.round(sample_mode_mz, decimals=6)
				per_sample_mz[mode].append(sample_mode_mz)

		recalibration_reference: dict[MsiIonMode, np.ndarray] = {}

		# Process each ion mode independently
		for mode, mode_samples in per_sample_mz.items():
			if len(mode_samples) == 0:
				recalibration_reference[mode] = np.array([], dtype=np.float64)
				continue

			# Global concatenation for frequency
			concatenated = np.concatenate(mode_samples)
			unique_mz, global_counts = np.unique(concatenated, return_counts=True)

			# Compute sample coverage per unique m/z
			mz_to_samples = defaultdict(set)
			for s_idx, mz_arr in enumerate(mode_samples):
				u = np.unique(mz_arr)
				pos = np.searchsorted(unique_mz, u)
				valid = (pos < len(unique_mz)) & (unique_mz[pos] == u)
				for p in pos[valid]:
					mz_to_samples[int(p)].add(s_idx)

			sample_coverage = np.zeros_like(global_counts, dtype=np.int32)
			for idx, samples in mz_to_samples.items():
				sample_coverage[idx] = len(samples)

			n_samples = len(mode_samples)
			coverage_fraction = sample_coverage / max(1, n_samples)

			# Score the candidates combining global counts and coverage
			scores = global_counts * (coverage_fraction ** 1.0)  # Alpha

			# Sort candidates by score
			sorted_idx = np.argsort(-scores)
			sorted_mz = unique_mz[sorted_idx]

			# Greedy selection ensuring coverage
			selected = []
			covered = np.zeros(n_samples, dtype=bool)

			# Helper to update coverage given a candidate m/z
			def update_coverage(candidate_mz: float):
				tol_da = candidate_mz * mass_tolerance * 1e-6
				newly_covered = np.zeros_like(covered)
				for s_idx, mz_arr in enumerate(mode_samples):
					if covered[s_idx]:
						continue
					if np.any((mz_arr >= candidate_mz - tol_da) & (mz_arr <= candidate_mz + tol_da)):
						newly_covered[s_idx] = True
				covered[newly_covered] = True
				return np.any(newly_covered)

			# First pass: pick top-scoring candidates until we reach the desired number
			# or all samples are covered
			for mz_cand in sorted_mz:
				if len(selected) >= number_of_references and covered.all():
					break
				if mz_cand in selected:
					continue
				selected.append(mz_cand)
				update_coverage(mz_cand)

			# Second pass: if some samples are still uncovered, add extra candidates
			if not covered.all():
				for mz_cand in sorted_mz:
					if covered.all():
						break
					if mz_cand in selected:
						continue
					if update_coverage(mz_cand):
						selected.append(mz_cand)

			recalibration_reference[mode] = np.array(selected, dtype=np.float64)

		return recalibration_reference

	def process_dataset(self,
						mass_tolerance: int = 10,
						frequency_threshold: float = 0.01,
						intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.TIC,
						recalibration_reference: dict[MsiIonMode, np.ndarray] | None = None,
						min_intensity_threshold: float = 10000.0,
						detect_background: bool = True,
						sample_type: str = MsiSampleType.TISSUE,
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
			Type of intensity normalization to apply.
		recalibration_reference : dict[MsiIonMode, np.ndarray] | None
			Reference M/Z vectors for recalibration per ion mode.
		min_intensity_threshold : float
			Minimum intensity threshold to consider a peak valid in the recalibration process.
		detect_background : bool
			If True, detects and stores the foreground mask for each sample. If False, the foreground mask covers all the spots.
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
		if recalibration_reference is not None and not isinstance(recalibration_reference, dict):
			raise ValueError('recalibration_reference must be a dictionary mapping MsiIonMode to np.ndarray or None.')
		if recalibration_reference is not None:
			for mode, ref in recalibration_reference.items():
				if mode not in MsiIonMode:
					raise ValueError(
						f'Invalid ion mode in recalibration_reference: {mode}. Expected one of {list(MsiIonMode)}.')
				if not isinstance(ref, np.ndarray):
					raise ValueError(f'Reference M/Z vector for mode {mode} must be a numpy array.')

		processed_samples = {}

		# STEP 0: Check if the computation is complete and can be skipped
		if not force_recomputing:
			all_sample_computed = True

			# Check if all the samples have already been processed
			for sample in self.samples:
				if not os.path.exists(
						MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name,
											   'h5ad')):
					all_sample_computed = False
					break
				else:
					processed_samples[sample.sample_id] = MODALITY_PREPROCESSING(self.dataset_source_path,
																				 sample.sample_id, sample.modality_name,
																				 'h5ad')

			# Check if the merged dataset already exists and has the correct sample composition
			merged_path = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')
			if not os.path.exists(merged_path):
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
				print("All samples have already been processed and merged dataset exists. Using cached results.")
				return processed_samples

		reporter = step_reporter or StepReporter()
		print("Processing Lipidomic Dataset")
		processed_samples = {}
		reference_mz_samples: dict[MsiIonMode, list[np.float32]] = {MsiIonMode.POSITIVE: [], MsiIonMode.NEGATIVE: []}

		# STEP 1: Initialize each sample to load the metadata
		for sample in reporter.tqdm(self.samples, desc="1/9 - Loading MSI data", unit="sample"):
			sample._step_reporter = reporter
			sample.initialize_sample()
			sample._sample_type = sample_type

		# STEP 2: Compute the recalibration offsets for each sample. If a reference is provided, it is used directly.
		# Otherwise, if the annotation DB is provided, the reference is computed from the annotated lipids (5 annotated lipids with the highest intensity).
		# If the DB is not provided and the recalibration reference is not provided, no recalibration is performed.
		if recalibration_reference is None:
			mz_vectors_all_samples = []

			for sample in reporter.tqdm(self.samples, desc="2/9 - Selecting high-confidence tissue spots", unit="sample"):
				raw_mz, _, filtered_mz, _ = sample.load_payload(annotation_db=self.lipid_annotation_db,
																mass_tolerance=mass_tolerance,
																detect_background=detect_background)

				# If it was possible to filter the datapoints using the lipid annotation DB, use the filtered M/Z values
				if filtered_mz != {}:
					mz_vectors_all_samples.append(filtered_mz)
				else:
					mz_vectors_all_samples.append(raw_mz)

				del raw_mz, filtered_mz  # Free memory
				gc.collect()

			# Compute the recalibration reference from the dataset
			reporter.step(
				"3/9 - Selecting recalibration reference M/Z values from the dataset and filtering background datapoints")
			recalibration_reference = self._find_calibration_reference(
				mz_vectors=mz_vectors_all_samples,
				mass_tolerance=mass_tolerance,
				number_of_references=5
			)
			del mz_vectors_all_samples  # Free memory
			gc.collect()
		else:
			# Dummy call to still filter the datapoints in each sample
			for sample in reporter.tqdm(self.samples, desc="2/8 - Selecting high-confidence tissue spots", unit="sample"):
				_, _, _, _ = sample.load_payload(annotation_db=self.lipid_annotation_db, mass_tolerance=mass_tolerance,
												 detect_background=detect_background)
				gc.collect()

			reporter.step("3/9 - Using provided recalibration reference M/Z values.")

		# Store the recalibration reference in each sample
		for sample in self.samples:
			sample.recalibration_reference = recalibration_reference
			sample.min_intensity_threshold = min_intensity_threshold

		# STEP 3: For each ion mode in each sample, compute the reference M/Z vector
		for sample in reporter.tqdm(self.samples, desc="4/9 - Computing reference M/Z backbone for each sample",
									unit="sample"):
			raw_mz, _, filtered_mz, _ = sample.load_payload(annotation_db=self.lipid_annotation_db,
															mass_tolerance=mass_tolerance)
			for mode in sample.ion_modes:
				reference_mz_samples[mode].append(
					self._compute_reference_mz(
						filtered_mz[mode] if filtered_mz != {} else raw_mz[mode],
						mass_tolerance=mass_tolerance,
						frequency_threshold=frequency_threshold
					)
				)
			del raw_mz, filtered_mz  # Free memory
			gc.collect()

		# STEP 4: Compute the global reference M/Z vector for each ion mode. No frequency thresholding is applied here
		reporter.step("5/9 - Computing global reference M/Z backbone for the dataset")
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

		print(
			f"Selected {len(self.reference_mz.get(MsiIonMode.POSITIVE, []))} M/Z values for POSITIVE mode and {len(self.reference_mz.get(MsiIonMode.NEGATIVE, []))} M/Z values for NEGATIVE mode as reference backbone.")

		# STEP 5: For each ion mode, annotate the reference M/Z vector if a lipid annotation database is provided
		reporter.step("6/9 - Annotating features (backbone M/Z values) for each ion mode")
		self.lipid_annotations: dict[MsiIonMode, np.ndarray] = {}
		if self.lipid_annotation_db is not None:
			for mode in self.reference_mz.keys():
				self.lipid_annotations[mode] = self._annotate_reference_mz(
					self.reference_mz[mode],
					mode,
					mass_tolerance=mass_tolerance
				)

		# STEP 6: Now that the global reference M/Z vectors are computed, process each sample to interpolate the intensities
		for sample in reporter.tqdm(self.samples, desc="7/9 - Aligning intensities to reference M/Z", unit="sample"):
			self.interpolated[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}
			self.normalized[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}

			# Load the intensities and M/Z values
			original_mzs, intensities, _, _ = sample.load_payload(annotation_db=self.lipid_annotation_db,
																  mass_tolerance=mass_tolerance)

			# All the spots are interpolated but an additional obs mask is stored to separate foreground/background
			self.foreground_masks[sample.sample_id] = sample.foreground_mask

			# Process each ion mode separately
			for mode in sample.ion_modes:
				merged_intensities = np.zeros((len(intensities[mode]), len(self.reference_mz[mode])),
											  dtype=sample._metadata[mode][MsiMetadata.INTENSITIES_DTYPE])

				# Consider only the datapoints for the current ion mode
				intensities_mode = intensities[mode]
				original_mzs_mode = original_mzs[mode]
				datapoints = len(intensities_mode)

				# Determine chunk size for each worker
				num_workers = min(utils.available_cpus() or 1, datapoints)
				chunk_size = (datapoints + num_workers - 1) // num_workers  # ceil division

				# Split data into contiguous chunks: lists of arrays
				chunks = [
					(original_mzs_mode[start:end], intensities_mode[start:end])
					for start, end in
					[(i * chunk_size, min((i + 1) * chunk_size, datapoints)) for i in range(num_workers)]
				]

				# Worker function which partially fixes reference_mz and mass_tolerance.
				# Reference via the class (not self) so joblib does not pickle the entire dataset.
				worker_func = partial(MsiDataset._interpolate_intensities,
									  reference_mz=self.reference_mz[mode],
									  mass_tolerance=mass_tolerance)

				# Joblib parallel execution - auto-manages processes
				results = Parallel(n_jobs=num_workers,
								   backend='loky',  # Robust process management
								   verbose=0)(
					delayed(worker_func)(orig_chunk, intens_chunk)
					for orig_chunk, intens_chunk in chunks
				)

				# Concatenate results in correct order into final array
				current_idx = 0
				for chunk_result in results:
					chunk_len = chunk_result.shape[0]
					merged_intensities[current_idx:current_idx + chunk_len, :] = chunk_result
					current_idx += chunk_len

				self.interpolated[sample.sample_id][mode] = merged_intensities

				# Intensity normalization
				if intensity_normalization == MsiIntensityNormalization.TIC:
					# Total Ion Count normalization
					tic = merged_intensities.sum(axis=1, keepdims=True)
					tic[tic == 0] = 1  # Prevent division by zero
					merged_intensities = merged_intensities / tic
				elif intensity_normalization == MsiIntensityNormalization.LOG:
					# Log-Transform normalization
					merged_intensities = np.log1p(merged_intensities)
				elif intensity_normalization == MsiIntensityNormalization.CLR:
					# Centered Log-Ratio normalization
					nonzero_vals = merged_intensities[merged_intensities > 0]
					delta = nonzero_vals.min() / 2.0 if nonzero_vals.size > 0 else 1.0
					x = merged_intensities.copy()
					x[x == 0] = delta
					log_x = np.log(x)
					merged_intensities = log_x - log_x.mean(axis=1, keepdims=True)

				self.normalized[sample.sample_id][mode] = merged_intensities

			del intensities, original_mzs  # Free memory
			gc.collect()

		# Pre-build var metadata (shared across all samples)
		positive_cols = self.reference_mz[MsiIonMode.POSITIVE].shape[0] if MsiIonMode.POSITIVE in self.reference_mz else 0
		negative_cols = self.reference_mz[MsiIonMode.NEGATIVE].shape[0] if MsiIonMode.NEGATIVE in self.reference_mz else 0
		total_cols = positive_cols + negative_cols

		# Reference mz, mode labels, and annotations (shared var metadata)
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

		merged_reference_mz = np.concatenate(mz_parts).astype(np.float32)
		merged_mode_labels = mode_parts
		merged_annotations = np.concatenate(annotation_parts) if annotation_parts else np.array(['Unannotated'] * total_cols)

		var_df = pd.DataFrame({
			"mz": merged_reference_mz,
			"mz_mode": pd.Categorical(merged_mode_labels),
			"lipid_annotation": pd.Categorical(merged_annotations),
		}, index=pd.Index([str(i) for i in range(total_cols)], dtype=object))

		spot_sizes: dict[str, list[float]] = {}
		write_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
		write_futures = []

		try:
			for sample in reporter.tqdm(self.samples, desc="8/9 - Generating AnnData objects and saving results",
										unit="sample"):
				ref_mode = MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in sample.ion_modes else MsiIonMode.NEGATIVE
				sample_id = sample.sample_id
				physical_coords = sample._metadata[ref_mode][MsiMetadata.PHYSICAL_COORDINATES].astype(np.float32)
				raster_coords = sample._metadata[ref_mode][MsiMetadata.RASTER_COORDINATES]
				rows = self.interpolated[sample_id][ref_mode].shape[0]

				# Spot size from raster_size: [width, height] in micrometers
				raster_size = sample._metadata[ref_mode][MsiMetadata.RASTER_SIZE]
				spot_size = np.array([float(raster_size[0]), float(raster_size[1])], dtype=np.float32)
				spot_sizes[sample_id] = spot_size.tolist()

				# Merge pos/neg intensity matrices into a single (N, M) float32 array
				raw_matrix = np.zeros((rows, total_cols), dtype=np.float32)
				norm_matrix = np.zeros((rows, total_cols), dtype=np.float32)

				if MsiIonMode.POSITIVE in sample.ion_modes and positive_cols > 0:
					raw_matrix[:, :positive_cols] = self.interpolated[sample_id][MsiIonMode.POSITIVE].astype(np.float32)
					norm_matrix[:, :positive_cols] = self.normalized[sample_id][MsiIonMode.POSITIVE].astype(np.float32)
				if MsiIonMode.NEGATIVE in sample.ion_modes and negative_cols > 0:
					raw_matrix[:, positive_cols:] = self.interpolated[sample_id][MsiIonMode.NEGATIVE].astype(np.float32)
					norm_matrix[:, positive_cols:] = self.normalized[sample_id][MsiIonMode.NEGATIVE].astype(np.float32)

				# Free per-mode arrays now that they're merged
				del self.interpolated[sample_id], self.normalized[sample_id]

				# Convert to sparse CSR for memory efficiency
				X_sparse = sp.csr_matrix(norm_matrix)
				raw_sparse = sp.csr_matrix(raw_matrix)
				del raw_matrix, norm_matrix

				# Build obs DataFrame
				n_obs = rows
				obs_index = pd.Index([f"{sample_id}_{i}" for i in range(n_obs)], dtype=object)

				obs_df = pd.DataFrame({
					'sample_id': pd.Categorical([sample_id] * n_obs),
					'foreground': pd.Categorical(self.foreground_masks[sample_id]),
				}, index=obs_index)

				# Create AnnData: .X = normalized (sparse), .layers["raw"] = raw interpolated (sparse)
				adata = ad.AnnData(
					X=X_sparse,
					layers={'raw': raw_sparse},
					obs=obs_df,
					obsm={
						'spatial': physical_coords,
						'raster_coordinates': raster_coords
					},
					var=var_df.copy(),
					uns={'spot_size': spot_size.tolist()}
				)

				# Per-sample Leiden clustering on the normalized data
				n_pcs = min(50, adata.n_obs - 1, adata.n_vars - 1)
				if adata.n_obs >= 2 and n_pcs >= 2:
					sc.pp.pca(adata, n_comps=n_pcs)
					sc.pp.neighbors(adata, n_neighbors=min(15, adata.n_obs - 1))
					sc.tl.leiden(adata, resolution=self._LEIDEN_RESOLUTION, flavor="igraph", n_iterations=2, key_added='leiden')
				else:
					adata.obs['leiden'] = '0'
				adata.obs['leiden'] = pd.Categorical(adata.obs['leiden'])

				# Save with lzf compression (faster than gzip for intermediate files)
				output_file = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')
				# Wait for any previous write to finish before reusing gc
				for f in write_futures:
					f.result()
				write_futures.clear()
				# Write in background thread so next sample's computation overlaps with I/O
				write_futures.append(write_executor.submit(utils.write_h5ad_compat, adata, output_file, compression=self._H5AD_INTERMEDIATE_COMPRESSION))
				processed_samples[sample.sample_id] = output_file

			# Wait for the last write to complete
			for f in write_futures:
				f.result()
		finally:
			write_executor.shutdown(wait=True)
		gc.collect()

		# STEP 9: Merge all samples into a single dataset (memory-efficient on-disk concat)
		reporter.step("9/9 - Merging all samples into a single dataset")
		merged_file = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')

		if processed_samples:
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
			utils.write_h5ad_compat(merged_adata, merged_file, compression=self._H5AD_COMPRESSION)
			del merged_adata
			gc.collect()

			processed_samples["merged"] = merged_file
		return processed_samples


# --- Modality Registration ---

def _create_msi_samples(path, sample_ids, modality_name, settings):
	samples = []
	for sample_id in sample_ids:
		subdir = os.listdir(os.path.join(path, sample_id, modality_name))
		ion_modes = 0

		for mode in MsiIonMode.list():
			if mode in subdir:
				ion_modes += 1

		if ion_modes == 0:
			raise ValueError(f"No ion mode subdirectories found for sample {sample_id}. Expected at least one of: {MsiIonMode.list()}")
		elif ion_modes == 1:
			samples.append(
				MsiSample(
					source_path=path,
					sample_id=sample_id,
					modality_name=modality_name,
					double_ion_mode=False,
					ion_mode=MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in subdir else MsiIonMode.NEGATIVE
				)
			)
		else:
			samples.append(
				MsiSample(
					source_path=path,
					sample_id=sample_id,
					modality_name=modality_name,
					double_ion_mode=True,
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
		'recalibration_reference': settings.get(MsiPreprocessingParams.RECALIBRATION_REFERENCE, None),
		'min_intensity_threshold': settings.get(MsiPreprocessingParams.MIN_INTENSITY_THRESHOLD, 1e4),
		'detect_background': settings.get(MsiPreprocessingParams.DETECT_BACKGROUND, False),
		'sample_type': settings.get(MsiPreprocessingParams.SAMPLE_TYPE, MsiSampleType.TISSUE),
		'force_recomputing': settings.get(MsiPreprocessingParams.FORCE_RECOMPUTING, False),
	}

register_modality('msi', ModalityHandler(
	create_samples=_create_msi_samples,
	create_dataset=_create_msi_dataset,
	extract_settings=_extract_msi_settings,
))
