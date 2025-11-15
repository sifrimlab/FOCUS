import tqdm, tifffile, os, subprocess, shlex
import sys, warnings, os, copy, cv2, shutil, json, time
import numpy as np
from readlif.reader import LifFile, LifImage
import xml.etree.ElementTree as ET
import ramanspy as rp
from skimage import exposure, morphology, measure
from scipy.ndimage import binary_fill_holes
from scipy.ndimage import distance_transform_edt
from joblib import Parallel, delayed
from sklearn.decomposition import PCA
import concurrent.futures

from constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED

class RamanMetadata:
	'''
	Store the metadata from a Raman Spectroscopy Imageing file regardless of the file format.
	'''
	def __init__(self):
		self._name: str = None
		self._index: int = None
		self._lambda_steps: int = None
		self._lambda_begin: float = None
		self._lambda_end: float = None
		self._scan_height: int = None
		self._scan_width: int = None
		self._laser_type: str = None
		self._lambda_stokes: float = None
		self._tile_number: int = None
		self._tiles_coordinates: np.ndarray[np.float32] = None
		self._pixel_size: np.ndarray[np.float32] = None

	@property
	def name(self) -> str:
		return self._name
	
	@name.setter
	def name(self, value: str):
		if not isinstance(value, str):
			raise TypeError("Name must be a string.")
		self._name = value

	@property
	def index(self) -> int:
		return self._index
	@index.setter
	def index(self, value: int):
		if not isinstance(value, int):
			raise TypeError("Index must be an integer.")
		if value < 0:
			raise ValueError("Index must be a non-negative integer.")
		self._index = value

	@property
	def lambda_steps(self) -> int:
		return self._lambda_steps
	
	@lambda_steps.setter
	def lambda_steps(self, value: int):
		if not isinstance(value, int):
			raise TypeError("Lambda steps must be an integer.")
		if value <= 0:
			raise ValueError("Lambda steps must be a positive integer.")
		self._lambda_steps = value

	@property
	def lambda_begin(self) -> float:
		return self._lambda_begin
	
	@lambda_begin.setter
	def lambda_begin(self, value: float):
		if not isinstance(value, (float, int)):
			raise TypeError("Lambda begin must be a float or an integer.")
		if value <= 0:
			raise ValueError("Lambda begin must be a positive number.")
		self._lambda_begin = float(value)

	@property
	def lambda_end(self) -> float:
		return self._lambda_end
	
	@lambda_end.setter
	def lambda_end(self, value: float):
		if not isinstance(value, (float, int)):
			raise TypeError("Lambda end must be a float or an integer.")
		if value <= 0:
			raise ValueError("Lambda end must be a positive number.")
		self._lambda_end = float(value)

	@property
	def scan_height(self) -> int:
		return self._scan_height
	
	@scan_height.setter
	def scan_height(self, value: int):
		if not isinstance(value, (int)):
			raise TypeError("Scan height must be an integer.")
		if value <= 0:
			raise ValueError("Scan height must be a positive number.")
		self._scan_height = int(value)

	@property
	def scan_width(self) -> int:
		return self._scan_width
	
	@scan_width.setter
	def scan_width(self, value: int):
		if not isinstance(value, (int)):
			raise TypeError("Scan width must be an integer.")
		if value <= 0:
			raise ValueError("Scan width must be a positive number.")
		self._scan_width = int(value)

	@property
	def laser_type(self) -> str:
		return self._laser_type
	@laser_type.setter
	def laser_type(self, value: str):
		if not isinstance(value, str):
			raise TypeError("Laser type must be a string.")
		self._laser_type = value

	@property
	def lambda_stokes(self) -> float:
		return self._lambda_stokes
	
	@lambda_stokes.setter
	def lambda_stokes(self, value: float):
		if not isinstance(value, (float, int)):
			raise TypeError("Lambda stokes must be a float or an integer.")
		if value <= 0:
			raise ValueError("Lambda stokes must be a positive number.")
		self._lambda_stokes = float(value)

	@property
	def tile_number(self) -> int:
		return self._tile_number
	@tile_number.setter
	def tile_number(self, value: int):
		if not isinstance(value, int):
			raise TypeError("Tile number must be an integer.")
		if value < 1:
			raise ValueError("Tile number must be a positive integer.")
		self._tile_number = value

	@property
	def tiles_coordinates(self) -> np.ndarray[np.float32]:
		return self._tiles_coordinates
	@tiles_coordinates.setter
	def tiles_coordinates(self, value: np.ndarray[np.float32]):
		if not isinstance(value, np.ndarray):
			raise TypeError("Tiles coordinates must be a numpy array.")
		if value.ndim != 2 or value.shape[1] != 2:
			raise ValueError("Tiles coordinates must be a 2D array with shape (N, 2).")
		self._tiles_coordinates = value.astype(np.float32)

	@property
	def pixel_size(self) -> np.ndarray[np.float32]:
		return self._pixel_size
	@pixel_size.setter
	def pixel_size(self, value: np.ndarray[np.float32]):
		if not isinstance(value, np.ndarray):
			raise TypeError("Pixel size must be a numpy array.")
		if value.ndim != 1 or value.shape[0] != 2:
			raise ValueError("Pixel size must be a 1D array with shape (2,).")
		self._pixel_size = value.astype(np.float32)

class RamanImage:
	def __init__(
			self,
			source_path: str,
			sample_id: str,
			modality_name: str,
			max_workers: int = 8
		):
		'''
		Wrapper to handle Raman Spectral Images. For now, it only supports Leica LIF files.
		Under the hood this class uses ramanspy and custom made methods to handle the data.

		Parameters
		----------
		input_path : str
			Path to the data source directory. If double_ion_mode is True, this should be the parent directory containing both ion mode subdirectories.
		sample_id : str
			Sample ID.
		modality_name : str
			Name of the modality.
		max_workers : int
			Maximum number of workers to use for parallel processing.
		'''

		# Check that the input path exists and it can be read
		if not os.path.exists(source_path):
			raise FileNotFoundError(f"Input path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise PermissionError(f"Input path {source_path} is not readable.")
		
		self.base_path = source_path
		self.source_path = os.path.join(source_path, sample_id, modality_name)
		self.sample_id = sample_id
		self.modality_name = modality_name
		self.output_path = os.path.join(source_path, sample_id, "preprocessing", modality_name)
		self._max_workers = max_workers

		# Define the intermediate data structure
		self._raw_tiles: np.ndarray[np.float32] = None
		self._basic_corrected_tiles: np.ndarray[np.float32] = None
		self._raman_corrected_tiles: np.ndarray[np.float32] = None
		self._quick_mosaic: np.ndarray[np.uint8] = None
		self._mosaic: np.ndarray[np.float32] = None
		self._metadata: RamanMetadata = []
		self._wavenumbers: np.ndarray[np.float32] = None
		self._tiles_coordinates: np.ndarray[np.float32] = None
		self._spectra_slices: list[tuple[int, int]] = []

	def load_source(self) -> None:
		'''
		Load the source data for the Raman image. This method looks for supported file formats inside the source directory.
		The first supported file found is loaded.
		'''
		# Check if the output_path exists, otherwise create it
		os.makedirs(self.output_path, exist_ok=True)

		# Look for the Raman file inside the source directory
		found = False
		with os.scandir(self.source_path) as it:
			for entry in it:
				if entry.is_file():
					if entry.name.endswith('.lif') or entry.name.endswith('.LIF'):
						print(f"1/5 - Loading Raman data from LIF file: {entry.name}")
						self._load_lif(os.path.join(self.source_path, entry.name))
						found = True
						break
		
		if not found:
			raise FileNotFoundError(f"Impossible to identify a valid Raman source file in {self.source_path}")
	
	@property
	def raw(self) -> np.ndarray[np.float32]:
		'''
		Get the raw tiles from the Raman Spectroscopy Imageing file.

		Returns
		-------
		np.ndarray[np.float32]
			Numpy array with shape (T, C, Y, X) where T is the number of tiles, C is the number of channels (1 for Raman), Y is the scan height, and X is the scan width.
		'''

		return self._raw_tiles
	
	@property
	def corrected(self) -> np.ndarray[np.float32]:
		'''
		Get the corrected tiles (BaSiC + Background removal + Ramanspy pipeline)

		Returns
		-------
		np.ndarray[np.float32]
			Numpy array with shape (T, C, Y, X) where T is the number of tiles, C is the number of channels (1 for Raman), Y is the scan height, and X is the scan width.
		'''
		
		return self._raman_corrected_tiles
	
	@property
	def mosaic(self) -> np.ndarray[np.float32]:
		'''
		Get the final stitched mosaic of corrected tiles

		Returns
		-------
		np.ndarray[np.float32]
			Numpy array with shape (C, Y, X) C is the number of channels (1 for Raman), Y is the scan height, and X is the scan width.
		'''
		
		return self._mosaic
	
	@property
	def metadata(self) -> RamanMetadata:
		'''
		Get the metadata from the Raman Spectroscopy Imageing file.

		Returns
		-------
		list[RamanMetadata]
			List of metadata objects.
		'''
		return self._metadata
	
	@property
	def wavenumbers(self) -> np.ndarray[np.float32]:
		'''
		Get the wavenumbers from the Raman Spectroscopy Imageing file.

		Returns
		-------
		np.ndarray[np.float32]
			Numpy arrays of wavenumbers with shape (W, ).
		'''
		return self._wavenumbers
	
	@property
	def tiles_coordinates(self) -> np.ndarray[np.float32]:
		'''
		Get the (X, Y) coordinates of each tile in the Raman Spectroscopy Imageing file.

		Returns
		-------
		np.ndarray[np.float32]
			Numpy arrays of coordinates with shape (T, 2) where T is the number of tiles and each row contains the (X, Y) coordinates of the tile.
		'''
		return self._tiles_coordinates

	@property
	def sample_id(self) -> str:
		'''
		Get the sample ID.

		Returns
		-------
		str
			Sample ID.
		'''
		return self._sample_id

	@sample_id.setter
	def sample_id(self, value: str):
		if not isinstance(value, str):
			raise TypeError("Sample ID must be a string.")
		self._sample_id = value

	def _load_lif(self, filename: str) -> None:
		'''
		Load Raman Spectroscopy Imageing data from a Leica LIF file.

		Parameters
		----------
		file : str
			Path to the LIF file.
		'''
		
		lif_file = LifFile(filename)
		metadata_dict: dict[str, RamanMetadata] = self._parse_lif_metadata(lif_file)

		# Define a reference metadata used for the global object
		reference_metadata = RamanMetadata()
		reference_metadata.name = "reference"
		reference_metadata.index = 0

		# Define a temporary raw tiles and wavenumbers dictionaries
		raw_tiles: dict[str, np.ndarray[np.float32]] = {}
		wavenumbers: dict[str, np.ndarray[np.float32]] = {}
		coordinates: dict[str, np.ndarray[np.float32]] = {}
		pixel_size: dict[str, np.ndarray[np.float32]] = {}

		# Iterate over the images following the order written in metadata
		for name, metadata in metadata_dict.items():

			# Extract only tiled images, ignore automatic stitching
			if metadata.tile_number is None or metadata.tile_number < 2:
				continue

			# Check if this image is corrupted (missing metadata)
			if None in [metadata.tile_number, metadata.lambda_steps, metadata.scan_width, metadata.scan_height]:
				print(f"Warning: Image '{name}' is missing required metadata. Probably corrupted scan")
				continue

			# Read the image
			image: LifImage = lif_file.get_image(metadata.index)
			if image is None:
				raise ValueError(f"Image with index {metadata.index} not found in the LIF file.")

			# Initialize a list to hold the tiles
			raw_tiles[name] = np.zeros((metadata.tile_number, metadata.lambda_steps, metadata.scan_width, metadata.scan_height), dtype=np.float32)

			# Read the tiles
			for tile_idx in range(metadata.tile_number):
				# For each tile, iterate over the spectral dimensions
				for spectral_idx in range(metadata.lambda_steps):
					# Read the image data for each lambda step
					plane = image.get_plane(display_dims=(1, 2), c = 0, requested_dims = {9: spectral_idx, 10: tile_idx})
					raw_tiles[name][tile_idx, spectral_idx, :, :] = plane

			# Record this spectra slice for later processing
			if len(self._spectra_slices) == 0:
				self._spectra_slices.append((0, metadata.lambda_steps - 1))
			else:
				self._spectra_slices.append((self._spectra_slices[-1][1] + 1, self._spectra_slices[-1][1] + metadata.lambda_steps))

			# Compute the wavenumbers based on the 
			wavenumbers[name] = self._compute_wavenumbers(
				metadata.lambda_begin, 
				metadata.lambda_end, 
				metadata.lambda_steps, 
				metadata.lambda_stokes
			)

			# Store the coordinates of the tiles
			if metadata.tiles_coordinates is not None:
				coordinates[name] = metadata.tiles_coordinates
			else:
				raise ValueError(f"Tiles coordinates not found for image '{name}' in the LIF file. Please ensure the metadata is complete.")
			
			# Store the pixel size
			if metadata.pixel_size is not None and np.all(metadata.pixel_size > 0):
				pixel_size[name] = metadata.pixel_size
			else:
				raise ValueError(f"Pixel size not found or invalid for image '{name}' in the LIF file. Please ensure the metadata is complete.")

		# Once all the tiles are loaded, merge them into a single high-dimensional array
		stacked_tiles = np.concatenate([raw_tiles[name] for name in raw_tiles], axis = 1)
		stacked_wavenumbers = np.concatenate([wavenumbers[name] for name in wavenumbers], axis = -1)
		coordinates = np.stack([coordinates[name] for name in coordinates], axis = 1)
		pixel_size = np.stack([pixel_size[name] for name in pixel_size], axis = 0)
		pixel_size = pixel_size.mean(axis = 0)

		# Check if the wavenumbers have overlaps to detect re-scanned regions
		break_idx, closest_idx = self._check_wavenumbers_overlaps(stacked_wavenumbers)

		# If there is an overlap, remove the dead region
		if break_idx is not None and closest_idx is not None:
			print(f"Detected overlapping wavenumbers at index {break_idx} - ({stacked_wavenumbers[break_idx]}). Removing overlapping region starting from index {closest_idx} - ({stacked_wavenumbers[closest_idx]}).")
			stacked_wavenumbers = np.concatenate([stacked_wavenumbers[:closest_idx], stacked_wavenumbers[break_idx:]], axis = 0)
			stacked_tiles = np.concatenate([stacked_tiles[:, :closest_idx, :, :], stacked_tiles[:, break_idx:, :, :]], axis = 1)

			# Identify the spectra slices involved in the overlap
			break_slice, overlap_slice = None, None
			for slice_index, (slice_start, slice_end) in enumerate(self._spectra_slices):
				if break_idx >= slice_start and break_idx <= slice_end:
					break_slice = slice_index

				if closest_idx >= slice_start and closest_idx <= slice_end:
					overlap_slice = slice_index

			new_slices: list[tuple[int, int]] = []
			for index, slice in enumerate(self._spectra_slices):
				if index < overlap_slice:							# If the index is before the overlap slice, keep it as is
					new_slices.append(slice)
				elif index == overlap_slice:						# If this is the overlap slice, truncate it at the closest_idx
					new_slices.append((slice[0], int(closest_idx - 1)))
				elif index == break_slice:							# If this is the break slice, shift it to start from closest_idx
					new_slices.append((int(closest_idx), int(slice[1] - (break_idx - closest_idx))))
				else:												# For all other slices after the break slice, shift them accordingly
					shift = break_idx - closest_idx
					new_slices.append((int(slice[0] - shift), int(slice[1] - shift)))

			self._spectra_slices = new_slices

		# Rescale the whole 4D object to float32 (Assume it was originally in uint8)
		if stacked_tiles.max() > 1.0 and stacked_tiles.max() <= 255.0:
			stacked_tiles = stacked_tiles.astype(np.float32) / 255.0
		elif stacked_tiles.max() > 1.0 and stacked_tiles.max() <= 65535.0:
			stacked_tiles = stacked_tiles.astype(np.float32) / 65535.0
		else:
			raise ValueError("Expected input data to be either in the range [0, 255] or [0, 65535]. Please check the input data format.")
		
		# Update the metadata
		reference_metadata.scan_height = stacked_tiles.shape[-2]
		reference_metadata.scan_width = stacked_tiles.shape[-1]
		reference_metadata.tile_number = stacked_tiles.shape[0]
		reference_metadata.lambda_steps = stacked_tiles.shape[1]
		reference_metadata.tiles_coordinates = coordinates[0]
		reference_metadata.pixel_size = pixel_size

		# Overwrite the raw tiles and wavenumbers with the stacked ones
		self._raw_tiles = stacked_tiles
		self._wavenumbers = stacked_wavenumbers
		self._metadata = reference_metadata
		self._tiles_coordinates = coordinates

	def _check_wavenumbers_overlaps(self, wavenumbers: np.ndarray[np.float32]) -> tuple[int, int] | tuple[None, None]:
		if len(wavenumbers) < 2:
			return [None, None]
		
		# Check overall monotony
		is_asc = np.all(wavenumbers[:-1] <= wavenumbers[1:])
		is_desc = np.all(wavenumbers[:-1] >= wavenumbers[1:])
		if is_asc or is_desc:
			return [None, None]  # monotone, no break
		
		# Determine expected direction from start (assumes mostly monotone beginning)
		expected_asc = wavenumbers[1] >= wavenumbers[0]
		
		# Find break index
		break_idx = None
		for i in range(1, len(wavenumbers)):
			if expected_asc and wavenumbers[i] < wavenumbers[i-1]:
				break_idx = i
				break
			elif not expected_asc and wavenumbers[i] > wavenumbers[i-1]:
				break_idx = i
				break
		
		if break_idx is None:
			return [None, None]  # No break found
		
		# Find closest index on left (0 to break_idx-1) to value at break_idx
		target_val = wavenumbers[break_idx]
		left_slice = wavenumbers[:break_idx]
		closest_idx = np.argmin(np.abs(left_slice - target_val))
		
		return [break_idx, closest_idx]
	
	def _parse_lif_metadata(self, lif: LifFile) -> dict[str, RamanMetadata]:
	
		# Obtain the XML root element from the LifFile object
		root = lif.xml_root

		# Obtain a list of Raman Scans
		top_level_elements = root.findall('./Element')
		if not top_level_elements:
			top_level_elements = root.findall('.')
			if root.tag != 'Element':
					top_level_elements = root.findall('.//Element')

		# Filter out the elements that do not represent scans
		elements_to_process = []
		for top_element in top_level_elements:
			children_tag = top_element.find('Children')
			if children_tag is not None:
				for image_element in children_tag.findall('Element'):
					elements_to_process.append(image_element)
			else:
					if top_element.find('./Data/Image') is not None:
						elements_to_process.append(top_element)

		# Fallback in case the XML structure is collapsed to a single level
		if not elements_to_process and root.tag == 'LMSDataContainerHeader':
			root_children_tag = root.find('Children')
			if root_children_tag is not None:
				for image_element in root_children_tag.findall('Element'):
					elements_to_process.append(image_element)
			elif root.find('./Element/Data/Image') is not None :
					for image_element in root.findall('Element'):
						if image_element.find('./Data/Image') is not None:
							elements_to_process.append(image_element)

		# No elements to process or unexpected structure
		if not elements_to_process:
			raise ValueError("No elements found in the LIF file or unexpected XML structure.")

		result: dict[str, RamanMetadata] = {}

		# Iterate over each element to extract metadata
		for i, element in enumerate(elements_to_process):
			element_name = element.get('Name', f"Unnamed Element {i+1}")
			data_image_tag = element.find('./Data/Image')
			
			metadata = RamanMetadata()
			metadata.name = element_name
			metadata.index = i
			metadata.pixel_size = np.array([0, 0], dtype=np.float32)
			dimension_scaling_factor = [None, None]

			# Get the dimensions
			if data_image_tag is not None:
				image_description = data_image_tag.find('ImageDescription')
				if image_description is not None:                
					dimensions = image_description.find('Dimensions')
					if dimensions is not None and len(list(dimensions)) > 0:
						for dim_desc in dimensions.findall('DimensionDescription'):
							id = int(dim_desc.get('DimID', None))
							size = int(dim_desc.get('NumberOfElements', None))
							length = float(dim_desc.get('Length', None))
							unit = dim_desc.get('Unit', None)

							# Interpret the dimension ID for standard Leica LIF files
							if id == 1:
								metadata.scan_height = size

								if unit == 'm':
									dimension_scaling_factor[0] = 1e6
								elif unit == 'um':
									dimension_scaling_factor[0] = 1e0
								else:
									raise ValueError(f"Unexpected unit '{unit}' for pixel size in image '{element_name}'. Expected 'm' or 'um'.")
								
								metadata.pixel_size[0] = (length * dimension_scaling_factor[0]) / size
							elif id == 2:
								metadata.scan_width = size

								if unit == 'm':
									dimension_scaling_factor[1] = 1e6
								elif unit == 'um':
									dimension_scaling_factor[1] = 1e0
								else:
									raise ValueError(f"Unexpected unit '{unit}' for pixel size in image '{element_name}'. Expected 'm' or 'um'.")
								
								metadata.pixel_size[1] = (length * dimension_scaling_factor[1]) / size
							elif id == 9:
								metadata.lambda_steps = size
							elif id == 10:
								metadata.tile_number = size

			# Define potential paths for ATLConfocalSettingDefinition
			atl_confocal_paths = [
				'./Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition',
				'./Data/Image/Attachment[@Name="HardwareSetting"]/LDM_Block_Sequential/LDM_Block_Sequential_Master/ATLConfocalSettingDefinition'
			]

			found_lambda, found_stokes = False, False

			# Extract Begin and End for Lambda Steps + Lambda Stokes (constant)
			for _, atl_path in enumerate(atl_confocal_paths):
				atl_confocal_setting_def = element.find(atl_path)
				if atl_confocal_setting_def is not None:
					if not found_lambda: 
						lambda_definition = atl_confocal_setting_def.find('LambdaDefinition')
						if lambda_definition is not None and len(list(lambda_definition)) > 0:
							lambda_excitation = lambda_definition.find('LambdaExcitation')
							
							if lambda_excitation is not None:
								metadata.lambda_begin = float(lambda_excitation.get('LambdaExcitationBeginDouble', lambda_excitation.get('LambdaExcitationBegin', None)))
								metadata.lambda_end = float(lambda_excitation.get('LambdaExcitationEndDouble', lambda_excitation.get('LambdaExcitationEnd', None)))
								found_lambda = True

					# Extract Lambda Stokes (Pump Wavelength)
					if not found_stokes:
						laser_array = atl_confocal_setting_def.find('LaserArray')
						if laser_array is not None:
							lasers_with_pumpwavelength = []
							for laser_tag in laser_array.findall('Laser'):
								pump_wavelength = laser_tag.get('PumpWavelength')
								if pump_wavelength is not None:
									laser_name = laser_tag.get('LaserName', 'Unknown Laser')
									lasers_with_pumpwavelength.append(
										f"<Laser Name='{laser_name}' PumpWavelength='{pump_wavelength}' />"
									)
							
							if lasers_with_pumpwavelength:
								for entry in lasers_with_pumpwavelength:
									# Parse the entry string into an XML element
									entry_element = ET.fromstring(entry)
									metadata.laser_type = entry_element.get('Name', None)
									metadata.lambda_stokes = float(entry_element.get('PumpWavelength', None))
									found_stokes = True
				if found_lambda and found_stokes:
					break
			
			# If the image represents a tile scan, extract info about the tiles
			if metadata.tile_number is not None and metadata.tile_number > 1:
				tiles = element.findall('./Data/Image/Attachment[@Name="TileScanInfo"]/Tile')
				metadata.tiles_coordinates = np.ndarray((metadata.tile_number, 2), dtype=np.float32)

				if len(tiles) != metadata.tile_number:
					print(f"RuntimeWarning: For element {element_name}, expected {metadata.tile_number} tiles, but found {len(tiles)} tiles in the metadata. This element is ignored (likely a broken scan)")
					continue

				for tile_index, tile in enumerate(tiles):
					metadata.tiles_coordinates[tile_index] = ((float(tile.get('PosX')) * dimension_scaling_factor[0], float(tile.get('PosY')) * dimension_scaling_factor[1]))

			# Append the metadata to the list
			result[metadata.name] = metadata

		return result

	def _compute_wavenumbers(self, lambda_begin: float, lambda_end: float, lambda_steps: float, lamnda_stokes: float) -> np.ndarray[np.float32]:
		'''
		Compute the wavenumbers array based on the LIF File metadata
		
		Parameters
		----------
		lambda_begin : float
			The starting wavelength in nm.
		lambda_end : float
			The ending wavelength in nm.
		lambda_steps : float
			The number of steps in the wavelength range.
		lambda_stokes : float
			The Stokes wavelength in nm.
		'''

		# Compute the laser pump wavelength
		pump_wavelength = np.linspace(lambda_begin, lambda_end, lambda_steps)

		# Compute the Raman wavenumbers
		raman_wavenumbers = ((1 / pump_wavelength) - (1 / lamnda_stokes)) * 1e7

		return raman_wavenumbers

	def _zero_variance_spectra(self, spectra_array: np.ndarray[np.float32]) -> np.ndarray[np.bool_]:
		'''
		Compute the variance across the spectra to identify zero-variance datapoints.
		These datapoints would produce numerical errors in downstream processing so they should be
		removed.

		Parameters
		----------
		spectra_array : np.ndarray[np.float32]
			The spectra array to compute the variance across. With shape (N, spectra_length)

		Returns
		----------
		np.ndarray[np.bool]
			A boolean array of the same shape as the input array, where True indicates a zero-variance
			spectrum.
		'''

		forward_differences = np.diff(spectra_array, axis = -1)
		mad = np.median([np.abs(forward_differences - np.median(forward_differences, axis = -1, keepdims=True))], axis = -1)
		
		return np.array(mad == 0, dtype=np.bool_).squeeze()

	def _process_tile_parallel(self, tile: np.ndarray[np.float32], wavenumbers: np.ndarray[np.float32], tile_index: int, slice_index: int) -> tuple[np.ndarray, int, int]:

		with warnings.catch_warnings():
			warnings.simplefilter("ignore", RuntimeWarning)
			# Define the processing pipeline applied to each pixel (first two steps are per tile)
			pipeline = rp.preprocessing.Pipeline([
				rp.preprocessing.despike.WhitakerHayes(),
				rp.preprocessing.denoise.SavGol(window_length=7, polyorder=3),
				rp.preprocessing.baseline.IASLS(),
				rp.preprocessing.normalise.MinMax()
			])

			# Create a coordinate grid to keep track of the valid pixels
			C, X, Y = tile.shape
			y_indices, x_indices = np.meshgrid(np.arange(X), np.arange(Y))
			coordinates = np.stack((x_indices, y_indices), axis=-1)

			# Reshape the tile and the coordinates to 2D
			reshaped_tile = tile.reshape(tile.shape[0], -1)			# Reshape to (C, Y*X)
			reshaped_tile = reshaped_tile.transpose((1, 0))			# Transpose to (Y*X, C)
			reshaped_coordinates = coordinates.reshape(-1, 2)

			# Compute the zero-variance spectra mask
			zero_variance_mask = self._zero_variance_spectra(reshaped_tile)
			reshaped_tile = reshaped_tile[~zero_variance_mask]
			reshaped_coordinates = reshaped_coordinates[~zero_variance_mask]

			# Create a SpectralImage object
			spectral_image = rp.SpectralImage(reshaped_tile, wavenumbers)

			# Apply the pipeline to the reshaped tile
			if reshaped_tile.shape[0] == 0:
				# No valid spectra to process
				return np.zeros_like(tile), tile_index, slice_index

			processed_tile = pipeline.apply(spectral_image)
			processed_tile = processed_tile.spectral_data

			# Reshape the processed tile back to its original shape
			restored_tile = np.zeros((C, X, Y), dtype=np.float32)

			for i in range(processed_tile.shape[0]):
				x = int(reshaped_coordinates[i, 0])
				y = int(reshaped_coordinates[i, 1])
				restored_tile[:, x, y] = processed_tile[i, :]

			return restored_tile, tile_index, slice_index

	def process_raw_tiles(self, wavenumbers: np.ndarray[np.float32] | None = None, parallel: bool = True, force_recomputing: bool = False) -> None:
		'''
		Process the raw tiles using the ramanspy library.
		This method can process the tiles in parallel (active by default) or sequentially.
		
		Parameters
		----------
		wavenumbers : np.ndarray[np.float32] | None, optional
			The wavenumbers to use for the processing. If None, the wavenumbers from the metadata will be used.
			Default is None.

		parallel : bool, optional
			If True, the tiles will be processed in parallel using multiprocessing. If False, the tiles will be processed sequentially.
			Default is True.
		force_recomputing : bool, optional
			If True, the processing will be forced even if the processed tiles already exist
		'''

		if type(parallel) is not bool or type(force_recomputing) is not bool:
			raise TypeError("parallel and force_recomputing must be booleans.")
		
		if force_recomputing == True or os.path.exists(os.path.join(self.output_path, "raman_corrected_tiles.npy")) == False:
			if self._basic_corrected_tiles is None:
				raise ValueError("No raw tiles to process. Please load the data first.")
			
			if wavenumbers is None:
				wavenumbers = self.wavenumbers

			self._raman_corrected_tiles = np.zeros_like(self._basic_corrected_tiles, dtype=np.float32)
			
			if parallel == True:

				# Process non-contiguous spectra slices independently
				units = []

				for slice_idx, (start_channel, end_channel) in enumerate(self._spectra_slices):
					for tile_idx in range(self._basic_corrected_tiles[:, start_channel:end_channel + 1, :, :].shape[0]):
						units.append(
							(
								self._basic_corrected_tiles[tile_idx, start_channel:end_channel + 1, :, :], 
								wavenumbers[start_channel:end_channel + 1], 
								tile_idx,
								slice_idx
							)
						)

				slice_result = list(
					tqdm.tqdm(
						Parallel(n_jobs=self._max_workers, return_as="generator")(
							delayed(self._process_tile_parallel)(*unit_args) for unit_args in units
						),
						total=len(units),
						desc="4/5 - Cleaning Raman Spectra (Parallel)",
						unit="tile"
					)
				)

				# Store processed tiles as before
				for processed_tile, tile_index, slice_index in slice_result:
					start_channel, end_channel = self._spectra_slices[slice_index]
					self._raman_corrected_tiles[tile_index, start_channel:end_channel + 1] = processed_tile

			else:
				for tile_idx in tqdm.tqdm(range(self._basic_corrected_tiles.shape[0]), desc="4/5 - Cleaning Raman Spectra"):
					for slice_index, (start_channel, end_channel) in enumerate(self._spectra_slices):
						wavenumbers_slice = wavenumbers[start_channel:end_channel + 1]
						tile_slice = self._basic_corrected_tiles[tile_idx, start_channel:end_channel + 1, :, :]

						# Process the tile
						processed_tile, _ = self._process_tile_parallel(tile_slice, wavenumbers_slice, tile_idx, slice_index)
						self._raman_corrected_tiles[tile_idx, start_channel:end_channel + 1] = processed_tile

			# Save the processed tiles
			np.save(os.path.join(self.output_path, "raman_corrected_tiles.npy"), self._raman_corrected_tiles)
		else:
			# Load the processed tiles
			self._raman_corrected_tiles = np.load(os.path.join(self.output_path, "raman_corrected_tiles.npy"))
			print("4/5 - Loaded Clean Raman Spectra from disk. (Using cached results)")

	def basic_correct(self, force_recomputing: bool = False) -> None:
		'''
		Apply the BaSiC correction to the raw tiles.
		This method uses the BaSiC algorithm to correct the raw tiles for background and noise.
		'''

		if type(force_recomputing) is not bool:
			raise TypeError("force_recomputing must be a boolean.")
		
		# Recompute the BaSiC only if needed
		if force_recomputing or not os.path.exists(os.path.join(self.output_path, "basic_corrected_tiles.npy")):
			if self._raw_tiles is None:
				raise ValueError("No raw tiles to correct. Please load the data first.")

			# Get the tools absolute path
			tools_basedir = os.path.abspath(__file__).replace("src/preprocessing/raman.py", "tools")

			# Check if conda env 'FOCUS_BaSiCpy' exists
			if shutil.which("conda") is None:
				raise RuntimeError("conda command not found. Make sure conda is installed and in PATH.")
			env_name = "FOCUS_BaSiCpy"
			# List conda envs and check presence
			result = subprocess.run(["conda", "env", "list", "--json"], capture_output=True, text=True)
			if result.returncode != 0:
				raise RuntimeError(f"Failed to list conda environments: {result.stderr}")

			envs_info = json.loads(result.stdout)
			env_paths = envs_info.get("envs", [])
			env_exists = any(env_name in path for path in env_paths)
			if not env_exists:
				raise RuntimeError(f"Conda environment '{env_name}' does not exist. Please create it before running.")

			main_script = os.path.join(tools_basedir, "BaSiCpy", "main.py")
			if not os.path.isfile(main_script):
				raise FileNotFoundError(f"BaSiCpy main script not found at {main_script}")

			def run_correction(channel_idx: int):
				input_file = os.path.join(self.output_path, f"basic_input_{channel_idx}.npy")
				output_file = os.path.join(self.output_path, f"basic_output_{channel_idx}.npy")
				np.save(input_file, self._raw_tiles[:, channel_idx, :, :])

				# Create a copy of current environment and add JAX_PLATFORM_NAME=cpu
				env = os.environ.copy()
				env["JAX_PLATFORM_NAME"] = "cpu"

				subprocess.run([
					"conda", "run", "-n", env_name, "python", main_script, self.output_path, str(channel_idx)
				], check=True, env=env)

				# Wait for the output file to be created
				timeout = 10
				poll_interval = 0.2
				start = time.time()
				while True:
					if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
						break
					if time.time() - start > timeout:
						raise TimeoutError(f"Output file {output_file} not created or empty after {timeout}s")
					time.sleep(poll_interval)

				corrected = np.load(output_file)

				os.remove(input_file)
				os.remove(output_file)
				return channel_idx, corrected

			self._basic_corrected_tiles = np.zeros_like(self._raw_tiles, dtype=np.float32)

			with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
				futures = {executor.submit(run_correction, idx): idx for idx in range(self._raw_tiles.shape[1])}
				for future in tqdm.tqdm(concurrent.futures.as_completed(futures), total=len(futures), desc="2/5 - Applying BaSiC Correction", unit='channel'):
					channel_idx, corrected_channel = future.result()
					self._basic_corrected_tiles[:, channel_idx, :, :] = corrected_channel

			# Global normalization
			self._basic_corrected_tiles -= np.min(self._basic_corrected_tiles)
			self._basic_corrected_tiles /= np.max(self._basic_corrected_tiles)

			# Save corrected tiles
			np.save(os.path.join(self.output_path, "basic_corrected_tiles.npy"), self._basic_corrected_tiles)

		else:
			self._basic_corrected_tiles = np.load(os.path.join(self.output_path, "basic_corrected_tiles.npy"))
			print("2/5 - Loaded BaSiC corrected tiles from disk. (Using cached results)")

	def remove_background(self, force_recomputing: bool = False) -> None:
		'''
		Remove the background from BaSiC corrected tiles using Meta SAM2.
		The tiles are stitched into a mosaic using an approximate method, then the background is removed using Meta SAM2,
		and finally the tiles are extracted back from the background-removed mosaic.

		Parameters
		----------
		force_recomputing : bool, optional
			If True, the background removal will be forced even if the background-removed tiles already exist
		'''

		# Check if basic corrected tiles are available
		if self._basic_corrected_tiles is None:
			raise RuntimeError("No BaSiC corrected tiles to remove background from. Please run basic_correct() first.")
		
		if type(force_recomputing) is not bool:
			raise TypeError("force_recomputing must be a boolean.")
		
		if force_recomputing == True or os.path.exists(os.path.join(self.output_path, "segmented_tiles.npy")) == False:
			print("3/5 - Removing background from BaSiC corrected tiles")
			
			# Quick stitch the tiles into a mosaic
			self._quick_stitch()
			
			# Clip intensities at 95th percentile to reduce oversaturation impact
			clip_value = np.percentile(self._quick_mosaic, 95)
			clipped_img = np.clip(self._quick_mosaic, None, clip_value).astype(np.uint8)  # clip and convert to uint8 if needed

			# Compute Otsu threshold on clipped image
			otsu_thresh, _ = cv2.threshold(clipped_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

			# Adjust threshold (e.g., reduce by 30%)
			adjusted_thresh = int(otsu_thresh * 0.7)

			# Threshold original image with adjusted threshold (no scaling needed)
			_, thresh = cv2.threshold(self._quick_mosaic, adjusted_thresh, 255, cv2.THRESH_BINARY)

			# Remove small objects and fill holes
			mask_clean = morphology.remove_small_objects(thresh.astype(bool), min_size=500)
			segmentation_mask = binary_fill_holes(mask_clean)

			# Convert mask for contour finding
			seg_mask_uint8 = segmentation_mask.astype(np.uint8) * 255

			# Find contours
			contours, _ = cv2.findContours(seg_mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

			if contours:
				# Total image area (number of pixels)
				image_area = seg_mask_uint8.shape[0] * seg_mask_uint8.shape[1]

				# Threshold at 5% of the image area
				area_threshold = 0.05 * image_area

				# Initialize empty mask
				tissue_mask = np.zeros_like(seg_mask_uint8)

				# Filter contours by area relative to image size
				large_contours = [c for c in contours if cv2.contourArea(c) >= area_threshold]

				# Draw all large contours in the mask
				cv2.drawContours(tissue_mask, large_contours, contourIdx=-1, color=255, thickness=cv2.FILLED)

				# Convert to boolean mask
				segmentation_mask = tissue_mask.astype(bool)
			else:
				print("Warning: No contours found; cannot refine background mask.")

			# Extract tile segments from the global segmentation mask
			tiles_segmentation_masks = self._extract_tiles_segmentation_from_mosaic(
				mosaic = segmentation_mask, 
				coordinates = self._tiles_coordinates, 
				pixel_size = self.metadata.pixel_size[0], 
				tile_size = (self.metadata.scan_height, self.metadata.scan_width)
			)

			# Apply the segmentation masks to the BaSiC corrected tiles
			segmented_tiles = self._basic_corrected_tiles * tiles_segmentation_masks

			# Save the segmented tiles
			np.save(os.path.join(self.output_path, "segmented_tiles.npy"), segmented_tiles)
			self._basic_corrected_tiles = segmented_tiles
		else:
			# Load the segmented, BaSiC corrected tiles
			self._basic_corrected_tiles = np.load(os.path.join(self.output_path, "segmented_tiles.npy"))
			print("3/5 - Loaded segmented BaSiC corrected tiles from disk. (Using cached results)")

	def _quick_stitch(self) -> None:
		"""
		Stitch the BaSiC corrected tiles into a mosaic for background removal.
		This mosaic is not intended to be used for any other purpose, as it incorporates misaligment artifacts.
		"""

		# Use only an approximation of the coordinates for the mosaic
		if self._basic_corrected_tiles is None:
			raise RuntimeError("No BaSiC corrected tiles to stitch. Please run basic_correct() first.")
		tiles = self._basic_corrected_tiles

		# Convert coordinates to pixel positions (x, y)
		coords_px = np.zeros_like(self._tiles_coordinates, dtype=int)
		for slice_idx, _ in enumerate(self._spectra_slices):
			coords_px[:, slice_idx, :] = (self._tiles_coordinates[:, slice_idx, :] / self.metadata.pixel_size[0]).astype(int)
		
		# Calculate mosaic dimensions (width, height)
		min_x, min_y = np.min(coords_px.reshape(-1, 2), axis=0)
		max_x = np.max(coords_px[:, :, 0] + tiles.shape[3])
		max_y = np.max(coords_px[:, :, 1] + tiles.shape[2])
		
		mosaic_width = max_x - min_x
		mosaic_height = max_y - min_y
		mosaic_shape = (tiles.shape[1], mosaic_height, mosaic_width)
		
		# Create accumulation arrays
		mosaic = np.zeros(mosaic_shape, dtype=np.float32)
		weights = np.zeros(mosaic_shape, dtype=np.float32)

		# Create blending weights using distance transform
		def create_blend_weights(tile_height, tile_width):
			weights = np.ones((tile_height, tile_width), dtype=np.float32)
			weights = distance_transform_edt(weights)
			weights /= np.max(weights)
			return weights

		tile_weights = create_blend_weights(tiles.shape[2], tiles.shape[3])
		
		# Place each tile in the mosaic
		for t in range(tiles.shape[0]):
			tile_height, tile_width = tiles.shape[2], tiles.shape[3]
			
			for c in range(tiles.shape[1]):
				# Find the corresponding spectra slice for this channel
				for slice_idx, (start_channel, end_channel) in enumerate(self._spectra_slices):
					if start_channel <= c <= end_channel:
						x, y = coords_px[t, slice_idx, :] - [min_x, min_y]
						break
				
				y_start = max(0, y)
				y_end = min(mosaic_height, y + tile_height)
				x_start = max(0, x)
				x_end = min(mosaic_width, x + tile_width)

				tile_y_start = max(0, -y)
				tile_y_end = tile_height - max(0, y + tile_height - mosaic_height)
				tile_x_start = max(0, -x)
				tile_x_end = tile_width - max(0, x + tile_width - mosaic_width)

				mosaic[c, y_start:y_end, x_start:x_end] += (
					tiles[t, c, tile_y_start:tile_y_end, tile_x_start:tile_x_end] *
					tile_weights[tile_y_start:tile_y_end, tile_x_start:tile_x_end]
				)
				weights[c, y_start:y_end, x_start:x_end] += tile_weights[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

		# Normalize mosaic by accumulated weights
		mosaic = np.divide(mosaic, weights, where=weights > 0)

		# Compute the principal component of the mosaic to convert it to grayscale
		pca = PCA(n_components=1)
		mosaic = mosaic.transpose((1, 2, 0))  # Reshape to (H, W, C)
		H, W, C = mosaic.shape
		mosaic_reshaped = mosaic.reshape(-1, C)  # Reshape to

		# Create a mask to remove black pixels from the computation
		filter_mask = np.all(mosaic_reshaped == 0, axis=1)
		if np.all(filter_mask):
			raise RuntimeError("The mosaic is completely black. Please check the input tiles and coordinates.")

		mosaic_pca = pca.fit_transform(mosaic_reshaped[~filter_mask])

		# After PCA fit_transform and reshape, apply percentile clipping for better contrast
		p2, p98 = np.percentile(mosaic_pca, (2, 98))
		mosaic_pca_clip = np.clip(mosaic_pca, p2, p98)
		mosaic_pca_norm = (mosaic_pca_clip - p2) / (p98 - p2)

		# Reshape back and convert to uint8
		mosaic_reshaped = np.zeros((H * W, 1), dtype=mosaic_pca_norm.dtype)
		mosaic_reshaped[~filter_mask] = mosaic_pca_norm
		mosaic = mosaic_reshaped.reshape(H, W)

		# Apply CLAHE for improved localized contrast
		mosaic_uint8 = (mosaic * 255).astype(np.uint8)
		clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
		mosaic_enhanced = clahe.apply(mosaic_uint8)
		self._quick_mosaic = mosaic_enhanced.astype(np.uint8)

	def _extract_tiles_segmentation_from_mosaic(self, mosaic: np.ndarray[np.float32], coordinates: np.ndarray[np.float32], pixel_size: float, tile_size: tuple[int, int]) -> np.ndarray[np.float32]:
		"""
		Extracts tiles segmentation masks from mosaic segmentation mask (invert the quick_stitch method).

		Parameters
		----------
		mosaic : np.ndarray[np.float32]
			Global segmentation mask of the mosaic.
		coordinates : np.ndarray[np.float32]
			The (X, Y) coordinates of each tile for each scan: shape [n_tiles, n_slices, 2].
		pixel_size : float
			The size of a pixel in the original image.
		tile_size : tuple[int, int]
			The size of each tile in pixels (height, width).
		"""
		tile_h, tile_w = tile_size
		n_tiles = coordinates.shape[0]
		n_slices = coordinates.shape[1]
		n_channels = sum(e - s + 1 for s, e in self._spectra_slices)

		# Convert coordinates to pixel positions (integer)
		coords_px = (coordinates / pixel_size).astype(int)

		# Calculate coordinate shifts globally (using all scans)
		min_x = np.min(coords_px[:, :, 0])
		min_y = np.min(coords_px[:, :, 1])
		coords_px[:, :, 0] -= min_x
		coords_px[:, :, 1] -= min_y

		H, W = mosaic.shape

		# Initialize output array: all tiles, all channels
		tiles = np.zeros((n_tiles, n_channels, tile_h, tile_w), dtype=mosaic.dtype)

		# Flatten spectra slices to channel->scan map for faster lookup
		channel_to_scan = {}
		ch_idx = 0
		for scan_idx, (start_ch, end_ch) in enumerate(self._spectra_slices):
			for c in range(start_ch, end_ch + 1):
				channel_to_scan[c] = scan_idx

		for t in range(n_tiles):
			for c in range(n_channels):
				scan_idx = channel_to_scan[c]
				x = coords_px[t, scan_idx, 0]
				y = coords_px[t, scan_idx, 1]

				# Calculate valid boundaries in mosaic
				x_start = max(0, x)
				x_end = min(W, x + tile_w)
				y_start = max(0, y)
				y_end = min(H, y + tile_h)

				# Calculate corresponding tile boundaries
				tx_start = x_start - x
				tx_end = tx_start + (x_end - x_start)
				ty_start = y_start - y
				ty_end = ty_start + (y_end - y_start)

				if (x_end > x_start) and (y_end > y_start):
					tiles[t, c, ty_start:ty_end, tx_start:tx_end] = mosaic[y_start:y_end, x_start:x_end]

		return tiles
	
	def _prepare_for_ashlar(self, tiles: np.ndarray, coordinates: np.ndarray) -> int:
		# Convert the coordinates in um and flip the y-axis (from Leica to OME TIFF format)
		for s, _ in enumerate(self._spectra_slices):
			coordinates[:, s, 1] = np.max(coordinates[:, s, 1]) - (coordinates[:, s, 1] - np.min(coordinates[:, s, 1]))

		# Replace NaN values with 0
		tiles = np.nan_to_num(tiles, nan=0.0)

		# Convert to Uint8
		tiles = (tiles * 255).astype(np.uint8)

		# Identify the cycle 0 channels range
		start_channel_0, end_channel_0 = self._spectra_slices[0]
		tiles_cycle_0 = tiles[:, start_channel_0:end_channel_0 + 1, :, :]  # shape [n_tiles, channels_in_cycle, H, W]

		# Compute average intensity per channel across tiles (mean over tiles & spatial dims)
		# For each channel: mean intensity per tile (H x W), then max of those means across tiles
		# Per channel: first mean over spatial dims (H, W), resulting (n_tiles, channels)
		mean_per_tile_channel = tiles_cycle_0.mean(axis=(2,3))  # shape (n_tiles, channels)
		max_avg_intensity_per_channel = mean_per_tile_channel.max(axis=0)  # shape (channels,)

		# Channel index with highest average intensity (relative to channels in cycle 0)
		highest_intensity_channel_idx_in_cycle = int(np.argmax(max_avg_intensity_per_channel))
		# Map to global channel index in original tiles
		highest_intensity_channel = start_channel_0 + highest_intensity_channel_idx_in_cycle

		# Save OME TIFFs for all cycles as before
		for cycle, (start_channel, end_channel) in enumerate(self._spectra_slices):
			tiles_cycle = tiles[:, start_channel:end_channel + 1, :, :]
			coordinates_cycle = coordinates[:, cycle, :]

			output_filename = os.path.join(self.output_path, f'ashlar_input_cycle_{cycle + 1}.ome.tiff')

			with tifffile.TiffWriter(output_filename, ome=True, bigtiff=True) as tif:
				tiles_cycle = tiles_cycle[:, np.newaxis, :, :, :]
				T, Z, C, Y, X = tiles_cycle.shape

				for t in range(T):
					metadata = {
						'Pixels': {
							'PhysicalSizeX': self.metadata.pixel_size[0],
							'PhysicalSizeY': self.metadata.pixel_size[1],
							'PhysicalSizeXUnit': 'µm',
							'PhysicalSizeYUnit': 'µm',
							'SizeT': 1,
							'SizeC': C,
							'SizeY': Y,
							'SizeX': X,
							'SizeZ': 1,
							'Type': 'uint8',
						},
						'Channel': [{'Name': f'Channel_{i}'} for i in range(C)],
						'Plane': [
							{
								'TheT': 0,
								'TheC': c,
								'TheZ': 0,
								'PositionX': float(coordinates_cycle[t, 0]),
								'PositionY': float(coordinates_cycle[t, 1]),
								'PositionXUnit': 'µm',
								'PositionYUnit': 'µm'
							}
							for c in range(C)
						]
					}

					tif.write(
						tiles_cycle[t, 0, :, :, :],
						metadata=metadata,
						tile=(Y, X),
						compression='zlib',
					)

		# Return the global channel index with highest average intensity in cycle 0
		return highest_intensity_channel

	def ashlar_stitch(self, force_recomputing: bool = False) -> str:
		"""
		Stitch the corrected tiles into a mosaic.
		This mosaic is intended to be used for further processing, as it does not incorporate misaligment artifacts.

		Prior to this method, the BaSiC correction and the raman denoising should be applied.

		Parameters
		----------
		force_recomputing : bool, optional
			If True, the stitching will be forced even if the stitched mosaic already exists
		
		Returns
		-------
		str
			The path to the stitched mosaic OME TIFF file.
		"""

		if type(force_recomputing) is not bool:
			raise TypeError("force_recomputing must be a boolean.")

		output_file = MODALITY_PREPROCESSING(self.base_path, self.sample_id, self.modality_name, "ome.tiff")

		if force_recomputing or not os.path.exists(output_file):
			if self.corrected is None:
				raise ValueError("Make sure to run process_raw_tiles() before calling this method.")

			tools_basedir = os.path.abspath(__file__).replace("src/preprocessing/raman.py", "tools")

			env_name = "FOCUS_ASHLAR"

			align_channel = self._prepare_for_ashlar(tiles=self.corrected, coordinates=copy.deepcopy(self.tiles_coordinates))
			print(f"5/5 - Stitching tiles with ASHLAR using {align_channel} as reference channel")

			# Compose path to main.py (adjust if your ASHLAR interface differs)
			main_script = os.path.join(tools_basedir, "ASHLAR", "main.py")
			if not os.path.isfile(main_script):
				raise FileNotFoundError(f"ASHLAR main script not found at {main_script}")

			# Run ASHLAR stitcher with conda environment
			cmd_parts = [
				"conda", "run", "-n", env_name,
				"python", "-u",
				main_script,
				self.output_path,
				str(align_channel)
			]

			cmd = " ".join(shlex.quote(part) for part in cmd_parts)

			env = os.environ.copy()
			env.pop("MPLBACKEND", None)  # Remove MPLBACKEND if present

			result = subprocess.run(
				cmd,
				shell=True,
				check=True,
				executable="/bin/bash",
				env=env,
			)

			if result.returncode != 0:
				raise RuntimeError(f"ASHLAR stitching failed with error: {result.stderr}")

			# Rename the output file to desired filename
			default_output = os.path.join(self.output_path, "ashlar_output.ome.tiff")
			os.rename(default_output, output_file)

			# Load the stitched mosaic back into the object
			self._mosaic = tifffile.imread(output_file)

			print(f"Sample {self.sample_id}: Processing completed successfully. Stitched mosaic saved to {output_file}")
		else:
			# Load cached mosaic
			self._mosaic = tifffile.imread(output_file)
			print("5/5 - Loaded ASHLAR stitched mosaic from disk. (Using cached results)")
			print(f"Sample {self.sample_id}: Processing completed successfully. Stitched mosaic saved to {output_file}")

		return output_file

	def _force_mosaic_load(self, output_path: str, filename: str):
		self._mosaic = tifffile.imread(os.path.join(output_path, f"{filename}.ome.tiff"))

class RamanDataset:
	'''
	Handle a collection of Raman Images and their metadata to produce a combined AnnData object in the end

	Parameters
	----------
	samples : list[RamanImage]
		A list of RamanImage objects to be included in the dataset.
	'''

	def __init__(self, path: str, samples: list[RamanImage]):
		self.path = path
		self.samples = samples

		# Check if the path exists, if not create it
		if not os.path.exists(self.path):
			os.makedirs(self.path)

	def process_dataset(self, force_recomputing: bool = False) -> dict[str, str]:
		'''
		Process each sample with the preprocessing steps and return a dictionary of processed sample paths.

		Parameters
		----------
		force_recomputing : bool, optional
			If True, the processing will be forced even if the processed data already exists.
			Default is False.

		Returns
		-------
		processed_samples : dict[str, str]
			A dictionary mapping sample IDs to the paths of their processed OME TIFF files.
		'''
		processed_samples = {}
		for sample in self.samples:
			print(f"Processing sample: {sample.sample_id}")

			try:
				if force_recomputing == True or not os.path.exists(MODALITY_PREPROCESSING(sample.base_path, sample.sample_id, sample.modality_name, "ome.tiff")):
					sample.load_source()
					sample.basic_correct(force_recomputing=force_recomputing)
					sample.remove_background(force_recomputing=force_recomputing)
					sample.process_raw_tiles(parallel=True, force_recomputing=force_recomputing)
					output_file = sample.ashlar_stitch(force_recomputing=force_recomputing)
				else:
					print(f"Sample {sample.sample_id} has already been processed. Using cached results.")
					output_file = os.path.join(sample.output_path, f"{sample.sample_id}.ome.tiff")

				processed_samples[sample.sample_id] = output_file

				# Clean up the cached files to save space
				if os.path.exists(os.path.join(sample.output_path, "basic_corrected_tiles.npy")):
					os.remove(os.path.join(sample.output_path, "basic_corrected_tiles.npy"))
				if os.path.exists(os.path.join(sample.output_path, "raman_corrected_tiles.npy")):
					os.remove(os.path.join(sample.output_path, "raman_corrected_tiles.npy"))
				if os.path.exists(os.path.join(sample.output_path, "segmented_tiles.npy")):
					os.remove(os.path.join(sample.output_path, "segmented_tiles.npy"))
			except Exception as e:
				print(f"Error processing sample {sample.sample_id}: {e}")

		return processed_samples