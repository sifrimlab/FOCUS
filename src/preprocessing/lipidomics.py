import numpy as np
import os, tqdm, psutil
from sklearn.linear_model import LinearRegression
import anndata as ad
import pandas as pd
import xml.etree.ElementTree as ET
import concurrent.futures
from numba import njit
from joblib import Parallel, delayed
from functools import partial
from constants import ImzMLFileParser, MsiIntensityNormalization, MsiMetadata, MsiIonMode

from constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED

class MsiSample:
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
		input_path : str
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
		if double_ion_mode != True and ion_mode not in MsiIonMode.list():
			raise ValueError(f'Invalid ion_mode value. Expected one of {MsiIonMode.list()} when double_ion_mode is False.')

		# Check that the input path exists and it can be read
		if not os.path.exists(source_path):
			raise FileNotFoundError(f"Input path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise PermissionError(f"Input path {source_path} is not readable.")
		
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

		self.source_path = source_path
		self.sample_id = sample_id
		self.double_ion_mode = double_ion_mode
		self.modality_name = modality_name
		self.ion_mode = ion_mode

		# Initialize the other variables
		self._metadata_files = {}				# For each ion mode, store the absolute path to the imzML file
		self._binary_files = {}					# For each ion mode, store the absolute path to the IBD file
		self._metadata = {}						# For each ion mode, store the metadata extracted from the imzML file
		self._aligned_mz = {}					# For each ion mode, store the aligned M/Z values (obtained from preprocessing)

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
		
		x, y, mzs, intesities = None, None, None, None
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

		# Fallback if the physical coordinates are not provided
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
			elif element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] in ['intensities', "intensityArray"]:
				intesities = {
					'length': length,
					'encoded_length': encoded_length,
					'offset': offset
				}

		return {'pixel_x': x, 'pixel_y': y, 'mzs': mzs, 'intensities': intesities, 'physical_x': physical_x, 'physical_y': physical_y}
	
	def _correct_rotation_error(self, physical_coords: np.ndarray[np.float32], pixel_coords: np.ndarray[np.int32]) -> np.ndarray[np.float32]:
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
						[np.sin(angle),  np.cos(angle)]])
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
			raise KeyError("Could not find the data types for mz and intensities in the imzML file. Check the metadata name")

		# Extract the raster size in micrometers
		raster_size = np.array([0, 0], dtype = np.int16)
		for scan_settings_list in root.find(ImzMLFileParser.SCAN_SETTINGS_LIST):
			for scan_settings in scan_settings_list:
				if scan_settings.attrib['name'] == "pixel size x":
					raster_size[0] = int(scan_settings.attrib['value'])
				if scan_settings.attrib['name'] == "pixel size y":
					raster_size[1] = int(scan_settings.attrib['value'])	

		run = root.find(ImzMLFileParser.RUN_KEY)
		spectrum_list = run.find(ImzMLFileParser.SPECTRUM_LIST_KEY)
		spectra = spectrum_list.findall(ImzMLFileParser.SPECTRUM_KEY)
		
		# Decode the binary data from the imzML file
		parsed_spectra = [self._spectra_to_dict(spectrum) for spectrum in spectra]

		# Convert the parsed spectra to a set of Numpy arrays to facilitate further processing
		pixel_coordinates = np.array([(metadata["pixel_y"], metadata['pixel_x']) for metadata in parsed_spectra], dtype = np.int32)		#NOTE: The X and Y axes are inverted between pixel and physical coordinates
		physical_coordinates = np.array([(metadata["physical_x"], metadata['physical_y']) for metadata in parsed_spectra], dtype = np.float32)
		mz_binary_metadata = np.array([(metadata["mzs"]["length"], metadata["mzs"]["encoded_length"], metadata["mzs"]["offset"]) for metadata in parsed_spectra])
		intensities_binary_metadata = np.array([(metadata["intensities"]["length"], metadata["intensities"]["encoded_length"], metadata["intensities"]["offset"]) for metadata in parsed_spectra])

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

	def _compute_raster_coordinates(physical_coords: np.ndarray, raster_size: np.ndarray[np.int16]) -> np.ndarray[np.int32]:
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
		tile_x = np.floor_divide(physical_coords[:,0], raster_size[0]).astype(int)
		tile_y = np.floor_divide(physical_coords[:,1], raster_size[1]).astype(int)
		
		# Top-left coordinate of the block in raster
		x1 = tile_x * raster_size[0]
		y1 = tile_y * raster_size[1]
		
		# Bottom-right coordinate
		x2 = x1 + raster_size[0]
		y2 = y1 + raster_size[1]
		
		# Output shape: (N,2,2): [[x1,y1],[x2,y2]] for each coord
		pixel_coords = np.zeros((physical_coords.shape[0], 2, 2), dtype=np.int32)
		pixel_coords[:,0,:] = np.stack([x1, y1], axis=1)   # top-left
		pixel_coords[:,1,:] = np.stack([x2, y2], axis=1)   # bottom-right

		return pixel_coords
	
	def _filter_unpaired_spots(self) -> None:
		'''
		Analyze the metadata of both ion modes and remove unpaired spots (experimental artifacts).
		'''

		if not self.double_ion_mode:
			return
		
		# Extract the pixel coordinates for both ion modes
		pos_pixel_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PIXEL_COORDINATES]
		neg_pixel_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PIXEL_COORDINATES]

		# Convert to structured array to compare rows
		dtype = [('x', pos_pixel_coords.dtype), ('y', pos_pixel_coords.dtype)]
		pos_pixel_coords_struct = pos_pixel_coords.view(dtype)
		neg_pixel_coords_struct = neg_pixel_coords.view(dtype)

		# Create mask of elements in the largest array not in the other one
		if pos_pixel_coords.shape[0] >= neg_pixel_coords.shape[0]:
			large_coords, small_coords = pos_pixel_coords_struct, neg_pixel_coords_struct
			large_mode, small_mode = MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE
		else:
			large_coords, small_coords = neg_pixel_coords_struct, pos_pixel_coords_struct
			large_mode, small_mode = MsiIonMode.NEGATIVE, MsiIonMode.POSITIVE

		missing_mask = ~np.isin(large_coords, small_coords)
		missing_indices = np.nonzero(missing_mask)[0]

		filtered_large_coords = np.delete(large_coords, missing_indices, axis=0)

		# Update the metadata to remove the unpaired spots
		self._metadata[large_mode][MsiMetadata.PIXEL_COORDINATES] = filtered_large_coords.view(np.int32).reshape(-1, 2)
		self._metadata[large_mode][MsiMetadata.PHYSICAL_COORDINATES] = np.delete(
			self._metadata[large_mode][MsiMetadata.PHYSICAL_COORDINATES],
			missing_indices,
			axis=0
		)
		self._metadata[large_mode][MsiMetadata.MZ_BINARY_METADATA] = np.delete(
			self._metadata[large_mode][MsiMetadata.MZ_BINARY_METADATA],
			missing_indices,
			axis=0
		)
		self._metadata[large_mode][MsiMetadata.INTENSITIES_BINARY_METADATA] = np.delete(
			self._metadata[large_mode][MsiMetadata.INTENSITIES_BINARY_METADATA],
			missing_indices,
			axis=0
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

		# If there are both ion modes, correct the physical coordinates offset between the two
		if self.double_ion_mode:

			# Filter out unpaired points (experimental artifacts)
			self._filter_unpaired_spots()

			# Compute the offset between the two physical coordinates sets
			pos_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES]
			neg_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES]

			# Use an affine transformation to account for the translation
			def affine_transform(points):
				aug = np.hstack([points, np.ones((points.shape[0],1))])
				x_new = model_x.predict(aug)
				y_new = model_y.predict(aug)
				return np.stack([x_new, y_new], axis=1)

			# If the two sets have different lenghts, 

			A = np.hstack([pos_coords, np.ones((pos_coords.shape[0], 1))])
			model_x = LinearRegression().fit(A, neg_coords[:,0])
			model_y = LinearRegression().fit(A, neg_coords[:,1])

			# Apply the transformation to the positive ion mode physical coordinates
			pos_coords_transformed = affine_transform(pos_coords)

			# Depending on the raster size, move the coordinates to the center
			if self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] == self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]:
				# The raster is square, offset along both axes
				offset = (self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] // 2, self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] // 2)
			elif self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] > self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1]:
				# The raster is rectangular, offset along the X axis
				offset = (self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][0] // 2, 0)
			else:
				# The raster is rectangular, offset along the Y axis
				offset = (0, self._metadata[MsiIonMode.POSITIVE][MsiMetadata.RASTER_SIZE][1] // 2)

			# Apply the offset to the transformed coordinates and compute the average between pos and neg to have the coordinate of the raster's center
			pos_coords_transformed -= np.array(offset)
			final_physical_coords = np.mean([pos_coords_transformed, neg_coords], axis=0)  # Midpoint between the two physical sets
		else:
			final_physical_coords = self._metadata[self.ion_mode][MsiMetadata.PHYSICAL_COORDINATES]

		# Normalize the physical coordinates to start from (0,0)
		final_physical_coords -= final_physical_coords.min(axis=0)

		# Replace the physical coordinates in the metadata
		for mode in self._metadata.keys():
			self._metadata[mode][MsiMetadata.PHYSICAL_COORDINATES] = final_physical_coords

			# Compute the raster pixel coordinates for each physical point
			self._metadata[mode][MsiMetadata.PIXEL_COORDINATES] = MsiSample._compute_raster_coordinates(
				final_physical_coords,
				self._metadata[mode][MsiMetadata.RASTER_SIZE]
			)

	def load_mz_vectors(self) -> dict[list[list[np.float32 | np.float64]]]:
		'''
		Load the M/Z vectors from the binary IBD files.

		Returns
		----------
		dict[list[list[np.float32]]]
			A dictionary with the M/Z vectors for each ion mode.
			Each ion mode contains a list of M/Z vectors, one for each spectrum. Each M/Z vector is a list of variable length.
		'''

		mz_vectors = {}

		# For each ion mode
		for mode in self._metadata.keys():

			# Define an utility to read the M/Z values from the binary file
			read_mzs = lambda count, offset: np.fromfile(self._binary_files[mode], dtype = self._metadata[mode][MsiMetadata.MZ_DTYPE], count = count, offset = offset)

			# Read the M/Z values for each spectrum
			mz_vectors[mode] = [read_mzs(
				self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA][i, 0],
				self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA][i, 2]
			) for i in range(self._metadata[mode][MsiMetadata.MZ_BINARY_METADATA].shape[0])]

		return mz_vectors
	
	def load_intensities(self) -> dict[list[list[np.float32 | np.float64]]]:
		'''
		Load the intensity vectors from the binary IBD files.

		Returns
		----------
		dict[list[list[np.float32]]]
			A dictionary with the intensity vectors for each ion mode.
			Each ion mode contains a list of intensity vectors, one for each spectrum. Each intensity vector is a list of variable length.
		'''

		intensity_vectors = {}

		# For each ion mode
		for mode in self._metadata.keys():

			# Define an utility to read the intensity values from the binary file
			read_intensities = lambda count, offset: np.fromfile(self._binary_files[mode], dtype = self._metadata[mode][MsiMetadata.INTENSITIES_DTYPE], count = count, offset = offset)

			# Read the intensity values for each spectrum
			intensity_vectors[mode] = [read_intensities(
				self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA][i, 0],
				self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA][i, 2]
			) for i in range(self._metadata[mode][MsiMetadata.INTENSITIES_BINARY_METADATA].shape[0])]

		return intensity_vectors

@njit
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
		cluster_mz = [unique_mz[start_idx]]
		cluster_counts = [counts[start_idx]]
		centroid = unique_mz[start_idx]
		weight_sum = counts[start_idx]
		end_idx = start_idx + 1

		while end_idx < n:
			candidate_mz = unique_mz[end_idx]
			ppm_diff = abs(candidate_mz - centroid) / centroid * 1e6
			if ppm_diff <= mass_tolerance_ppm:
				cluster_mz.append(candidate_mz)
				cluster_counts.append(counts[end_idx])
				weight_sum += counts[end_idx]
				# Update weighted centroid incrementally
				weighted_sum = 0.0
				total_weight = 0.0
				for m, w in zip(cluster_mz, cluster_counts):
					weighted_sum += m * w
					total_weight += w
				centroid = weighted_sum / total_weight
				end_idx += 1
			else:
				break

		consensus_mz.append(centroid)
		consensus_weights.append(weight_sum)

		start_idx = end_idx

	return np.array(consensus_mz), np.array(consensus_weights)

def merge_chunks(prev_mz, prev_w, curr_mz, curr_w, mass_tolerance_ppm):
	"""
	Merge consensus clusters from two adjacent chunks resolving overlaps.

	Both prev_mz and curr_mz are sorted arrays.

	Returns merged consensus m/z and weights arrays.
	"""
	i, j = 0, 0
	merged_mz = []
	merged_w = []

	while i < len(prev_mz) and j < len(curr_mz):
		ppm_diff = abs(curr_mz[j] - prev_mz[i]) / ((curr_mz[j] + prev_mz[i]) / 2) * 1e6
		if ppm_diff <= mass_tolerance_ppm:
			# Merge clusters by weighted average
			tot_w = prev_w[i] + curr_w[j]
			centroid = (prev_mz[i] * prev_w[i] + curr_mz[j] * curr_w[j]) / tot_w
			merged_mz.append(centroid)
			merged_w.append(tot_w)
			i += 1
			j += 1
		elif prev_mz[i] < curr_mz[j]:
			merged_mz.append(prev_mz[i])
			merged_w.append(prev_w[i])
			i += 1
		else:
			merged_mz.append(curr_mz[j])
			merged_w.append(curr_w[j])
			j += 1

	# Append remaining
	while i < len(prev_mz):
		merged_mz.append(prev_mz[i])
		merged_w.append(prev_w[i])
		i += 1
	while j < len(curr_mz):
		merged_mz.append(curr_mz[j])
		merged_w.append(curr_w[j])
		j += 1

	return np.array(merged_mz), np.array(merged_w)

class MsiDataset:
	def __init__(self, path: str,  samples: list[MsiSample], lipid_annotation_db: str | None = None) -> None:
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

		if not isinstance(samples, list) or not all(isinstance(sample, MsiSample) for sample in samples):
			raise TypeError('Invalid input type. Expected list of MsiSample objects.')
		
		self.samples = samples
		self.reference_mz: dict[MsiIonMode, np.ndarray] = {}
		self.interpolated: dict[str, dict[MsiIonMode, np.ndarray]] = {}
		self.normalized: dict[str, dict[MsiIonMode, np.ndarray]] = {}

		self.dataset_source_path = path

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
		n_cores = os.cpu_count() or 1

		# Choose number of chunks limited by cores and memory
		n_chunks = max(n_cores, max_chunks_mem)
		n_chunks = min(n_chunks, 32)  # Optional: cap max chunks to avoid overhead

		return n_chunks

	def _compute_reference_mz(self, spectra_list: list[np.ndarray], mass_tolerance: int = 10, frequency_threshold: float = 0.01, batch_size: int = 10000) -> np.ndarray:
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
		batch_size : int
			Legacy parameter, unused.

		Returns:
		-----------
		consensus_mz : np.ndarray
			Consensus m/z values after grouping and filtering.
		"""

		# Concatenate and round m/z values
		all_mz = np.concatenate(spectra_list).astype(np.float64)
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

		with concurrent.futures.ProcessPoolExecutor() as executor:
			futures = [executor.submit(cluster_unique_mz_chunk, chunk[0], chunk[1], mass_tolerance) for chunk in chunks]
			results = [f.result() for f in futures]

		# Merge clusters across chunks
		merged_mz, merged_w = results[0]
		for i in range(1, len(results)):
			merged_mz, merged_w = merge_chunks(merged_mz, merged_w, results[i][0], results[i][1], mass_tolerance)

		# Filter by frequency threshold
		max_count = merged_w.max()
		threshold_count = max_count * frequency_threshold
		filtered_idx = merged_w >= threshold_count

		return merged_mz[filtered_idx]
	
	def _interpolate_intensities(self,
								original_mzs_list: list[np.ndarray],
								original_intensities_list: list[np.ndarray],
								reference_mz: np.ndarray,
								mass_tolerance: float) -> np.ndarray:
		"""
		Rebin intensities proportionally distributing each original peak's intensity
		across overlapping reference_mz bins weighted inversely by distance.
		"""

		n_datapoints = len(original_mzs_list)
		n_ref = reference_mz.size

		if n_datapoints == 0 or n_ref == 0:
			return np.zeros((n_datapoints, n_ref), dtype=reference_mz.dtype)

		result_matrix = np.zeros((n_datapoints, n_ref), dtype=original_intensities_list[0].dtype)

		assert np.all(reference_mz[:-1] <= reference_mz[1:]), "reference_mz must be sorted ascending"

		window = reference_mz * mass_tolerance / 1e6
		lower_bounds = reference_mz - window
		upper_bounds = reference_mz + window

		for idx, (original_mz, original_intensity) in enumerate(zip(original_mzs_list, original_intensities_list)):
			if original_mz.size == 0:
				continue

			original_mz = original_mz.astype(reference_mz.dtype)

			if not np.all(original_mz[:-1] <= original_mz[1:]):
				sort_idx = np.argsort(original_mz)
				original_mz = original_mz[sort_idx]
				original_intensity = original_intensity[sort_idx]

			# For each original mz peak, find all overlapping reference bins
			for peak_mz, peak_int in zip(original_mz, original_intensity):
				# Compute ppm distance to all reference_mz points
				ppm_diff = np.abs(reference_mz - peak_mz) / peak_mz * 1e6
				in_window_idx = np.where(ppm_diff <= mass_tolerance)[0]

				if in_window_idx.size == 0:
					# Peak doesn't fall in any bin, intensity lost here
					continue

				# Compute weights as inverse distance (add small epsilon to prevent div0)
				distances = ppm_diff[in_window_idx]
				weights = 1 / (distances + 1e-9)
				weights_sum = weights.sum()

				# Normalize weights so sum to 1
				weights /= weights_sum

				# Distribute intensity proportionally to the bins
				for w_idx, w in zip(in_window_idx, weights):
					result_matrix[idx, w_idx] += peak_int * w

		return result_matrix

	def _annotate_reference_mz(self, mz_vector: np.ndarray[np.float32], ion_mode: MsiIonMode, mass_tolerance: int = 10) -> np.ndarray:
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

	def process_dataset(self,
			mass_tolerance: int = 10,
			frequency_threshold: float = 0.01,
			batch_size: int = 10000,
			intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.TIC,
			force_recomputing: bool = False) -> dict[str, str]:
		'''
		Process the dataset by aligning the M/Z values across all samples and interpolating the intensities.

		Parameters
		----------
		mass_tolerance : int
			Adaptive mass tolerance in ppm for grouping M/Z values.
		frequency_threshold : float
			Frequency threshold for filtering M/Z values.
		batch_size : int
			Batch size for processing M/Z values.
		intensity_normalization : MsiIntensityNormalization
			Type of intensity normalization to apply.
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
			raise ValueError(f'Invalid intensity normalization method. Expected one of {MsiIntensityNormalization.list()}.')
		
		processed_samples = {}

		if not force_recomputing:
			all_sample_computed = True

			# Check if all the samples have already been processed
			for sample in self.samples:
				if not os.path.exists(MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')):
					all_sample_computed = False
					break
				else:
					processed_samples[sample.sample_id] = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')
			
			# Check if the merged dataset already exists
			if not os.path.exists(MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')):
				all_sample_computed = False
			else:
				processed_samples["merged"] = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')

			# If the merged dataset already exists and force_recompute is False, skip the computation
			if all_sample_computed:
				print("All samples have already been processed and merged dataset exists. Using cached results.")
				return processed_samples
			
		print("Processing Lipidomic Dataset")
		processed_samples = {}

		for sample in tqdm.tqdm(self.samples, desc="1/4 - Loading MSI data", unit="sample"):
			sample.initialize_sample()

		reference_mz_samples: dict[MsiIonMode, list[np.float32]] = {MsiIonMode.POSITIVE: [], MsiIonMode.NEGATIVE: []}

		# For each ion mode in each sample, compute the reference M/Z vector
		for sample in tqdm.tqdm(self.samples, desc="2/4 - Computing reference M/Z vectors", unit="sample"):
			raw_mz = sample.load_mz_vectors()
			for mode in sample.ion_modes:
				reference_mz_samples[mode].append(
					self._compute_reference_mz(
						raw_mz[mode],
						mass_tolerance=mass_tolerance,
						frequency_threshold=frequency_threshold,
						batch_size=batch_size
					)
				)
			del raw_mz  # Free memory

		# Compute the global reference M/Z vector for each ion mode. No frequency thresholding is applied here
		for mode in reference_mz_samples.keys():
			if len(reference_mz_samples[mode]) > 0:
				self.reference_mz[mode] = self._compute_reference_mz(
					reference_mz_samples[mode],
					mass_tolerance=mass_tolerance,
					frequency_threshold=0.0,
					batch_size=batch_size
				)
			else:
				self.reference_mz[mode] = np.array([], dtype=np.float32)

		# For each ion mode, annotate the reference M/Z vector if a lipid annotation database is provided
		self.lipid_annotations: dict[MsiIonMode, np.ndarray] = {}
		if self.lipid_annotation_db is not None:
			for mode in self.reference_mz.keys():
				self.lipid_annotations[mode] = self._annotate_reference_mz(
					self.reference_mz[mode],
					mode,
					mass_tolerance=mass_tolerance
				)

		# Now that the global reference M/Z vectors are computed, process each sample to interpolate the intensities
		for sample in tqdm.tqdm(self.samples, desc="3/4 - Aligning intensities to reference M/Z", unit="sample"):
			self.interpolated[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}
			self.normalized[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}

			# Load the intensities and M/Z values
			intensities = sample.load_intensities()
			original_mzs = sample.load_mz_vectors()

			# Process each ion mode separately
			for mode in sample.ion_modes:
				merged_intensities = np.zeros((len(intensities[mode]), len(self.reference_mz[mode])), dtype=sample._metadata[mode][MsiMetadata.INTENSITIES_DTYPE])

				# Consider only the datapoints for the current ion mode
				intensities_mode = intensities[mode]
				original_mzs_mode = original_mzs[mode]
				datapoints = len(intensities_mode)

				# Determine chunk size for each worker
				num_workers = min(os.cpu_count() or 1, datapoints)
				chunk_size = (datapoints + num_workers - 1) // num_workers  # ceil division

				# Split data into contiguous chunks: lists of arrays
				chunks = [
					(original_mzs_mode[start:end], intensities_mode[start:end])
					for start, end in [(i * chunk_size, min((i + 1) * chunk_size, datapoints)) for i in range(num_workers)]
				]

				# Worker function which partially fixes reference_mz and mass_tolerance
				worker_func = partial(self._interpolate_intensities, 
                     reference_mz=self.reference_mz[mode], 
                     mass_tolerance=mass_tolerance)

				# Joblib parallel execution - auto-manages processes
				results = Parallel(n_jobs=num_workers, 
								backend='loky',      # Robust process management
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
					tic = merged_intensities.sum(axis=1, keepdims=True)
					tic[tic == 0] = 1  # Prevent division by zero
					merged_intensities = merged_intensities / tic

				self.normalized[sample.sample_id][mode] = merged_intensities
			
			del intensities, original_mzs  # Free memory

		for sample in self.samples:
			reference_mode = MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in sample.ion_modes else MsiIonMode.NEGATIVE
			sample_id = sample.sample_id
			raster_size = sample._metadata[reference_mode][MsiMetadata.RASTER_SIZE].tolist()
			physical_coords = sample._metadata[reference_mode][MsiMetadata.PHYSICAL_COORDINATES]				# Shape (N, 2)
			raster_coords = sample._metadata[reference_mode][MsiMetadata.PIXEL_COORDINATES]						# Shape (N, 2, 2)
			rows = self.interpolated[sample_id][reference_mode].shape[0]										# Shape (N, )
			positive_cols = self.interpolated[sample_id][MsiIonMode.POSITIVE].shape[1] if MsiIonMode.POSITIVE in sample.ion_modes else 0	# Shape (M1, )
			negative_cols = self.interpolated[sample_id][MsiIonMode.NEGATIVE].shape[1] if MsiIonMode.NEGATIVE in sample.ion_modes else 0	# Shape (M2, )
			rows_dtype = self.interpolated[sample_id][MsiIonMode.POSITIVE].dtype if MsiIonMode.POSITIVE in sample.ion_modes else self.interpolated[sample_id][MsiIonMode.NEGATIVE].dtype

			merged_interpolated = np.zeros((
				rows,
				positive_cols + negative_cols
				), dtype = rows_dtype)
			
			merged_normalized = np.zeros((
				rows,
				positive_cols + negative_cols
			), dtype = rows_dtype)

			merged_reference_mz = np.concatenate([self.reference_mz[MsiIonMode.POSITIVE], self.reference_mz[MsiIonMode.NEGATIVE]])
			reference_mode = np.concatenate([[MsiIonMode.POSITIVE] * len(self.reference_mz[MsiIonMode.POSITIVE]), [MsiIonMode.NEGATIVE] * len(self.reference_mz[MsiIonMode.NEGATIVE])])

			# Concatenate the annotations if available
			if self.lipid_annotation_db is not None:
				reference_annotations = np.concatenate([
					self.lipid_annotations[MsiIonMode.POSITIVE] if MsiIonMode.POSITIVE in self.lipid_annotations else np.zeros_like(self.reference_mz[MsiIonMode.POSITIVE], dtype=str),
					self.lipid_annotations[MsiIonMode.NEGATIVE] if MsiIonMode.NEGATIVE in self.lipid_annotations else np.zeros_like(self.reference_mz[MsiIonMode.NEGATIVE], dtype=str)
				])
			
			# Merge the two ion modes
			if sample.double_ion_mode:
				merged_interpolated[:, :self.interpolated[sample_id][MsiIonMode.POSITIVE].shape[1]] = self.interpolated[sample_id][MsiIonMode.POSITIVE]
				merged_interpolated[:, self.interpolated[sample_id][MsiIonMode.POSITIVE].shape[1]:] = self.interpolated[sample_id][MsiIonMode.NEGATIVE]

				merged_normalized[:, :self.normalized[sample_id][MsiIonMode.POSITIVE].shape[1]] = self.normalized[sample_id][MsiIonMode.POSITIVE]
				merged_normalized[:, self.normalized[sample_id][MsiIonMode.POSITIVE].shape[1]:] = self.normalized[sample_id][MsiIonMode.NEGATIVE]
			else:
				if sample.ion_mode == MsiIonMode.POSITIVE:
					merged_interpolated = self.interpolated[sample_id][MsiIonMode.POSITIVE]
					merged_normalized = self.normalized[sample_id][MsiIonMode.POSITIVE]
				else:
					merged_interpolated = self.interpolated[sample_id][MsiIonMode.NEGATIVE]
					merged_normalized = self.normalized[sample_id][MsiIonMode.NEGATIVE]

			# Create the AnnData object
			self.adata = ad.AnnData(
				X = merged_interpolated,
				layers = {
					f"X_{intensity_normalization}": merged_normalized
				},
				obs = pd.DataFrame({
					'sample_id': [sample_id] * merged_interpolated.shape[0]
				}, index = [str(i) for i in range(merged_interpolated.shape[0])]),
				obsm={
					'spatial': physical_coords,
					'raster_coordinates': raster_coords
				},
				var = pd.DataFrame({
					"mz": merged_reference_mz,
					"mz_mode": reference_mode,
					"lipid_annotation": reference_annotations if self.lipid_annotation_db is not None else ['Unannotated'] * merged_reference_mz.shape[0]
				}, index = [str(i) for i in range(merged_interpolated.shape[1])]),
				uns = {
					"raster_size": raster_size,
                }
			)

			# Save the AnnData object to the output path
			output_file = MODALITY_PREPROCESSING(self.dataset_source_path, sample.sample_id, sample.modality_name, 'h5ad')
			self.adata.write_h5ad(output_file)
			processed_samples[sample.sample_id] = output_file

		# Save the merged dataset
		adatas: list[ad.AnnData] = []

		for sample_id, output_file in tqdm.tqdm(processed_samples.items(), desc="4/4 - Merging MSI samples into AnnData", unit="sample"):
			sample_adata = ad.read_h5ad(output_file)

			# Update the obs_names to include the sample ID for uniqueness
			sample_adata.obs_names = [f"{sample_id}_{obs_name}" for obs_name in sample_adata.obs_names]
			adatas.append(sample_adata)

		# Concatenate all the AnnData objects, ensuring unique obs_names (indexes)
		if adatas:
			msi_adata = ad.concat(adatas)

			# Add the var informations
			msi_adata.var = adatas[0].var.copy()
			msi_adata.uns = adatas[0].uns.copy()

		# Save the combined AnnData
		msi_adata.write_h5ad(MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad'))
		processed_samples["merged"] = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, 'h5ad')
		return processed_samples