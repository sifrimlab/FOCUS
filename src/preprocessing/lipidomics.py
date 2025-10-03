import numpy as np
import torch, os
from tqdm import tqdm
from sklearn.linear_model import LinearRegression
import anndata as ad
import pandas as pd
import xml.etree.ElementTree as ET
from constants import ImzMLFileParser, MsiIntensityNormalization, MsiMetadata, MsiIonMode

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
		self.output_path = os.path.join(source_path, sample_id, "preprocessing", modality_name)
		self.sample_id = sample_id
		self.double_ion_mode = double_ion_mode
		self.modality_name = modality_name
		self.ion_mode = ion_mode

		# Create the output directory if it does not exist
		if not os.path.exists(self.output_path):
			os.makedirs(self.output_path)

		# Initialize the other variables
		self._metadata_files = {}				# For each ion mode, store the absolute path to the imzML file
		self._binary_files = {}					# For each ion mode, store the absolute path to the IBD file
		self._metadata = {}						# For each ion mode, store the metadata extracted from the imzML file
		self._aligned_mz = {}					# For each ion mode, store the aligned M/Z values (obtained from preprocessing)

		# Initialize the sample
		self._initialize_sample()

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

	def _initialize_sample(self) -> None:
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
			# Compute the offset between the two physical coordinates sets
			pos_coords = self._metadata[MsiIonMode.POSITIVE][MsiMetadata.PHYSICAL_COORDINATES]
			neg_coords = self._metadata[MsiIonMode.NEGATIVE][MsiMetadata.PHYSICAL_COORDINATES]

			A = np.hstack([pos_coords, np.ones((pos_coords.shape[0], 1))])
			model_x = LinearRegression().fit(A, neg_coords[:,0])
			model_y = LinearRegression().fit(A, neg_coords[:,1])

			# Use an affine transformation to account for the translation
			def affine_transform(points):
				aug = np.hstack([points, np.ones((points.shape[0],1))])
				x_new = model_x.predict(aug)
				y_new = model_y.predict(aug)
				return np.stack([x_new, y_new], axis=1)
			
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

class MsiDataset:
	def __init__(self, samples: list[MsiSample]) -> None:
		'''
		MSI dataset containing multiple samples. This class provide utilities to preprocess the raw experiments
		and generate an aligned and corrected AnnData object.

		Parameters
		----------
		samples : list[MsiSample]
			List of MsiSample objects.
		'''

		if not isinstance(samples, list) or not all(isinstance(sample, MsiSample) for sample in samples):
			raise TypeError('Invalid input type. Expected list of MsiSample objects.')
		
		self.samples = samples
		self.reference_mz: dict[MsiIonMode, np.ndarray] = {}
		self.interpolated: dict[str, dict[MsiIonMode, np.ndarray]] = {}
		self.normalized: dict[str, dict[MsiIonMode, np.ndarray]] = {}

	def process_dataset(self, mass_tolerance: int = 10, frequency_threshold: float = 0.01, batch_size: int = 10000, intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.TIC) -> None:
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
		'''

		# Check if the required normalization method is implemented
		if intensity_normalization not in MsiIntensityNormalization.list():
			raise ValueError(f'Invalid intensity normalization method. Expected one of {MsiIntensityNormalization.list()}.')

		reference_mz_samples: dict[MsiIonMode, list[np.float32]] = {MsiIonMode.POSITIVE: [], MsiIonMode.NEGATIVE: []}

		# For each ion mode in each sample, compute the reference M/Z vector
		for sample in tqdm(self.samples, desc="Computing reference M/Z vectors", unit="sample"):
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

		# Now that the global reference M/Z vectors are computed, process each sample to interpolate the intensities
		for sample in tqdm(self.samples, desc="Aligning intensities to reference M/Z", unit="sample"):
			self.interpolated[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}
			self.normalized[sample.sample_id] = {MsiIonMode.POSITIVE: None, MsiIonMode.NEGATIVE: None}

			# Load the intensities and M/Z values
			intensities = sample.load_intensities()
			original_mzs = sample.load_mz_vectors()

			# Process each ion mode separately
			for mode in sample.ion_modes:
				# Define the final data matrix to store the intensities values
				merged_intensities = np.zeros((len(intensities[mode]), len(self.reference_mz[mode])), dtype = sample._metadata[mode][MsiMetadata.INTENSITIES_DTYPE])

				for index in range(0, merged_intensities.shape[0]):
					# Interpolate the intensities values to the unified M/Z values
					merged_intensities[index, :] = self._interpolate_intensities(
						original_mzs[mode][index],
						intensities[mode][index],
						self.reference_mz[mode],
						mass_tolerance=mass_tolerance
					)

				self.interpolated[sample.sample_id][mode] = merged_intensities

				# Apply intensity normalization
				if intensity_normalization == MsiIntensityNormalization.TIC:
					# Total Ion Current normalization
					tic = merged_intensities.sum(axis=1, keepdims=True)
					tic[tic == 0] = 1  # Prevent division by zero
					merged_intensities = merged_intensities / tic
				
				self.normalized[sample.sample_id][mode] = merged_intensities

		for sample in self.samples:
			reference_mode = MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in sample.ion_modes else MsiIonMode.NEGATIVE
			sample_id = sample.sample_id
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
					'batch_id': [sample_id] * merged_interpolated.shape[0]
				}, index = [str(i) for i in range(merged_interpolated.shape[0])]),
				obsm={
					'physical_coordinates': physical_coords,
					'raster_coordinates': raster_coords
				},
				var = pd.DataFrame({
					"mz": merged_reference_mz,
					"mz_mode": reference_mode
				}, index = [str(i) for i in range(merged_interpolated.shape[1])])
			)

			# Save the AnnData object to the output path
			output_file = os.path.join(sample.output_path, f"{sample.sample_id}_{sample.modality_name}_processed.h5ad")
			self.adata.write_h5ad(output_file)

	def _compute_reference_mz(self, spectra_list: list[np.ndarray], mass_tolerance: int = 10, frequency_threshold: float = 0.01, batch_size: int = 10000) -> np.ndarray:
		"""
		Create consensus reference m/z vector using adaptive mass tolerance
		Reference: 10.1021/acs.analchem.0c03833

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

		# Group the m/z values from all spectra and count occurrences
		all_mz = np.concatenate(spectra_list)
		all_mz = all_mz.astype(np.float32)                                  # Ensure all m/z values are float32 for consistency
		all_mz = np.round(all_mz, decimals = 5)                               # Round m/z values to 5 decimal places to reduce numerical noise
		all_mz.sort()
		unique_mz, counts = np.unique(all_mz, return_counts=True)

		# Cast unique_mz and counts to float32 for consistency
		unique_mz = unique_mz.astype(np.float32)
		counts = counts.astype(np.float32)

		unique_mz = torch.from_numpy(unique_mz).float().cuda()
		counts = torch.from_numpy(counts).float().cuda()

		# Get the total length of unique m/z values
		total_length = unique_mz.shape[0]

		# Store the totals
		total_unique_mz, total_weights = None, None

		# Iterate over the unique m/z values and create consensus peaks
		while total_unique_mz is None or torch.equal(total_unique_mz, unique_mz) == False:

			# Define the new unique_mz as the result of the previous iteration
			if total_unique_mz is not None:
				unique_mz = total_unique_mz
				counts = total_weights
				total_length = unique_mz.shape[0]

				# Reset the totals for the next iteration
				total_unique_mz, total_weights = None, None

			for batch_start in range(0, total_length, batch_size):

				# Get the batch slice
				batch_end: int = min(batch_start + batch_size, total_length)

				unique_mz_batch: torch.Tensor = unique_mz[batch_start:batch_end]
				counts_batch: torch.Tensor = counts[batch_start:batch_end]

				# Computing adaptive mass tolerance windows around each m/z to compute the overlapping clusters
				tolerance_mask = torch.zeros((unique_mz_batch.shape[0], unique_mz_batch.shape[0]), dtype=bool)
				tolerance_mask = torch.abs(unique_mz_batch[:, None] - unique_mz_batch[None, :]) <= (unique_mz_batch[:, None] * mass_tolerance * 1e-6)

				# Count the number of overlapping clusters
				cluster_mz = torch.where(tolerance_mask, unique_mz_batch[None, :], torch.nan)
				cluster_weights = torch.where(tolerance_mask, counts_batch[None, :], 0)

				# Compute a unicity filter mask
				unicity_mask = self._compute_tolerance_matrix(cluster_weights)

				# Apply the unicity mask to the clusters
				cluster_mz = torch.where(unicity_mask, cluster_mz, torch.nan)
				cluster_weights = torch.where(unicity_mask, cluster_weights, torch.nan)

				centroid_mz = torch.nanmean(cluster_mz, axis = 1)
				centroid_weights = torch.nansum(cluster_weights, axis = 1)

				# Filter duplicated m/z values and sum their intensities
				unique_centroid_mz = torch.unique(centroid_mz)
				unique_weights = torch.zeros_like(unique_centroid_mz, dtype=counts_batch.dtype)
				weights_matrix = centroid_weights * (centroid_mz == unique_centroid_mz[:, None])
				weights_matrix[weights_matrix == 0] = torch.nan
				unique_weights = torch.nanmean(weights_matrix, axis=1)

				# Get the indices of NaN values
				nan_indices = torch.isnan(unique_centroid_mz)
				# Remove NaN values
				unique_centroid_mz = unique_centroid_mz[~nan_indices]
				unique_weights = unique_weights[~nan_indices]

				if total_unique_mz is None:
					total_unique_mz = unique_centroid_mz
					total_weights = unique_weights
				else:
					# Concatenate the results
					total_unique_mz = torch.concatenate((total_unique_mz, unique_centroid_mz))
					total_weights = torch.concatenate((total_weights, unique_weights))

				del unique_mz_batch, counts_batch, tolerance_mask, cluster_mz, cluster_weights, unique_centroid_mz, unique_weights
				del unicity_mask, centroid_mz, centroid_weights, weights_matrix
				torch.cuda.empty_cache()
				torch.cuda.synchronize()

		# Apply a frequency threshold to filter out low-frequency m/z values
		if frequency_threshold > 0:
			peak_indices: torch.Tensor = self._find_peaks_torch(total_weights, prominence_factor = frequency_threshold)
			consensus_mz: torch.Tensor = total_unique_mz[peak_indices]
		else:
			consensus_mz: torch.Tensor = total_unique_mz

		consensus_mz_cpu = consensus_mz.cpu().numpy()

		# Free GPU memory
		del unique_mz, counts, total_unique_mz, total_weights, consensus_mz
		torch.cuda.empty_cache()
		torch.cuda.synchronize()

		return consensus_mz_cpu

	def _find_peaks_torch(self, density: torch.Tensor, prominence_factor: float = 0.01) -> torch.Tensor:
		"""
		PyTorch peak detection with basic prominence filtering
		
		Args:
			density: 1D tensor of density values
			prominence_factor: Relative threshold (0.01 = 1% of max density)
		
		Returns:
			Tensor of peak indices
		"""
		# Find local maxima
		shifted_left = density[:-2]
		shifted_center = density[1:-1]
		shifted_right = density[2:]
		
		peaks = (shifted_center > shifted_left) & (shifted_center > shifted_right)
		peak_indices = torch.nonzero(peaks).squeeze() + 1  # Compensate for window shift
		
		# Apply prominence filter
		if prominence_factor > 0:
			threshold = prominence_factor * density.max()
			peak_heights = density[peak_indices]
			mask = peak_heights >= threshold
			peak_indices = peak_indices[mask]
		
		return peak_indices

	def _compute_tolerance_matrix(self, input: torch.Tensor) -> torch.Tensor:
		"""
		Scan the tolerance matrix and select to which row to uniquely assign an M/Z value (column).
		This method takes a boolean matrix of shape (N, M) that represents the overlapping clusters of M/Z values.
		It determines to which cluster to assign an M/Z value based on the weight of the clusters.
		This ensures that each M/Z value that falls within multiple clusters is always assigned to the cluster with the highest density.
		
		Parameters:
		-----------
			input: torch.Tensor
				A boolean tensor of shape (N, M) where N is the number of rows (clusters) and M is the number of columns (M/Z values).
			
		Returns:
		-----------
			torch.Tensor
				A boolean tensor of shape (N, M) where each column has a single True value indicating the selected row for that M/Z value.
				All other values are False.
		"""

		# Create boolean mask from non-zero values
		bool_mask = (input != 0)
		
		# Calculate row sums of numeric values
		row_sums = input.sum(dim=1)  # Shape: (N,)
		
		# Create scoring matrix with -inf for zeros
		score = torch.where(bool_mask, row_sums.unsqueeze(1), -torch.inf)
		
		# Find best rows per column
		max_indices = score.argmax(dim=0)
		
		# Identify active columns
		has_nonzero = bool_mask.any(dim=0)
		
		# Build output mask
		mask = torch.zeros_like(bool_mask)
		valid_cols = has_nonzero.nonzero().squeeze(-1)
		
		if valid_cols.numel() > 0:
			mask[max_indices[valid_cols], valid_cols] = True
			
		return mask
	
	def _interpolate_intensities(self, original_mz: np.ndarray, original_intensity: np.ndarray, reference_mz: np.ndarray, mass_tolerance: int) -> np.ndarray:
		"""
		GPU-accelerated MSI intensities interpolation using variable-size windowing to map peaks to a reference M/Z vector.

		Parameters
		----------
		original_mz : np.ndarray
			Original m/z values with shape (N, ).
		original_intensity : np.ndarray
			Corresponding intensity values with shape (N, ).
		reference_mz : np.ndarray
			Target reference m/z values with shape (M, ).
		mass_tolerance : float
			Tolerance window in parts per million (ppm).

		Returns
		-------
		np.ndarray
			Intensities mapped to reference_mz.
		"""

		device = "cuda" if torch.cuda.is_available() else "cpu"

		# Move to GPU
		mz = torch.from_numpy(original_mz).float().to(device)
		intensity = torch.from_numpy(original_intensity).float().to(device)
		ref_mz = torch.from_numpy(reference_mz).float().to(device)

		# Compute PPM window bounds
		window = mz * mass_tolerance / 1e6
		lower = mz - window
		upper = mz + window

		# Expand dimensions for broadcasting
		ref_mz_exp = ref_mz.unsqueeze(0)  # (1, M)
		mz_exp = mz.unsqueeze(1)          # (N, 1)
		lower_exp = lower.unsqueeze(1)    # (N, 1)
		upper_exp = upper.unsqueeze(1)    # (N, 1)

		# Boolean mask for matching windows
		in_window = (ref_mz_exp >= lower_exp) & (ref_mz_exp <= upper_exp)

		# Distance from each mz to each reference mz (masked)
		distances = torch.where(in_window, torch.abs(ref_mz_exp - mz_exp), torch.inf)

		# Find index of closest ref_mz within window
		nearest_idx = torch.argmin(distances, axis=1)
		valid = torch.any(in_window, axis=1)  # mz values that found a match

		# Only keep valid mappings
		valid_idx = nearest_idx[valid]
		valid_intensity = intensity[valid]

		# Accumulate using scatter_add for efficient summation
		result = torch.zeros_like(ref_mz, dtype=intensity.dtype)
		result.scatter_add_(0, valid_idx, valid_intensity)

		return result.cpu().numpy()
