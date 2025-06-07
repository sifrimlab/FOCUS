import cv2, tqdm
import numpy as np
from readlif.reader import LifFile, LifImage
import xml.etree.ElementTree as ET
import ramanspy as rp

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
		self._raw_tiles: dict[str, np.ndarray[np.float32]] = {}
		self._metadata: dict[str, list[RamanMetadata]] = {}
		self._wavenumbers: dict[str, np.ndarray[np.float32]] = {}
		self._processed_tiles: dict[str, np.ndarray[np.float32]] = {}
		self._mosaics: dict[str, np.ndarray[np.float32]] = {}


		# Load the file based on its extension
		if filename.endswith('.lif'):
			self._load_lif(filename = self.filename)

	@property
	def scan_names(self) -> list[str]:
		'''
		Get the names of the scans in the Raman Spectroscopy Imageing file.

		Returns
		-------
		list[str]
			A list of scan names.
		'''
		return list(self._metadata.keys())
	
	@property
	def raw_tiles(self) -> dict[str, np.ndarray[np.float32]]:
		'''
		Get the raw tiles from the Raman Spectroscopy Imageing file.

		Returns
		-------
		dict[str, np.ndarray[np.float32]]
			A dictionary where keys are scan names and values are numpy arrays of raw tiles.
		'''
		return self._raw_tiles
	
	@property
	def metadata(self) -> dict[str, RamanMetadata]:
		'''
		Get the metadata from the Raman Spectroscopy Imageing file.

		Returns
		-------
		dict[str, RamanMetadata]
			A dictionary where keys are scan names and values are RamanMetadata objects.
		'''
		return self._metadata
	
	@property
	def wavenumbers(self) -> dict[str, np.ndarray[np.float32]]:
		'''
		Get the wavenumbers from the Raman Spectroscopy Imageing file.

		Returns
		-------
		dict[str, np.ndarray[np.float32]]
			A dictionary where keys are scan names and values are numpy arrays of wavenumbers.
		'''
		return self._wavenumbers
	
	@property
	def processed_tiles(self) -> dict[str, np.ndarray[np.float32]]:
		'''
		Get the processed tiles from the Raman Spectroscopy Imageing file.

		Returns
		-------
		dict[str, np.ndarray[np.float32]]
			A dictionary where keys are scan names and values are numpy arrays of processed tiles.
		'''
		return self._processed_tiles

	def _load_lif(self, filename: str) -> None:
		'''
		Load Raman Spectroscopy Imageing data from a Leica LIF file.

		Parameters
		----------
		file : str
			Path to the LIF file.
		'''
		
		lif_file = LifFile(filename)
		self._metadata: dict[str, RamanMetadata] = self._parse_lif_metadata(lif_file)

		# Iterate over the images following the order written in metadata
		for name, metadata in self._metadata.items():

			# Extract only tiled images, ignore automatic stitching
			if metadata.tile_number is None or metadata.tile_number < 2:
				continue

			# Read the image
			image: LifImage = lif_file.get_image(metadata.index)
			if image is None:
				raise ValueError(f"Image with index {metadata.index} not found in the LIF file.")

			# Initialize a list to hold the tiles
			self._raw_tiles[name] = np.zeros((metadata.tile_number, metadata.scan_width, metadata.scan_height, metadata.lambda_steps), dtype=np.float32)

			# Read the tiles
			for tile_idx in range(metadata.tile_number):
				# For each tile, iterate over the spectral dimensions
				for spectral_idx in range(metadata.lambda_steps):
					# Read the image data for each lambda step
					plane = image.get_plane(display_dims=(1, 2), c = 0, requested_dims = {9: spectral_idx, 10: tile_idx})

					# Convert the plane to a numpy array and normalize it between 0 and 1
					plane = np.array(plane, dtype=np.float32)
					plane = cv2.normalize(plane, None, 0, 1, cv2.NORM_MINMAX)
					self._raw_tiles[name][tile_idx, :, :, spectral_idx] = plane



			# Compute the wavenumbers based on the 
			self._wavenumbers[name] = self.compute_wavenumbers(
				metadata.lambda_begin, 
				metadata.lambda_end, 
				metadata.lambda_steps, 
				metadata.lambda_stokes
			)
	
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

	def zero_variance_spectra(self, spectra_array: np.ndarray[np.float32]) -> np.ndarray[np.bool]:
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
		
		return np.array(mad == 0, dtype=np.bool).squeeze()

	def _process_tile_parallel(self, tile: np.ndarray[np.float32], wavenumbers: np.ndarray[np.float32], tile_index: int, image_index: int) -> tuple[np.ndarray, int, int]:


		pipeline = rp.preprocessing.Pipeline([
			rp.preprocessing.despike.WhitakerHayes(),
			rp.preprocessing.denoise.SavGol(window_length=9, polyorder=3),
			rp.preprocessing.baseline.IASLS(),
			#rp.preprocessing.normalise.MinMax(pixelwise = True)
		])

		# Create a coordinate grid to keep track of the valid pixels
		X, Y, Z = tile.shape
		y_indices, x_indices = np.meshgrid(np.arange(X), np.arange(Y))
		coordinates = np.stack((x_indices, y_indices), axis=-1)

		# Reshape the tile and the coordinates to 2D
		reshaped_tile = tile.reshape(-1, tile.shape[-1])
		reshaped_coordinates = coordinates.reshape(-1, 2)

		# Compute the zero-variance spectra mask
		zero_variance_mask = self.zero_variance_spectra(reshaped_tile)
		reshaped_tile = reshaped_tile[~zero_variance_mask]
		reshaped_coordinates = reshaped_coordinates[~zero_variance_mask]

		# Create a SpectralImage object
		spectral_image = rp.SpectralImage(reshaped_tile, wavenumbers)

		# Apply the pipeline to the reshaped tile
		processed_tile = pipeline.apply(spectral_image)

		# Normalize the processed tile
		processed_tile = rp.preprocessing.normalise.MinMax().apply(processed_tile)


		processed_tile = processed_tile.spectral_data

		# Reshape the processed tile back to its original shape
		restored_tile = np.zeros((X, Y, Z), dtype=np.float32)

		for i in range(processed_tile.shape[0]):
			x = int(reshaped_coordinates[i, 0])
			y = int(reshaped_coordinates[i, 1])
			restored_tile[x, y, :] = processed_tile[i, :]

		return restored_tile, tile_index, image_index

	def process_raw_tiles(self, parallel: bool = True) -> None:
		'''
		Process the raw tiles using the ramanspy library.
		This method can process the tiles in parallel (active by default) or sequentially.
		
		Parameters
		----------
		parallel : bool, optional
			If True, process the tiles in parallel using multiprocessing. Default is True.
		'''

		if not self._raw_tiles:
			raise ValueError("No raw tiles to process. Please load the data first.")
		
		if parallel:
			total_tiles = sum(self._raw_tiles[name].shape[0] for name in self._raw_tiles)

			# Use multiprocessing to process the tiles in parallel
			with Pool(processes = total_tiles) as pool:
				results = pool.starmap(
					self._process_tile_parallel, 
					[(tile, self._wavenumbers[name], tile_index, name) for name, raw_tiles in self._raw_tiles.items() for tile_index, tile in enumerate(raw_tiles)]
				)
			
			# Store the processed tiles in the dictionary
			for processed_tile, tile_index, image_name in results:
				if image_name not in self._processed_tiles:
					self._processed_tiles[image_name] = np.zeros_like(self._raw_tiles[image_name], dtype=np.float32)
				self._processed_tiles[image_name][tile_index] = processed_tile
		else:
			for name, raw_tiles in tqdm.tqdm(self._raw_tiles.items(), desc="Processing Tiles"):
				self._processed_tiles[name] = np.zeros_like(raw_tiles, dtype=np.float32)
				for tile_index, tile in enumerate(raw_tiles):
					processed_tile, _, _ = self._process_tile_parallel(tile, self._wavenumbers[name], tile_index, self._metadata[name].index)
					self._processed_tiles[name][tile_index] = processed_tile
