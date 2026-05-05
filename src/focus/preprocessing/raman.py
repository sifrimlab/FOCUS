import tqdm, tifffile, os, subprocess, shlex
from focus.preprocessing._utils import StepReporter
import warnings, copy, cv2, shutil, json, time
import numpy as np
from readlif.reader import LifFile, LifImage
import xml.etree.ElementTree as ET
import ramanspy as rp
from skimage import morphology
from scipy.ndimage import binary_fill_holes
from scipy.ndimage import distance_transform_edt
from joblib import Parallel, delayed
from sklearn.decomposition import PCA
import concurrent.futures

from focus.constants import MODALITY_PREPROCESSING, RamanPreprocessingParams
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality

# Resolve tools directory relative to the project root (raman.py → preprocessing → focus → src → project_root → tools)
_TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "tools")


class RamanMetadata:
	"""Store metadata from a Raman Spectroscopy Imaging file regardless of file format."""

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
		self._tiles_coordinates: np.ndarray = None
		self._pixel_size: np.ndarray = None

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
		if not isinstance(value, int):
			raise TypeError("Scan height must be an integer.")
		if value <= 0:
			raise ValueError("Scan height must be a positive number.")
		self._scan_height = int(value)

	@property
	def scan_width(self) -> int:
		return self._scan_width

	@scan_width.setter
	def scan_width(self, value: int):
		if not isinstance(value, int):
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
	def tiles_coordinates(self) -> np.ndarray:
		return self._tiles_coordinates

	@tiles_coordinates.setter
	def tiles_coordinates(self, value: np.ndarray):
		if not isinstance(value, np.ndarray):
			raise TypeError("Tiles coordinates must be a numpy array.")
		if value.ndim != 2 or value.shape[1] != 2:
			raise ValueError("Tiles coordinates must be a 2D array with shape (N, 2).")
		self._tiles_coordinates = value.astype(np.float32)

	@property
	def pixel_size(self) -> np.ndarray:
		return self._pixel_size

	@pixel_size.setter
	def pixel_size(self, value: np.ndarray):
		if not isinstance(value, np.ndarray):
			raise TypeError("Pixel size must be a numpy array.")
		if value.ndim != 1 or value.shape[0] != 2:
			raise ValueError("Pixel size must be a 1D array with shape (2,).")
		self._pixel_size = value.astype(np.float32)


def _zero_variance_spectra(spectra_array: np.ndarray) -> np.ndarray:
	"""Identify zero-variance spectra (produce numerical errors downstream)."""
	forward_differences = np.diff(spectra_array, axis=-1)
	mad = np.median(np.abs(forward_differences - np.median(forward_differences, axis=-1, keepdims=True)), axis=-1)
	return np.array(mad == 0, dtype=np.bool_).squeeze()


def _process_tile_parallel(tile: np.ndarray, wavenumbers: np.ndarray,
	tile_index: int, slice_index: int,
	savgol_window: int, savgol_polyorder: int
) -> tuple[np.ndarray, int, int]:
	"""Process a single tile with the RamanSPy spectral cleaning pipeline."""
	with warnings.catch_warnings():
		warnings.simplefilter("ignore", RuntimeWarning)

		pipeline = rp.preprocessing.Pipeline([
			rp.preprocessing.despike.WhitakerHayes(),
			rp.preprocessing.denoise.SavGol(window_length=savgol_window, polyorder=savgol_polyorder),
			rp.preprocessing.baseline.IASLS(),
			rp.preprocessing.normalise.MinMax()
		])

		C, X, Y = tile.shape
		y_indices, x_indices = np.meshgrid(np.arange(X), np.arange(Y))
		coordinates = np.stack((x_indices, y_indices), axis=-1)

		reshaped_tile = tile.reshape(C, -1).T  # (Y*X, C)
		reshaped_coordinates = coordinates.reshape(-1, 2)

		zero_variance_mask = _zero_variance_spectra(reshaped_tile)
		reshaped_tile = reshaped_tile[~zero_variance_mask]
		reshaped_coordinates = reshaped_coordinates[~zero_variance_mask]

		if reshaped_tile.shape[0] == 0:
			return np.zeros_like(tile), tile_index, slice_index

		spectral_image = rp.SpectralImage(reshaped_tile, wavenumbers)
		processed_tile = pipeline.apply(spectral_image).spectral_data

		restored_tile = np.zeros((C, X, Y), dtype=np.float32)
		for i in range(processed_tile.shape[0]):
			x = int(reshaped_coordinates[i, 0])
			y = int(reshaped_coordinates[i, 1])
			restored_tile[:, x, y] = processed_tile[i, :]

		return restored_tile, tile_index, slice_index


class RamanImage(BaseSample):
	"""
	Process Raman Spectral Images. Supports Leica LIF files.
	Pipeline: load → BaSiC correction → background removal → spectral cleaning → ASHLAR stitching → OME-TIFF.
	"""

	# Default processing parameters (all configurable via process_dataset)
	_SAVGOL_WINDOW = 7
	_SAVGOL_POLYORDER = 3
	_BG_MIN_AREA_FRACTION = 0.05
	_OTSU_THRESHOLD_FACTOR = 0.7
	_MIN_OBJECT_SIZE = 500
	_MAX_WORKERS = 8

	def __init__(
			self,
			source_path: str,
			sample_id: str,
			modality_name: str,
			max_workers: int = _MAX_WORKERS
		):
		"""
		Parameters
		----------
		source_path : str
			Path to the data source directory.
		sample_id : str
			Sample ID.
		modality_name : str
			Name of the modality.
		max_workers : int
			Maximum number of workers for parallel processing.
		"""
		super().__init__(source_path, sample_id, modality_name)
		self.input_path = os.path.join(source_path, sample_id, modality_name)
		self._max_workers = max_workers

		# Intermediate data
		self._raw_tiles: np.ndarray = None
		self._basic_corrected_tiles: np.ndarray = None
		self._raman_corrected_tiles: np.ndarray = None
		self._quick_mosaic: np.ndarray = None
		self._mosaic: np.ndarray = None
		self._metadata: RamanMetadata = None
		self._wavenumbers: np.ndarray = None
		self._tiles_coordinates: np.ndarray = None
		self._spectra_slices: list[tuple[int, int]] = []

	def load_source(self) -> None:
		"""Load the source data. Looks for supported file formats (LIF) in the input directory."""
		os.makedirs(self.output_path, exist_ok=True)

		reporter = getattr(self, '_step_reporter', None) or StepReporter()
		found = False
		with os.scandir(self.input_path) as it:
			for entry in it:
				if entry.is_file() and entry.name.lower().endswith('.lif'):
					reporter.step(f"1/5 - Loading Raman data from LIF file: {entry.name}")
					self._load_lif(os.path.join(self.input_path, entry.name))
					found = True
					break

		if not found:
			raise FileNotFoundError(f"No supported Raman source file found in {self.input_path}")

	# --- Properties ---

	@property
	def raw(self) -> np.ndarray:
		"""Raw tiles: (T, C, Y, X) float32."""
		return self._raw_tiles

	@property
	def corrected(self) -> np.ndarray:
		"""Corrected tiles (BaSiC + background + RamanSPy): (T, C, Y, X) float32."""
		return self._raman_corrected_tiles

	@property
	def mosaic(self) -> np.ndarray:
		"""Final stitched mosaic: (C, Y, X)."""
		return self._mosaic

	@property
	def metadata(self) -> RamanMetadata:
		return self._metadata

	@property
	def wavenumbers(self) -> np.ndarray:
		"""Wavenumber array: (W,) float32."""
		return self._wavenumbers

	@property
	def tiles_coordinates(self) -> np.ndarray:
		"""Tile coordinates: (T, N_slices, 2) float32."""
		return self._tiles_coordinates

	@property
	def sample_id(self) -> str:
		return self._sample_id

	@sample_id.setter
	def sample_id(self, value: str):
		if not isinstance(value, str):
			raise TypeError("Sample ID must be a string.")
		self._sample_id = value

	# --- Data loading ---

	def _load_lif(self, filename: str) -> None:
		"""Load Raman data from a Leica LIF file."""
		lif_file = LifFile(filename)
		metadata_dict: dict[str, RamanMetadata] = self._parse_lif_metadata(lif_file)

		reference_metadata = RamanMetadata()
		reference_metadata.name = "reference"
		reference_metadata.index = 0

		raw_tiles: dict[str, np.ndarray] = {}
		wavenumbers: dict[str, np.ndarray] = {}
		coordinates: dict[str, np.ndarray] = {}
		pixel_size: dict[str, np.ndarray] = {}

		for name, metadata in metadata_dict.items():
			# Extract only tiled images, ignore automatic stitching
			if metadata.tile_number is None or metadata.tile_number < 2:
				continue

			if None in [metadata.tile_number, metadata.lambda_steps, metadata.scan_width, metadata.scan_height]:
				print(f"Warning: Image '{name}' is missing required metadata. Probably corrupted scan")
				continue

			image: LifImage = lif_file.get_image(metadata.index)
			if image is None:
				raise ValueError(f"Image with index {metadata.index} not found in the LIF file.")

			# Allocate as float32 (needed for BaSiC correction downstream)
			raw_tiles[name] = np.zeros(
				(metadata.tile_number, metadata.lambda_steps, metadata.scan_width, metadata.scan_height),
				dtype=np.float32
			)

			for tile_idx in range(metadata.tile_number):
				for spectral_idx in range(metadata.lambda_steps):
					plane = image.get_plane(display_dims=(1, 2), c=0, requested_dims={9: spectral_idx, 10: tile_idx})
					raw_tiles[name][tile_idx, spectral_idx, :, :] = plane

			# Record spectra slice boundaries
			if len(self._spectra_slices) == 0:
				self._spectra_slices.append((0, metadata.lambda_steps - 1))
			else:
				prev_end = self._spectra_slices[-1][1]
				self._spectra_slices.append((prev_end + 1, prev_end + metadata.lambda_steps))

			wavenumbers[name] = self._compute_wavenumbers(
				metadata.lambda_begin, metadata.lambda_end,
				metadata.lambda_steps, metadata.lambda_stokes
			)

			if metadata.tiles_coordinates is not None:
				coordinates[name] = metadata.tiles_coordinates
			else:
				raise ValueError(f"Tiles coordinates not found for image '{name}'.")

			if metadata.pixel_size is not None and np.all(metadata.pixel_size > 0):
				pixel_size[name] = metadata.pixel_size
			else:
				raise ValueError(f"Pixel size not found or invalid for image '{name}'.")

		# Merge all scans into single arrays
		stacked_tiles = np.concatenate([raw_tiles[name] for name in raw_tiles], axis=1)
		stacked_wavenumbers = np.concatenate([wavenumbers[name] for name in wavenumbers], axis=-1)
		coords_array = np.stack([coordinates[name] for name in coordinates], axis=1)
		pixel_size_array = np.stack([pixel_size[name] for name in pixel_size], axis=0).mean(axis=0)

		# Handle wavenumber overlaps (re-scanned regions)
		break_idx, closest_idx = self._check_wavenumbers_overlaps(stacked_wavenumbers)

		if break_idx is not None and closest_idx is not None:
			print(f"Detected overlapping wavenumbers at index {break_idx}. Removing overlapping region.")
			stacked_wavenumbers = np.concatenate([stacked_wavenumbers[:closest_idx], stacked_wavenumbers[break_idx:]])
			stacked_tiles = np.concatenate([stacked_tiles[:, :closest_idx, :, :], stacked_tiles[:, break_idx:, :, :]], axis=1)

			# Update spectra slice indices
			new_slices = []
			shift = break_idx - closest_idx
			for index, s in enumerate(self._spectra_slices):
				if index < self._find_slice_index(closest_idx):
					new_slices.append(s)
				elif index == self._find_slice_index(closest_idx):
					new_slices.append((s[0], int(closest_idx - 1)))
				elif index == self._find_slice_index(break_idx):
					new_slices.append((int(closest_idx), int(s[1] - shift)))
				else:
					new_slices.append((int(s[0] - shift), int(s[1] - shift)))
			self._spectra_slices = new_slices

		# Normalize to [0, 1] float32
		max_val = stacked_tiles.max()
		if max_val > 1.0 and max_val <= 255.0:
			stacked_tiles /= np.float32(255.0)
		elif max_val > 1.0 and max_val <= 65535.0:
			stacked_tiles /= np.float32(65535.0)
		elif max_val > 65535.0:
			raise ValueError("Expected input data in range [0, 255] or [0, 65535].")

		# Store results
		reference_metadata.scan_height = stacked_tiles.shape[-2]
		reference_metadata.scan_width = stacked_tiles.shape[-1]
		reference_metadata.tile_number = stacked_tiles.shape[0]
		reference_metadata.lambda_steps = stacked_tiles.shape[1]
		reference_metadata.tiles_coordinates = coords_array[0]
		reference_metadata.pixel_size = pixel_size_array

		self._raw_tiles = stacked_tiles
		self._wavenumbers = stacked_wavenumbers.astype(np.float32)
		self._metadata = reference_metadata
		self._tiles_coordinates = coords_array

	def _find_slice_index(self, channel_idx: int) -> int:
		"""Find which spectra slice a channel index belongs to."""
		for i, (start, end) in enumerate(self._spectra_slices):
			if start <= channel_idx <= end:
				return i
		return len(self._spectra_slices) - 1

	def _check_wavenumbers_overlaps(self, wavenumbers: np.ndarray) -> tuple:
		if len(wavenumbers) < 2:
			return None, None

		is_asc = np.all(wavenumbers[:-1] <= wavenumbers[1:])
		is_desc = np.all(wavenumbers[:-1] >= wavenumbers[1:])
		if is_asc or is_desc:
			return None, None

		expected_asc = wavenumbers[1] >= wavenumbers[0]

		break_idx = None
		for i in range(1, len(wavenumbers)):
			if expected_asc and wavenumbers[i] < wavenumbers[i - 1]:
				break_idx = i
				break
			elif not expected_asc and wavenumbers[i] > wavenumbers[i - 1]:
				break_idx = i
				break

		if break_idx is None:
			return None, None

		target_val = wavenumbers[break_idx]
		left_slice = wavenumbers[:break_idx]
		closest_idx = int(np.argmin(np.abs(left_slice - target_val)))

		return break_idx, closest_idx

	def _parse_lif_metadata(self, lif: LifFile) -> dict[str, RamanMetadata]:
		"""Parse LIF file XML metadata and extract scan parameters."""
		root = lif.xml_root

		top_level_elements = root.findall('./Element')
		if not top_level_elements:
			top_level_elements = root.findall('.')
			if root.tag != 'Element':
				top_level_elements = root.findall('.//Element')

		elements_to_process = []
		for top_element in top_level_elements:
			children_tag = top_element.find('Children')
			if children_tag is not None:
				for image_element in children_tag.findall('Element'):
					elements_to_process.append(image_element)
			else:
				if top_element.find('./Data/Image') is not None:
					elements_to_process.append(top_element)

		# Fallback for collapsed XML structures
		if not elements_to_process and root.tag == 'LMSDataContainerHeader':
			root_children_tag = root.find('Children')
			if root_children_tag is not None:
				for image_element in root_children_tag.findall('Element'):
					elements_to_process.append(image_element)
			elif root.find('./Element/Data/Image') is not None:
				for image_element in root.findall('Element'):
					if image_element.find('./Data/Image') is not None:
						elements_to_process.append(image_element)

		if not elements_to_process:
			raise ValueError("No elements found in the LIF file or unexpected XML structure.")

		result: dict[str, RamanMetadata] = {}

		for i, element in enumerate(elements_to_process):
			element_name = element.get('Name', f"Unnamed Element {i + 1}")
			data_image_tag = element.find('./Data/Image')

			metadata = RamanMetadata()
			metadata.name = element_name
			metadata.index = i
			metadata.pixel_size = np.array([0, 0], dtype=np.float32)
			dimension_scaling_factor = [None, None]

			if data_image_tag is not None:
				image_description = data_image_tag.find('ImageDescription')
				if image_description is not None:
					dimensions = image_description.find('Dimensions')
					if dimensions is not None and len(list(dimensions)) > 0:
						for dim_desc in dimensions.findall('DimensionDescription'):
							dim_id = int(dim_desc.get('DimID', None))
							size = int(dim_desc.get('NumberOfElements', None))
							length = float(dim_desc.get('Length', None))
							unit = dim_desc.get('Unit', None)

							if dim_id == 1:
								metadata.scan_height = size
								if unit == 'm':
									dimension_scaling_factor[0] = 1e6
								elif unit == 'um':
									dimension_scaling_factor[0] = 1e0
								else:
									raise ValueError(f"Unexpected unit '{unit}' for pixel size in image '{element_name}'.")
								metadata.pixel_size[0] = (length * dimension_scaling_factor[0]) / size
							elif dim_id == 2:
								metadata.scan_width = size
								if unit == 'm':
									dimension_scaling_factor[1] = 1e6
								elif unit == 'um':
									dimension_scaling_factor[1] = 1e0
								else:
									raise ValueError(f"Unexpected unit '{unit}' for pixel size in image '{element_name}'.")
								metadata.pixel_size[1] = (length * dimension_scaling_factor[1]) / size
							elif dim_id == 9:
								metadata.lambda_steps = size
							elif dim_id == 10:
								metadata.tile_number = size

			# Extract wavelength range and laser info
			atl_confocal_paths = [
				'./Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition',
				'./Data/Image/Attachment[@Name="HardwareSetting"]/LDM_Block_Sequential/LDM_Block_Sequential_Master/ATLConfocalSettingDefinition'
			]

			found_lambda, found_stokes = False, False

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

					if not found_stokes:
						laser_array = atl_confocal_setting_def.find('LaserArray')
						if laser_array is not None:
							for laser_tag in laser_array.findall('Laser'):
								pump_wavelength = laser_tag.get('PumpWavelength')
								if pump_wavelength is not None:
									metadata.laser_type = laser_tag.get('LaserName', 'Unknown Laser')
									metadata.lambda_stokes = float(pump_wavelength)
									found_stokes = True
									break

				if found_lambda and found_stokes:
					break

			# Extract tile coordinates
			if metadata.tile_number is not None and metadata.tile_number > 1:
				tiles = element.findall('./Data/Image/Attachment[@Name="TileScanInfo"]/Tile')
				metadata.tiles_coordinates = np.zeros((metadata.tile_number, 2), dtype=np.float32)

				if len(tiles) != metadata.tile_number:
					print(f"RuntimeWarning: For element {element_name}, expected {metadata.tile_number} tiles, but found {len(tiles)} tiles. Ignoring.")
					continue

				for tile_index, tile in enumerate(tiles):
					metadata.tiles_coordinates[tile_index] = (
						float(tile.get('PosX')) * dimension_scaling_factor[0],
						float(tile.get('PosY')) * dimension_scaling_factor[1]
					)

			result[metadata.name] = metadata

		return result

	@staticmethod
	def _compute_wavenumbers(lambda_begin: float, lambda_end: float, lambda_steps: int, lambda_stokes: float) -> np.ndarray:
		"""Compute Raman wavenumber array from wavelength metadata."""
		pump_wavelength = np.linspace(lambda_begin, lambda_end, lambda_steps, dtype=np.float32)
		raman_wavenumbers = ((1.0 / pump_wavelength) - (1.0 / lambda_stokes)) * 1e7
		return raman_wavenumbers

	# --- Processing steps ---

	def process_raw_tiles(self, wavenumbers: np.ndarray = None, parallel: bool = True,
		force_recomputing: bool = False,
		savgol_window: int = _SAVGOL_WINDOW,
		savgol_polyorder: int = _SAVGOL_POLYORDER
	) -> None:
		"""
		Process raw tiles using the RamanSPy spectral cleaning pipeline.

		Parameters
		----------
		wavenumbers : np.ndarray, optional
			Wavenumber array. If None, uses the loaded metadata wavenumbers.
		parallel : bool
			Process tiles in parallel.
		force_recomputing : bool
			Force reprocessing even if cached results exist.
		savgol_window : int
			Savitzky-Golay filter window length.
		savgol_polyorder : int
			Savitzky-Golay filter polynomial order.
		"""
		cache_file = os.path.join(self.output_path, "raman_corrected_tiles.npy")

		if force_recomputing or not os.path.exists(cache_file):
			if self._basic_corrected_tiles is None:
				raise ValueError("No corrected tiles to process. Run basic_correct() first.")

			if wavenumbers is None:
				wavenumbers = self.wavenumbers

			self._raman_corrected_tiles = np.zeros_like(self._basic_corrected_tiles, dtype=np.float32)

			if parallel:
				units = []
				for slice_idx, (start_ch, end_ch) in enumerate(self._spectra_slices):
					for tile_idx in range(self._basic_corrected_tiles.shape[0]):
						units.append((
							self._basic_corrected_tiles[tile_idx, start_ch:end_ch + 1, :, :],
							wavenumbers[start_ch:end_ch + 1],
							tile_idx, slice_idx,
							savgol_window, savgol_polyorder
						))

				_reporter = getattr(self, '_step_reporter', None) or StepReporter()
				slice_result = list(
					_reporter.tqdm(
						Parallel(n_jobs=self._max_workers, return_as="generator")(
							delayed(_process_tile_parallel)(*args) for args in units
						),
						desc="4/5 - Cleaning Raman Spectra (Parallel)",
						total=len(units),
						unit="tile"
					)
				)

				for processed_tile, tile_index, slice_index in slice_result:
					start_ch, end_ch = self._spectra_slices[slice_index]
					self._raman_corrected_tiles[tile_index, start_ch:end_ch + 1] = processed_tile
			else:
				_reporter = getattr(self, '_step_reporter', None) or StepReporter()
				for tile_idx in _reporter.tqdm(range(self._basic_corrected_tiles.shape[0]), desc="4/5 - Cleaning Raman Spectra"):
					for slice_index, (start_ch, end_ch) in enumerate(self._spectra_slices):
						processed_tile, _, _ = _process_tile_parallel(
							self._basic_corrected_tiles[tile_idx, start_ch:end_ch + 1, :, :],
							wavenumbers[start_ch:end_ch + 1],
							tile_idx, slice_index,
							savgol_window, savgol_polyorder
						)
						self._raman_corrected_tiles[tile_idx, start_ch:end_ch + 1] = processed_tile

			np.save(cache_file, self._raman_corrected_tiles)
		else:
			self._raman_corrected_tiles = np.load(cache_file)
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step("4/5 - Loaded Clean Raman Spectra from disk. (Using cached results)")

	def basic_correct(self, force_recomputing: bool = False) -> None:
		"""Apply BaSiC illumination correction to raw tiles via external conda environment."""
		cache_file = os.path.join(self.output_path, "basic_corrected_tiles.npy")

		if force_recomputing or not os.path.exists(cache_file):
			if self._raw_tiles is None:
				raise ValueError("No raw tiles to correct. Load the data first.")

			if shutil.which("conda") is None:
				raise RuntimeError("conda not found. Make sure conda is installed and in PATH.")

			env_name = "FOCUS_BaSiCpy"
			result = subprocess.run(["conda", "env", "list", "--json"], capture_output=True, text=True)
			if result.returncode != 0:
				raise RuntimeError(f"Failed to list conda environments: {result.stderr}")
			env_paths = json.loads(result.stdout).get("envs", [])
			if not any(env_name in path for path in env_paths):
				raise RuntimeError(f"Conda environment '{env_name}' does not exist.")

			main_script = os.path.join(_TOOLS_DIR, "BaSiCpy", "main.py")
			if not os.path.isfile(main_script):
				raise FileNotFoundError(f"BaSiCpy main script not found at {main_script}")

			def run_correction(channel_idx: int):
				input_file = os.path.join(self.output_path, f"basic_input_{channel_idx}.npy")
				output_file = os.path.join(self.output_path, f"basic_output_{channel_idx}.npy")
				np.save(input_file, self._raw_tiles[:, channel_idx, :, :])

				env = os.environ.copy()
				env["JAX_PLATFORM_NAME"] = "cpu"

				subprocess.run([
					"conda", "run", "-n", env_name, "python", main_script, self.output_path, str(channel_idx)
				], check=True, env=env)

				# Wait for output file
				timeout, poll_interval = 10, 0.2
				start = time.time()
				while True:
					if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
						break
					if time.time() - start > timeout:
						raise TimeoutError(f"Output file {output_file} not created after {timeout}s")
					time.sleep(poll_interval)

				corrected = np.load(output_file)
				os.remove(input_file)
				os.remove(output_file)
				return channel_idx, corrected

			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			self._basic_corrected_tiles = np.zeros_like(self._raw_tiles, dtype=np.float32)

			with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
				futures = {executor.submit(run_correction, idx): idx for idx in range(self._raw_tiles.shape[1])}
				for future in _reporter.tqdm(concurrent.futures.as_completed(futures), desc="2/5 - Applying BaSiC Correction", total=len(futures), unit='channel'):
					channel_idx, corrected_channel = future.result()
					self._basic_corrected_tiles[:, channel_idx, :, :] = corrected_channel

			# Global normalization to [0, 1]
			min_val = self._basic_corrected_tiles.min()
			max_val = self._basic_corrected_tiles.max()
			if max_val > min_val:
				self._basic_corrected_tiles -= min_val
				self._basic_corrected_tiles /= (max_val - min_val)

			np.save(cache_file, self._basic_corrected_tiles)
		else:
			self._basic_corrected_tiles = np.load(cache_file)
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step("2/5 - Loaded BaSiC corrected tiles from disk. (Using cached results)")

	def remove_background(self, force_recomputing: bool = False,
		bg_min_area_fraction: float = _BG_MIN_AREA_FRACTION,
		otsu_threshold_factor: float = _OTSU_THRESHOLD_FACTOR,
		min_object_size: int = _MIN_OBJECT_SIZE
	) -> None:
		"""
		Remove background from BaSiC corrected tiles using Otsu thresholding on a quick-stitched mosaic.

		Parameters
		----------
		force_recomputing : bool
			Force reprocessing even if cached results exist.
		bg_min_area_fraction : float
			Minimum contour area as fraction of total image area.
		otsu_threshold_factor : float
			Multiplicative factor applied to Otsu threshold.
		min_object_size : int
			Minimum connected component size in pixels.
		"""
		if self._basic_corrected_tiles is None:
			raise RuntimeError("No BaSiC corrected tiles. Run basic_correct() first.")

		cache_file = os.path.join(self.output_path, "segmented_tiles.npy")

		if force_recomputing or not os.path.exists(cache_file):
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step("3/5 - Removing background from BaSiC corrected tiles")

			self._quick_stitch()

			# Otsu-based segmentation on the quick mosaic (uint8)
			clip_value = np.percentile(self._quick_mosaic, 95)
			clipped_img = np.clip(self._quick_mosaic, None, clip_value).astype(np.uint8)

			otsu_thresh, _ = cv2.threshold(clipped_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
			adjusted_thresh = int(otsu_thresh * otsu_threshold_factor)
			_, thresh = cv2.threshold(self._quick_mosaic, adjusted_thresh, 255, cv2.THRESH_BINARY)
			del clipped_img

			mask_clean = morphology.remove_small_objects(thresh.astype(bool), max_size=min_object_size)
			del thresh
			segmentation_mask = binary_fill_holes(mask_clean)
			del mask_clean

			seg_mask_uint8 = segmentation_mask.astype(np.uint8) * 255
			contours, _ = cv2.findContours(seg_mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

			if contours:
				image_area = seg_mask_uint8.shape[0] * seg_mask_uint8.shape[1]
				area_threshold = bg_min_area_fraction * image_area
				large_contours = [c for c in contours if cv2.contourArea(c) >= area_threshold]

				tissue_mask = np.zeros_like(seg_mask_uint8)
				cv2.drawContours(tissue_mask, large_contours, contourIdx=-1, color=255, thickness=cv2.FILLED)
				segmentation_mask = tissue_mask.astype(bool)
			else:
				print("Warning: No contours found; cannot refine background mask.")
			del seg_mask_uint8

			tiles_masks = self._extract_tiles_segmentation_from_mosaic(
				mosaic=segmentation_mask,
				coordinates=self._tiles_coordinates,
				pixel_size=self.metadata.pixel_size[0],
				tile_size=(self.metadata.scan_height, self.metadata.scan_width)
			)

			segmented_tiles = self._basic_corrected_tiles * tiles_masks
			np.save(cache_file, segmented_tiles)
			self._basic_corrected_tiles = segmented_tiles
		else:
			self._basic_corrected_tiles = np.load(cache_file)
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step("3/5 - Loaded segmented BaSiC corrected tiles from disk. (Using cached results)")

	def _quick_stitch(self) -> None:
		"""Stitch tiles into a quick mosaic (with blending) for background removal only."""
		if self._basic_corrected_tiles is None:
			raise RuntimeError("No BaSiC corrected tiles to stitch.")
		tiles = self._basic_corrected_tiles

		coords_px = np.zeros_like(self._tiles_coordinates, dtype=np.int32)
		for slice_idx, _ in enumerate(self._spectra_slices):
			coords_px[:, slice_idx, :] = (self._tiles_coordinates[:, slice_idx, :] / self.metadata.pixel_size[0]).astype(np.int32)

		min_x, min_y = np.min(coords_px.reshape(-1, 2), axis=0)
		max_x = np.max(coords_px[:, :, 0] + tiles.shape[3])
		max_y = np.max(coords_px[:, :, 1] + tiles.shape[2])

		mosaic_width = max_x - min_x
		mosaic_height = max_y - min_y
		mosaic_shape = (tiles.shape[1], mosaic_height, mosaic_width)

		mosaic = np.zeros(mosaic_shape, dtype=np.float32)
		weights = np.zeros(mosaic_shape, dtype=np.float32)

		tile_weights = distance_transform_edt(np.ones((tiles.shape[2], tiles.shape[3]), dtype=np.float32))
		tile_weights /= tile_weights.max()

		for t in range(tiles.shape[0]):
			tile_height, tile_width = tiles.shape[2], tiles.shape[3]

			for c in range(tiles.shape[1]):
				for slice_idx, (start_ch, end_ch) in enumerate(self._spectra_slices):
					if start_ch <= c <= end_ch:
						x, y = coords_px[t, slice_idx, :] - [min_x, min_y]
						break

				y_start, y_end = max(0, y), min(mosaic_height, y + tile_height)
				x_start, x_end = max(0, x), min(mosaic_width, x + tile_width)
				ty_start, ty_end = max(0, -y), tile_height - max(0, y + tile_height - mosaic_height)
				tx_start, tx_end = max(0, -x), tile_width - max(0, x + tile_width - mosaic_width)

				mosaic[c, y_start:y_end, x_start:x_end] += (
					tiles[t, c, ty_start:ty_end, tx_start:tx_end] * tile_weights[ty_start:ty_end, tx_start:tx_end]
				)
				weights[c, y_start:y_end, x_start:x_end] += tile_weights[ty_start:ty_end, tx_start:tx_end]

		mosaic = np.divide(mosaic, weights, out=np.zeros_like(mosaic), where=weights > 0)

		# PCA → grayscale → CLAHE for visualization
		pca = PCA(n_components=1)
		mosaic_hwc = mosaic.transpose((1, 2, 0))  # (H, W, C)
		H, W, C = mosaic_hwc.shape
		mosaic_flat = mosaic_hwc.reshape(-1, C)

		filter_mask = np.all(mosaic_flat == 0, axis=1)
		if np.all(filter_mask):
			raise RuntimeError("The mosaic is completely black.")

		mosaic_pca = pca.fit_transform(mosaic_flat[~filter_mask])

		p2, p98 = np.percentile(mosaic_pca, (2, 98))
		mosaic_pca_norm = np.clip((mosaic_pca - p2) / (p98 - p2 + 1e-9), 0, 1)

		result = np.zeros((H * W, 1), dtype=np.float32)
		result[~filter_mask] = mosaic_pca_norm
		mosaic_gray = result.reshape(H, W)

		mosaic_uint8 = (mosaic_gray * 255).astype(np.uint8)
		clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
		self._quick_mosaic = clahe.apply(mosaic_uint8)

	def _extract_tiles_segmentation_from_mosaic(self, mosaic: np.ndarray, coordinates: np.ndarray,
		pixel_size: float, tile_size: tuple[int, int]) -> np.ndarray:
		"""Extract per-tile segmentation masks from the global mosaic mask."""
		tile_h, tile_w = tile_size
		n_tiles = coordinates.shape[0]
		n_channels = sum(e - s + 1 for s, e in self._spectra_slices)

		coords_px = (coordinates / pixel_size).astype(np.int32)
		min_x = np.min(coords_px[:, :, 0])
		min_y = np.min(coords_px[:, :, 1])
		coords_px[:, :, 0] -= min_x
		coords_px[:, :, 1] -= min_y

		H, W = mosaic.shape
		tiles = np.zeros((n_tiles, n_channels, tile_h, tile_w), dtype=mosaic.dtype)

		channel_to_scan = {}
		for scan_idx, (start_ch, end_ch) in enumerate(self._spectra_slices):
			for c in range(start_ch, end_ch + 1):
				channel_to_scan[c] = scan_idx

		for t in range(n_tiles):
			for c in range(n_channels):
				scan_idx = channel_to_scan[c]
				x = coords_px[t, scan_idx, 0]
				y = coords_px[t, scan_idx, 1]

				x_start, x_end = max(0, x), min(W, x + tile_w)
				y_start, y_end = max(0, y), min(H, y + tile_h)
				tx_start = x_start - x
				tx_end = tx_start + (x_end - x_start)
				ty_start = y_start - y
				ty_end = ty_start + (y_end - y_start)

				if (x_end > x_start) and (y_end > y_start):
					tiles[t, c, ty_start:ty_end, tx_start:tx_end] = mosaic[y_start:y_end, x_start:x_end]

		return tiles

	def _prepare_for_ashlar(self, tiles: np.ndarray, coordinates: np.ndarray) -> int:
		"""Prepare tiles and coordinates for ASHLAR stitching. Returns the alignment channel index."""
		# Flip y-axis (Leica → OME TIFF convention)
		for s, _ in enumerate(self._spectra_slices):
			coordinates[:, s, 1] = np.max(coordinates[:, s, 1]) - (coordinates[:, s, 1] - np.min(coordinates[:, s, 1]))

		tiles = np.nan_to_num(tiles, nan=0.0)
		tiles = (tiles * 255).astype(np.uint8)

		# Find highest-intensity channel in cycle 0 for alignment
		start_ch_0, end_ch_0 = self._spectra_slices[0]
		tiles_cycle_0 = tiles[:, start_ch_0:end_ch_0 + 1, :, :]
		mean_per_tile_channel = tiles_cycle_0.mean(axis=(2, 3))
		max_avg = mean_per_tile_channel.max(axis=0)
		highest_intensity_channel = start_ch_0 + int(np.argmax(max_avg))

		# Write OME-TIFF input files for each cycle
		for cycle, (start_ch, end_ch) in enumerate(self._spectra_slices):
			tiles_cycle = tiles[:, start_ch:end_ch + 1, :, :]
			coordinates_cycle = coordinates[:, cycle, :]

			output_filename = os.path.join(self.output_path, f'ashlar_input_cycle_{cycle + 1}.ome.tiff')

			with tifffile.TiffWriter(output_filename, ome=True, bigtiff=True) as tif:
				tiles_cycle = tiles_cycle[:, np.newaxis, :, :, :]
				T, Z, C, Y, X = tiles_cycle.shape

				for t in range(T):
					metadata = {
						'Pixels': {
							'PhysicalSizeX': float(self.metadata.pixel_size[0]),
							'PhysicalSizeY': float(self.metadata.pixel_size[1]),
							'PhysicalSizeXUnit': 'µm',
							'PhysicalSizeYUnit': 'µm',
							'SizeT': 1, 'SizeC': C, 'SizeY': Y, 'SizeX': X, 'SizeZ': 1,
							'Type': 'uint8',
						},
						'Channel': [{'Name': f'Channel_{i}'} for i in range(C)],
						'Plane': [
							{'TheT': 0, 'TheC': c, 'TheZ': 0,
							 'PositionX': float(coordinates_cycle[t, 0]),
							 'PositionY': float(coordinates_cycle[t, 1]),
							 'PositionXUnit': 'µm', 'PositionYUnit': 'µm'}
							for c in range(C)
						]
					}
					tif.write(tiles_cycle[t, 0, :, :, :], metadata=metadata,
						tile=(Y, X), compression='zlib')

		return highest_intensity_channel

	def ashlar_stitch(self, force_recomputing: bool = False) -> str:
		"""
		Stitch corrected tiles into a final mosaic using ASHLAR.

		Returns
		-------
		str
			Path to the output OME-TIFF file.
		"""
		output_file = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, "ome.tiff")

		if force_recomputing or not os.path.exists(output_file):
			if self.corrected is None:
				raise ValueError("Run process_raw_tiles() before calling ashlar_stitch().")

			env_name = "FOCUS_ASHLAR"

			align_channel = self._prepare_for_ashlar(
				tiles=self.corrected,
				coordinates=copy.deepcopy(self.tiles_coordinates)
			)
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step(f"5/5 - Stitching tiles with ASHLAR using channel {align_channel} as reference")

			main_script = os.path.join(_TOOLS_DIR, "ASHLAR", "main.py")
			if not os.path.isfile(main_script):
				raise FileNotFoundError(f"ASHLAR main script not found at {main_script}")

			cmd_parts = [
				"conda", "run", "-n", env_name,
				"python", "-u", main_script,
				self.output_path, str(align_channel)
			]
			cmd = " ".join(shlex.quote(part) for part in cmd_parts)

			env = os.environ.copy()
			env.pop("MPLBACKEND", None)

			result = subprocess.run(cmd, shell=True, check=True, executable="/bin/bash", env=env)
			if result.returncode != 0:
				raise RuntimeError(f"ASHLAR stitching failed: {result.stderr}")

			default_output = os.path.join(self.output_path, "ashlar_output.ome.tiff")
			os.rename(default_output, output_file)

			self._mosaic = tifffile.imread(output_file)
			print(f"Sample {self.sample_id}: Stitched mosaic saved to {output_file}")
		else:
			self._mosaic = tifffile.imread(output_file)
			_reporter = getattr(self, '_step_reporter', None) or StepReporter()
			_reporter.step(f"5/5 - Loaded ASHLAR stitched mosaic from disk. (Using cached results)")

		return output_file

	def _force_mosaic_load(self, output_path: str, filename: str):
		self._mosaic = tifffile.imread(os.path.join(output_path, f"{filename}.ome.tiff"))


class RamanDataset(BaseDataset):
	"""Handle a collection of RamanImage samples."""

	def __init__(self, path: str, samples: list[RamanImage]):
		super().__init__(path, samples)

	def process_dataset(self, force_recomputing: bool = False,
		max_workers: int = RamanImage._MAX_WORKERS,
		savgol_window: int = RamanImage._SAVGOL_WINDOW,
		savgol_polyorder: int = RamanImage._SAVGOL_POLYORDER,
		bg_min_area_fraction: float = RamanImage._BG_MIN_AREA_FRACTION,
		otsu_threshold_factor: float = RamanImage._OTSU_THRESHOLD_FACTOR,
		min_object_size: int = RamanImage._MIN_OBJECT_SIZE,
		step_reporter=None
	) -> dict[str, str]:
		"""
		Process each sample through the full Raman preprocessing pipeline.
		All processing parameters are forwarded to per-sample methods.

		Parameters
		----------
		force_recomputing : bool
			Force reprocessing even if cached results exist.
		max_workers : int
			Maximum parallel workers for BaSiC and spectral cleaning.
		savgol_window : int
			Savitzky-Golay filter window length.
		savgol_polyorder : int
			Savitzky-Golay filter polynomial order.
		bg_min_area_fraction : float
			Minimum contour area as fraction of image area for background removal.
		otsu_threshold_factor : float
			Multiplicative factor for Otsu threshold adjustment.
		min_object_size : int
			Minimum connected component size in pixels for morphological cleanup.

		Returns
		-------
		dict[str, str]
			Maps sample IDs to output OME-TIFF paths.
		"""
		reporter = step_reporter or StepReporter()
		processed_samples = {}
		total = len(self.samples)
		for i, sample in enumerate(self.samples):
			reporter.set_sample(sample.sample_id, i + 1, total)
			sample._max_workers = max_workers
			sample._step_reporter = reporter

			try:
				output_expected = MODALITY_PREPROCESSING(sample.source_path, sample.sample_id, sample.modality_name, "ome.tiff")
				if force_recomputing or not os.path.exists(output_expected):
					sample.load_source()
					sample.basic_correct(force_recomputing=force_recomputing)
					sample.remove_background(
						force_recomputing=force_recomputing,
						bg_min_area_fraction=bg_min_area_fraction,
						otsu_threshold_factor=otsu_threshold_factor,
						min_object_size=min_object_size
					)
					sample.process_raw_tiles(
						parallel=True,
						force_recomputing=force_recomputing,
						savgol_window=savgol_window,
						savgol_polyorder=savgol_polyorder
					)
					output_file = sample.ashlar_stitch(force_recomputing=force_recomputing)
				else:
					print(f"Sample {sample.sample_id} already processed. Using cached results.")
					output_file = output_expected

				processed_samples[sample.sample_id] = output_file

				# Clean up intermediate cache files
				for cache_name in ["basic_corrected_tiles.npy", "raman_corrected_tiles.npy", "segmented_tiles.npy"]:
					cache_path = os.path.join(sample.output_path, cache_name)
					if os.path.exists(cache_path):
						os.remove(cache_path)
			except Exception as e:
				print(f"Error processing sample {sample.sample_id}: {e}")

		return processed_samples


# --- Modality Registration ---

def _create_raman_samples(path, sample_ids, modality_name, settings):
	max_workers = settings.get(RamanPreprocessingParams.MAX_WORKERS, RamanImage._MAX_WORKERS)
	return [
		RamanImage(source_path=path, sample_id=sid, modality_name=modality_name, max_workers=max_workers)
		for sid in sample_ids
	]

def _create_raman_dataset(path, samples, settings):
	return RamanDataset(path=path, samples=samples)

def _extract_raman_settings(settings):
	return {
		'force_recomputing': settings.get(RamanPreprocessingParams.FORCE_RECOMPUTING, False),
		'max_workers': settings.get(RamanPreprocessingParams.MAX_WORKERS, RamanImage._MAX_WORKERS),
		'savgol_window': settings.get(RamanPreprocessingParams.SAVGOL_WINDOW, RamanImage._SAVGOL_WINDOW),
		'savgol_polyorder': settings.get(RamanPreprocessingParams.SAVGOL_POLYORDER, RamanImage._SAVGOL_POLYORDER),
		'bg_min_area_fraction': settings.get(RamanPreprocessingParams.BG_MIN_AREA_FRACTION, RamanImage._BG_MIN_AREA_FRACTION),
		'otsu_threshold_factor': settings.get(RamanPreprocessingParams.OTSU_THRESHOLD_FACTOR, RamanImage._OTSU_THRESHOLD_FACTOR),
		'min_object_size': settings.get(RamanPreprocessingParams.MIN_OBJECT_SIZE, RamanImage._MIN_OBJECT_SIZE),
	}

register_modality('raman', ModalityHandler(
	create_samples=_create_raman_samples,
	create_dataset=_create_raman_dataset,
	extract_settings=_extract_raman_settings,
))
