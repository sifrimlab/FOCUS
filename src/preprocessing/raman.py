import tqdm
import numpy as np
from readlif.reader import LifFile, LifImage
import xml.etree.ElementTree as ET
import ramanspy as rp
from basicpy import BaSiC
from scipy.ndimage import distance_transform_edt

from multiprocessing import Pool

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
	
class RamanImage:
	def __init__(self, filename: str):
		'''
		Wrapper to handle Raman Spectral Images. For now, it only supports Leica LIF files.
		Under the hood this class uses ramanspy and custom made methods to handle the data.

		Parameters
		----------
		filename : str
			Path to the LIF file.
		'''

		self._pixel_size = 1.13525390625  		#TODO: Replace with the actual one from metadata

		if type(filename) is not str:
			raise TypeError("Filename must be a string representing the path to the LIF file.")
		
		# Check if the file exists and it's accessible
		try:
			with open(filename, 'rb') as f:
				pass
		except IOError as e:
			raise IOError(f"Could not open the file {filename}. Please check the file path and permissions.") from e

		self.filename = filename

		# Define the intermediate data structure
		self._raw_tiles: np.ndarray[np.float32] = None
		self._basic_corrected_tiles: np.ndarray[np.float32] = None
		self._raman_corrected_tiles: np.ndarray[np.float32] = None
		self._quick_mosaic: np.ndarray[np.float32] = None
		self._mosaic: np.ndarray[np.float32] = None
		self._metadata: RamanMetadata = []
		self._wavenumbers: np.ndarray[np.float32] = None
		self._tiles_coordinates: np.ndarray[np.float32] = None
		self._spectra_slices: list[tuple[int, int]] = []

		# Load the file based on its extension
		if filename.endswith('.lif'):
			self._load_lif(filename = self.filename)
	
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

		# Iterate over the images following the order written in metadata
		for name, metadata in metadata_dict.items():

			# Extract only tiled images, ignore automatic stitching
			if metadata.tile_number is None or metadata.tile_number < 2:
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
				self._spectra_slices.append((self._spectra_slices[-1][1] + 1, self._spectra_slices[-1][1] + metadata.lambda_steps - 1))

			# Compute the wavenumbers based on the 
			wavenumbers[name] = self.compute_wavenumbers(
				metadata.lambda_begin, 
				metadata.lambda_end, 
				metadata.lambda_steps, 
				metadata.lambda_stokes
			)

		# Once all the tiles are loaded, merge them into a single high-dimensional array
		stacked_tiles = np.concatenate([raw_tiles[name] for name in raw_tiles], axis = 1)
		stacked_wavenumbers = np.concatenate([wavenumbers[name] for name in wavenumbers], axis = -1)
		coordinates = metadata_dict[list(metadata_dict.keys())[0]].tiles_coordinates

		# Normalize the tiles to [0, 1] if they are not already normalized
		if np.min(stacked_tiles) < 0 or np.max(stacked_tiles) > 1:
			stacked_tiles -= np.min(stacked_tiles)
			stacked_tiles /= np.max(stacked_tiles)

		# Update the metadata
		reference_metadata.scan_height = stacked_tiles.shape[-2]
		reference_metadata.scan_width = stacked_tiles.shape[-1]
		reference_metadata.tile_number = stacked_tiles.shape[0]
		reference_metadata.lambda_steps = stacked_tiles.shape[1]
		reference_metadata.tiles_coordinates = coordinates

		# Overwrite the raw tiles and wavenumbers with the stacked ones
		self._raw_tiles = stacked_tiles
		self._wavenumbers = stacked_wavenumbers
		self._metadata = reference_metadata
		self._tiles_coordinates = coordinates * 1e6
	
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

			# Get the dimensions
			if data_image_tag is not None:
				image_description = data_image_tag.find('ImageDescription')
				if image_description is not None:                
					dimensions = image_description.find('Dimensions')
					if dimensions is not None and len(list(dimensions)) > 0:
						for dim_desc in dimensions.findall('DimensionDescription'):
							id = int(dim_desc.get('DimID', None))
							size = int(dim_desc.get('NumberOfElements', None))

							# Interpret the dimension ID for standard Leica LIF files
							if id == 1:
								metadata.scan_height = size
							elif id == 2:
								metadata.scan_width = size
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
								metadata.lambda_begin = float(lambda_excitation.get('LambdaExcitationBeginDouble', None))
								metadata.lambda_end = float(lambda_excitation.get('LambdaExcitationEndDouble', None))
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

				for tile_index, tile in enumerate(tiles):
					metadata.tiles_coordinates[tile_index] = ((float(tile.get('PosX')), float(tile.get('PosY'))))

			# Append the metadata to the list
			result[metadata.name] = metadata

		return result

	def compute_wavenumbers(self, lambda_begin: float, lambda_end: float, lambda_steps: float, lamnda_stokes: float) -> np.ndarray[np.float32]:
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

	def check_invalid_values(self, tile: np.ndarray[np.float32]) -> bool:
		'''
		Check if the tile contains invalid values (NaN or Inf).

		Parameters
		----------
		tile : np.ndarray[np.float32]
			The tile to check.

		Returns
		----------
		bool
			True if the tile contains invalid values, False otherwise.
		'''
		
		return np.isnan(tile).any() or np.isinf(tile).any()

	def zero_variance_spectra(self, spectra_array: np.ndarray[np.float32]) -> np.ndarray[np.bool_]:
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

	def _process_tile_parallel(self, tile: np.ndarray[np.float32], wavenumbers: np.ndarray[np.float32], tile_index: int, slice_index: int, global_norm: bool) -> tuple[np.ndarray, int, int]:


		pipeline = rp.preprocessing.Pipeline([
			rp.preprocessing.despike.WhitakerHayes(),
			rp.preprocessing.denoise.SavGol(window_length=9, polyorder=3),
			rp.preprocessing.baseline.IASLS(),
		])

		if global_norm == False:
			pipeline.append(rp.preprocessing.normalise.MinMax())

		# Create a coordinate grid to keep track of the valid pixels
		C, X, Y = tile.shape
		y_indices, x_indices = np.meshgrid(np.arange(X), np.arange(Y))
		coordinates = np.stack((x_indices, y_indices), axis=-1)

		# Reshape the tile and the coordinates to 2D
		reshaped_tile = tile.reshape(tile.shape[0], -1)			# Reshape to (C, Y*X)
		reshaped_tile = reshaped_tile.transpose((1, 0))			# Transpose to (Y*X, C)
		reshaped_coordinates = coordinates.reshape(-1, 2)

		# Compute the zero-variance spectra mask
		zero_variance_mask = self.zero_variance_spectra(reshaped_tile)
		reshaped_tile = reshaped_tile[~zero_variance_mask]
		reshaped_coordinates = reshaped_coordinates[~zero_variance_mask]

		# Create a SpectralImage object
		spectral_image = rp.SpectralImage(reshaped_tile, wavenumbers)

		# Apply the pipeline to the reshaped tile
		processed_tile = pipeline.apply(spectral_image)
		processed_tile = processed_tile.spectral_data

		# Reshape the processed tile back to its original shape
		restored_tile = np.zeros((C, X, Y), dtype=np.float32)

		for i in range(processed_tile.shape[0]):
			x = int(reshaped_coordinates[i, 0])
			y = int(reshaped_coordinates[i, 1])
			restored_tile[:, x, y] = processed_tile[i, :]

		return restored_tile, tile_index, slice_index

	def process_raw_tiles(self, wavenumbers: np.ndarray[np.float32] | None = None, parallel: bool = True, global_norm: bool = False) -> None:
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

		global_norm : bool, optional
			If True, the tiles will be normalized to [0, 1] using a global normalization. If False, the tiles will not be normalized.
			Default is False.
		'''

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
							slice_idx,
							global_norm
						)
					)

			# Use multiprocessing to process the tiles in parallel
			with Pool(processes = len(units)) as pool:
				slice_result = pool.starmap(
					self._process_tile_parallel, 
					units
				)
			
			# Store the processed tiles in the dictionary
			for processed_tile, tile_index, slice_index in slice_result:
				start_channel, end_channel = self._spectra_slices[slice_index]
				self._raman_corrected_tiles[tile_index, start_channel:end_channel + 1] = processed_tile
		else:
			for tile_idx in tqdm.tqdm(range(self._basic_corrected_tiles.shape[0]), desc="Processing Tiles"):
				for slice_index, (start_channel, end_channel) in enumerate(self._spectra_slices):
					wavenumbers_slice = wavenumbers[start_channel:end_channel + 1]
					tile_slice = self._basic_corrected_tiles[tile_idx, start_channel:end_channel + 1, :, :]

					# Process the tile
					processed_tile, _ = self._process_tile_parallel(tile_slice, wavenumbers_slice, tile_idx, slice_index, global_norm)
					self._raman_corrected_tiles[tile_idx, start_channel:end_channel + 1] = processed_tile

		if global_norm == True:
			# Get the global minimum and maximum values across all tiles
			min_value = np.min(self._raman_corrected_tiles)
			max_value = np.max(self._raman_corrected_tiles)


			# Normalize the tiles to [0, 1] with a global normalization
			self._raman_corrected_tiles -= np.min(self._raman_corrected_tiles)
			self._raman_corrected_tiles /= np.max(self._raman_corrected_tiles)

	def basic_correct(self) -> None:
		'''
		Apply the BaSiC correction to the raw tiles.
		This method uses the BaSiC algorithm to correct the raw tiles for background and noise.
		'''

		if self._raw_tiles is None:
			raise ValueError("No raw tiles to correct. Please load the data first.")

		for channel_idx in tqdm.tqdm(range(self._raw_tiles.shape[1]), desc="Applying BaSiC Correction"):
			# Extract the channel data
			channel_data = self._raw_tiles[:, channel_idx, :, :]

			# Apply BaSiC correction
			basic = BaSiC()
			basic.fit(channel_data)

			# Store the corrected channel
			if self._basic_corrected_tiles is None:
				self._basic_corrected_tiles = np.zeros_like(self._raw_tiles, dtype=np.float32)
			
			self._basic_corrected_tiles[:, channel_idx, :, :] = basic.transform(channel_data)

		# Apply global normalization to the corrected tiles
		self._basic_corrected_tiles -= np.min(self._basic_corrected_tiles)
		self._basic_corrected_tiles /= np.max(self._basic_corrected_tiles)

	def quick_stitch(self) -> None:
		"""
		Stitch the BaSiC corrected tiles into a mosaic for background removal.
		This mosaic is not intended to be used for any other purpose, as it incorporates misaligment artifacts.
		"""

		tiles = self._basic_corrected_tiles
		coordinates = self._tiles_coordinates

		# Convert coordinates to pixel positions (x, y)
		coords_px = (coordinates / self._pixel_size).astype(int)
		
		# Calculate mosaic dimensions (width, height)
		min_x, min_y = np.min(coords_px, axis=0)
		max_x = np.max(coords_px[:, 0] + tiles.shape[3])
		max_y = np.max(coords_px[:, 1] + tiles.shape[2])
		
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
			x, y = coords_px[t] - [min_x, min_y]
			tile_height, tile_width = tiles.shape[2], tiles.shape[3]
			
			# Calculate valid regions
			y_start = max(0, y)
			y_end = min(mosaic_height, y + tile_height)
			x_start = max(0, x)
			x_end = min(mosaic_width, x + tile_width)
			
			tile_y_start = max(0, -y)
			tile_y_end = tile_height - max(0, y + tile_height - mosaic_height)
			tile_x_start = max(0, -x)
			tile_x_end = tile_width - max(0, x + tile_width - mosaic_width)

			# Add weighted tile contribution
			for c in range(tiles.shape[1]):
				mosaic[c, y_start:y_end, x_start:x_end] += (
					tiles[t, c, tile_y_start:tile_y_end, tile_x_start:tile_x_end] *
					tile_weights[tile_y_start:tile_y_end, tile_x_start:tile_x_end]
				)
				weights[c, y_start:y_end, x_start:x_end] += tile_weights[tile_y_start:tile_y_end, tile_x_start:tile_x_end]

		# Normalize mosaic by accumulated weights
		mosaic = np.divide(mosaic, weights, where=weights > 0)

		# Normalize the mosaic to [0, 1]
		mosaic -= np.min(mosaic)
		mosaic /= np.max(mosaic)

		self._quick_mosaic = mosaic.astype(np.float32)

	def _extract_tiles_from_mosaic(self, mosaic, coordinates, pixel_size, tile_size) -> np.ndarray[np.float32]:
		"""
		Extracts tiles from mosaic (invert the quick_stitch method).

		Parameters
		----------
		mosaic : np.ndarray[np.float32]
			The stitched mosaic from which to extract tiles.
		coordinates : np.ndarray[np.float32]
			The (X, Y) coordinates of each tile in the mosaic.
		pixel_size : float
			The size of a pixel in the original image.
		tile_size : tuple[int, int]
			The size of each tile in pixels (height, width).
		"""
		# Convert coordinates to pixel positions
		coords_px = (coordinates / pixel_size).astype(int)
		
		# REPLICATE MOSAIC CREATION LOGIC
		min_x = np.min(coords_px[:, 0])
		min_y = np.min(coords_px[:, 1])
		coords_px[:, 0] -= min_x  # X coordinate shift
		coords_px[:, 1] -= min_y  # Y coordinate shift
		
		# Get tile dimensions
		tile_h, tile_w = tile_size
		T = len(coords_px)
		C, H, W = mosaic.shape
		
		# Initialize output array
		tiles = np.zeros((T, C, tile_h, tile_w), dtype=mosaic.dtype)
		
		for t in range(T):
			x, y = coords_px[t]
			
			# Calculate valid regions
			x_start = max(0, x)
			x_end = min(W, x + tile_w)
			y_start = max(0, y)
			y_end = min(H, y + tile_h)
			
			# Calculate tile regions
			tx_start = x_start - x
			tx_end = tx_start + (x_end - x_start)
			ty_start = y_start - y
			ty_end = ty_start + (y_end - y_start)
			
			if (x_end > x_start) and (y_end > y_start):
				tiles[t, :, ty_start:ty_end, tx_start:tx_end] = mosaic[
					:, y_start:y_end, x_start:x_end
				]

		# Normalize the tiles to [0, 1]
		tiles -= np.min(tiles)
		tiles /= np.max(tiles)
		
		return tiles
	
