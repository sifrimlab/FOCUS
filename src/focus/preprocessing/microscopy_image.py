import os, tifffile, cv2, anndata, czifile
import numpy as np
import skimage.morphology as morphology
from ome_types.model import OME, Image, Pixels, Channel, TiffData, Plane, Color
from scipy.ndimage import binary_fill_holes

import focus.utils as utils
from focus.constants import SegmentationBackgroundColor

from focus.constants import MODALITY_PREPROCESSING

class MicroscopyImage():
	def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
		'''
		Process a microscopy image to uniform the format, enhance the colors prepare it for registration.
		'''

		# Check if the input path exists and we can read
		if not os.path.exists(source_path):
			raise ValueError(f"The path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise ValueError(f"The path {source_path} is not readable.")

		self.source_path = source_path
		self.sample_id = sample_id
		self.modality_name = modality_name

		# Find the first .tiff or .tif file in the directory
		self.filename = None
		for f in os.listdir(os.path.join(source_path, sample_id, modality_name)):
			if f.endswith(".tiff") or f.endswith(".tif"):
				self.filename = os.path.join(source_path, sample_id, modality_name, f)
				break
			elif f.endswith(".czi"):
				self.filename = os.path.join(source_path, sample_id, modality_name, f)
				break
		# Check if we found a valid filename
		if self.filename is None:
			raise ValueError(f"No valid TIFF file found in {os.path.join(source_path, sample_id, modality_name)}")
		
		# Define the standard output folder
		self.output_folder = os.path.join(source_path, sample_id, 'preprocessing', modality_name)
		if not os.path.exists(self.output_folder):
			os.makedirs(self.output_folder)

	def _load_tiff(self, file: str) -> np.ndarray:
		'''
		Read a tiff/tif file and return the image with color channel always in the last dimension
		(swap channels if needed).
		The image is converted to float32 and normalized to 0-1.

		Parameters
		----------
		file : str
			The path to the tiff/tif file.
		
		Returns
		----------
		image : np.ndarray
			The image with color channel always in the last dimension.
		'''

		# Check if the file exists and is a tiff/tif file
		if not os.path.isfile(file) or not (file.endswith(".tiff") or file.endswith(".tif")):
			raise ValueError(f"The file {file} does not exist or is not a tiff/tif file.")

		image = None

		# Read the tiff/tif file
		with tifffile.TiffFile(file) as f:
			# Get the image data
			image = f.asarray()

			# Determine the channel index by looking for the smallest dimension and place it last
			# Skip if it's a grayscale image
			if len(image.shape) > 2:
				channel_index = np.argmin(image.shape)
				if channel_index == 0:
					image = image.transpose(1, 2, 0)
				elif channel_index == 1:
					image = image.transpose(0, 2, 1)

		# Convert the image to float32
		image = image.astype(np.float32)

		# Normalize the image to 0-1
		image = image / np.max(image)

		# Ensure that the image has at most 3 channels
		if image.shape[-1] > 3:
			image = image[:, :, :3]

		return image
	
	def _load_czi(self, file: str) -> np.ndarray:
		'''
		Read a CZI file and return the image with color channel always in the last dimension
		(swap channels if needed).
		The image is converted to float32 and normalized to 0-1.

		Parameters
		----------
		file : str
			The path to the CZI file.
		
		Returns
		----------
		image : np.ndarray
			The image with color channel always in the last dimension.
		'''

		# Check if the file exists and is a CZI file
		if not os.path.isfile(file) or not file.endswith(".czi"):
			raise ValueError(f"The file {file} does not exist or is not a CZI file.")
		
		image = None

		# Read the CZI file
		with czifile.CziFile(file) as czi:
			image = czi.asarray()

			# Always consider only the first image if multiple are present
			if image.ndim > 3:
				if image.shape[0] > 1:
					print("WARNING: CZI file has more than 3 dimensions. Considering only the first image.")
				while image.ndim > 3:
					image = image[0]

			# Determine the channel index by looking for the smallest dimension and place it last
			# Skip if it's a grayscale image
			if len(image.shape) > 2:
				channel_index = np.argmin(image.shape)
				if channel_index == 0:
					image = image.transpose(1, 2, 0)
				elif channel_index == 1:
					image = image.transpose(0, 2, 1)

		# Convert the image to float32
		image = image.astype(np.float32)

		# Normalize the image to 0-1
		image = image / np.max(image)

		# If the image is grayscale with shape (H, W), convert to (H, W, 1)
		if image.ndim == 2:
			image = image[:, :, np.newaxis]

		return image

	def _save_image_pyramid(self, img: np.ndarray, output_file: str, levels: int = 4):
		"""
		Saves an RGB image as a fully compliant OME-TIFF containing multiple 
		independent images, one for each resolution level, interleaved RGB pixels.

		Parameters
		----------
		img : np.ndarray
			The input RGB image as a NumPy array of shape (H, W, 3) and dtype float32.
		output_file : str
			The path to the output OME-TIFF file.
		levels : int
			The number of resolution levels to generate (default is 4).
		"""

		assert img.dtype == np.float32, "Expecting float32 array"
		
		# Normalize to [H,W,C] shape
		if img.ndim == 2:
			img = img[..., np.newaxis]  # Grayscale -> [H,W,1]
		H_base, W_base, C = img.shape
		is_rgb = (C == 3)

		# 1. Generate pyramid (resize preserves shape type)
		pyramid_data = []
		for i in range(levels):
			scale = 0.5 ** i
			h_scaled = max(1, int(H_base * scale))
			w_scaled = max(1, int(W_base * scale))
			resized = cv2.resize(img, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
			pyramid_data.append(resized)

		# 2. Build OME-XML
		ome = OME()
		ifd_counter = 0
		for i, level_img in enumerate(pyramid_data):
			# Safe shape unpack
			if len(level_img.shape) == 3:
				H, W, CC = level_img.shape  # CC to avoid overwriting C
			else:
				H, W = level_img.shape
				CC = 1

			if is_rgb:
				# RGB interleaved
				image_block = Image(
					id=f"Image:{i}", name=f"ResolutionLevel_{i}",
					pixels=Pixels(
						id=f"Pixels:{i}", dimension_order="XYCZT", type="float",
						size_x=W, size_y=H, size_z=1, size_c=1, size_t=1, interleaved=True,
						channels=[Channel(id=f"Channel:{i}:0", name="RGB", samples_per_pixel=3)],
						planes=[Plane(the_c=0, the_z=0, the_t=0)],
						tiff_data_blocks=[TiffData(ifd=ifd_counter, plane_count=1)]
					)
				)
				ifd_counter += 1
			else:
				# Multi/single-channel separate planes
				image_block = Image(
					id=f"Image:{i}", name=f"ResolutionLevel_{i}",
					pixels=Pixels(
						id=f"Pixels:{i}", dimension_order="XYCZT", type="float",
						size_x=W, size_y=H, size_z=1, size_c=CC, size_t=1,
						channels=[Channel(id=f"Channel:{i}:{c}", name=f"Channel_{c}") for c in range(CC)],
						planes=[Plane(the_c=c, the_z=0, the_t=0) for c in range(CC)],
						tiff_data_blocks=[TiffData(ifd=ifd_counter + c, plane_count=1) for c in range(CC)]
					)
				)
				ifd_counter += CC
			ome.images.append(image_block)

		xml_metadata = ome.to_xml()

		# 3. Write TIFF
		with tifffile.TiffWriter(output_file, bigtiff=True) as tif:
			for c, level_img in enumerate(pyramid_data):
				if is_rgb:
					description = xml_metadata if c == 0 else None
					tif.write(level_img, description=description, photometric='rgb',
							metadata={'axes': 'YXC'}, compression="zlib")
				else:
					# Write each channel slice
					if len(level_img.shape) == 2:
						ch_data = [level_img]
					else:
						ch_data = [level_img[:,:,ch] for ch in range(C)]
					for ch_idx, ch_img in enumerate(ch_data):
						description = xml_metadata if c == 0 and ch_idx == 0 else None
						tif.write(ch_img, description=description, photometric='minisblack',
								metadata={'axes': 'YX'}, compression="zlib")

		return output_file

	def _remove_background(self, image: np.ndarray, background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE, min_object_coverage: float = 0.01) -> np.ndarray:
		"""
		Remove background from an H&E image, preserving all tissue areas that cover an area larger than
		image_area * min_object_coverage, replacing the background with the specified color.
		
		Parameters
		----------
		image : np.ndarray
			The input RGB image as a NumPy array of shape (H, W, 3).
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal.
		min_object_coverage : float
			The minimum coverage (relative to the image area) for tissue areas to keep when removing background.

		Returns
		-------
		output_image : np.ndarray
			The image with background removed and filled with the specified color.
		"""
		assert image.ndim == 3 and image.shape[2] == 3

		if background_color == SegmentationBackgroundColor.WHITE:
			background_color = (1.0, 1.0, 1.0)
		elif background_color == SegmentationBackgroundColor.BLACK:
			background_color = (0.0, 0.0, 0.0)
		else:
			raise ValueError(f"Unsupported background color: {background_color}")
		
		# Check image dtype and convert to uint8 if needed
		original_dtype = image.dtype
		if image.dtype == np.float32 or image.dtype == np.float64:
			image_uint8 = (image * 255).astype(np.uint8)
		elif image.dtype == np.uint8:
			image_uint8 = image

		# Replace black pixels with white to avoid issues in thresholding
		black_pixels = np.all(image_uint8 == [0, 0, 0], axis=-1)
		image_uint8[black_pixels] = [255, 255, 255]

		# Convert RGB image to grayscale
		image_uint8 = cv2.cvtColor(image_uint8, cv2.COLOR_RGB2GRAY)

		# Invert grayscale image (assuming white background to black)
		image_uint8 = cv2.bitwise_not(image_uint8)

		# Clip intensities at 99th percentile to reduce oversaturation impact
		clip_value = np.percentile(image_uint8, 99)
		clipped_img = np.clip(image_uint8, None, clip_value).astype(np.uint8)

		# Apply Gaussian blur to reduce noise
		clipped_img = cv2.GaussianBlur(clipped_img, (251, 251), 0)

		# Compute Otsu threshold on clipped image
		otsu_thresh, _ = cv2.threshold(clipped_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

		# Threshold original image with adjusted threshold (no scaling needed)
		_, thresh = cv2.threshold(image_uint8, otsu_thresh, 255, cv2.THRESH_BINARY)

		# Remove small objects and fill holes
		mask_clean = morphology.remove_small_objects(thresh.astype(bool), min_size=500)
		segmentation_mask = binary_fill_holes(mask_clean)

		# Convert mask for contour finding
		seg_mask_uint8 = segmentation_mask.astype(np.uint8) * 25

		# Find contours
		contours, _ = cv2.findContours(seg_mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

		if contours:
			# Total image area (number of pixels)
			image_area = seg_mask_uint8.shape[0] * seg_mask_uint8.shape[1]

			# Threshold at relative to image area
			area_threshold = min_object_coverage * image_area

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
		
		# Apply the mask to the original image
		output_image = np.zeros_like(image, dtype=image.dtype)
		output_image[segmentation_mask] = image[segmentation_mask]
		output_image[~segmentation_mask] = background_color

		return output_image
	
	def _crop_image(self, image: np.ndarray, background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE) -> np.ndarray:
		'''
		Crop the image to the bounding box of the tissue area.

		Parameters
		----------
		image : np.ndarray
			The input RGB image as a NumPy array of shape (H, W, 3).
		background_color : SegmentationBackgroundColor
			The color used to identify the background.

		Returns
		-------
		cropped_image : np.ndarray
			The cropped image.
		'''

		assert image.ndim == 3 and image.shape[2] == 3

		if background_color == SegmentationBackgroundColor.WHITE:
			bg_color = np.array([1.0, 1.0, 1.0], dtype=np.float32)
		elif background_color == SegmentationBackgroundColor.BLACK:
			bg_color = np.array([0.0, 0.0, 0.0], dtype=np.float32)
		else:
			raise ValueError(f"Unsupported background color: {background_color}")

		# Create a mask of non-background pixels
		bg_mask = np.all(np.isclose(image, bg_color, atol=1e-3), axis=-1)  # shape (H, W)
		non_bg_mask = ~bg_mask

		# Find bounding box of non-background area
		rows = np.any(non_bg_mask, axis=1)
		cols = np.any(non_bg_mask, axis=0)
		if not np.any(rows) or not np.any(cols):
			raise ValueError("The image appears to be entirely background; cannot crop.")

		ymin, ymax = np.where(rows)[0][[0, -1]]
		xmin, xmax = np.where(cols)[0][[0, -1]]

		# Add a 250 pixel margin
		margin = 250
		ymin = max(0, ymin - margin)
		ymax = min(image.shape[0] - 1, ymax + margin)
		xmin = max(0, xmin - margin)
		xmax = min(image.shape[1] - 1, xmax + margin)

		# Crop the image
		cropped_image = image[ymin:ymax+1, xmin:xmax+1, :]

		return cropped_image

	def preview_image(self) -> np.ndarray:
		'''
		Load and return a preview of the microscopy image.

		Returns
		-------
		image : np.ndarray
			The preview image as a NumPy array.
		'''

		# Load the input file
		if self.filename.endswith(".czi"):
			print(f"Loading CZI from file {self.filename}")
			image = self._load_czi(self.filename)
		else:
			print(f"Loading TIFF from file {self.filename}")
			image = self._load_tiff(self.filename)

		return image

	def process_image(self, 
		color_enhancement: bool = True,
		remove_background: bool = True,
		crop_to_tissue: bool = True,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		pyramid_levels: int = 4,
		min_object_coverage: float = 0.01,
		force_recomputing: bool = False
		) -> str:
		'''
		Preprocess a microscopy image to uniform the format, enhance the colors, remove background and crop to tissue area.
		The result is saved as a multi-resolution OME-TIFF file. The result is saved in the output folder defined during initialization.

		Parameters
		----------
		remove_background : bool
			Whether to remove the background using Meta SAM2 (default is True).
		color_enhancement : bool
			Whether to enhance the colors using gamma correction and contrast enhancement (default is True).
		crop_to_tissue : bool
			Whether to crop the image to the tissue area after background removal (default is True).
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.
		pyramid_levels : int
			The number of pyramid levels to save in the output OME-TIFF (default is 3).
		min_tissue_area : float
			The minimum area (relative to the image size) for tissue areas to keep when removing background (default is 0.05).
		force_recomputing : bool
			Whether to force recomputation of the preprocessing even if the output files already exist (default is False).

		Returns
		-------
		output_ome_tiff : str
			The path to the processed OME-TIFF file.
		'''

		# Check the inputs
		if type(color_enhancement) is not bool:
			raise ValueError(f"Invalid value for color_enhancement: {color_enhancement}. Expected a boolean.")
		if type(remove_background) is not bool:
			raise ValueError(f"Invalid value for remove_background: {remove_background}. Expected a boolean.")
		if type(crop_to_tissue) is not bool:
			raise ValueError(f"Invalid value for crop_to_tissue: {crop_to_tissue}. Expected a boolean.")
		if background_color not in SegmentationBackgroundColor.list():
			raise ValueError(f"Invalid value for background_color: {background_color}. Expected one of {SegmentationBackgroundColor.list()}.")
		if type(min_object_coverage) is not float or min_object_coverage < 0 or min_object_coverage > 1:
			raise ValueError(f"Invalid value for min_object_coverage: {min_object_coverage}. Expected a float between 0 and 1.")
		if type(force_recomputing) is not bool:
			raise ValueError(f"Invalid value for force_recomputing: {force_recomputing}. Expected a boolean.")
		if type(pyramid_levels) is not int or pyramid_levels <= 0:
			raise ValueError(f"Invalid value for pyramid_levels: {pyramid_levels}. Expected a positive integer.")
		
		# Check if the results already exist
		output_ome_tiff = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, 'ome.tiff')

		if force_recomputing == False and os.path.exists(output_ome_tiff):
			print(f"Processed image already exists. Using cached results.")
			return output_ome_tiff
		
		# Load the input file
		if self.filename.endswith(".czi"):
			print(f"1/5 - Loading CZI from file {self.filename}")
			image = self._load_czi(self.filename)
		else:
			print(f"1/5 - Loading TIFF from file {self.filename}")
			image = self._load_tiff(self.filename)

		# Enhance colors if needed
		if color_enhancement:
			print(f"2/5 - Enhancing colors")
			image = utils.gamma_correction(image)
			image = utils.enhance_contrast(image)
		else:
			print(f"2/5 - Color enhancement not required")

		# Force the image to be float32
		image = image.astype(np.float32)

		# Remove background if needed
		if remove_background:
			print(f"3/5 - Removing background")
			image = self._remove_background(image, background_color=background_color, min_object_coverage=min_object_coverage)
		else:
			print(f"3/5 - Background removal not required")

		# Crop to tissue area if needed
		if crop_to_tissue:
			print(f"4/5 - Cropping to tissue area")
			image = self._crop_image(image, background_color=background_color)
		else:
			print(f"4/5 - Cropping to tissue area not required")

		# Save the processed image as a multi-resolution OME-TIFF
		print(f"5/5 - Saving processed image as OME-TIFF with {pyramid_levels} pyramid levels")
		self._save_image_pyramid(image, output_ome_tiff, levels=pyramid_levels)
		return output_ome_tiff

class MicroscopyImageDataset:
	'''
	Handle a collection of Microscopy Images

	Parameters
	----------
	path : str
		The root path where the samples are stored.
	samples : list[MicroscopyImage]
		A list of MicroscopyImage objects to be included in the dataset.
	'''

	def __init__(self, path: str, samples: list[MicroscopyImage]):

		for sample in samples:
			if isinstance(sample, MicroscopyImage) == False:
				raise ValueError(f"Invalid sample: {sample}. Expected an instance of MicroscopyImage.")
			
		self.samples = samples
		self.dataset_source_path = path

	def process_dataset(self, 
		color_enhancement: bool = True,
		remove_background: bool = True,
		crop_to_tissue: bool = True,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		pyramid_levels: int = 4,
		min_object_coverage: float = 0.05,
		force_recomputing: bool = False
	) -> dict[str, str]:
		'''
		Preprocess all microscopy images in the dataset.

		Parameters
		----------
		remove_background : bool
			Whether to remove the background using Meta SAM2 (default is True).
		color_enhancement : bool
			Whether to enhance the colors using gamma correction and contrast enhancement (default is True).
		crop_to_tissue : bool
			Whether to crop the image to the tissue area after background removal (default is True).
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.
		pyramid_levels : int
			The number of pyramid levels to save in the output OME-TIFF (default is 3).
		min_tissue_area : float
			The minimum area (relative to the image size) for tissue areas to keep when removing background (default is 0.05).
		force_recomputing : bool
			Whether to force recomputation of the preprocessing even if the output files already exist (default is False).
		'''

		processed_samples = {}
		for sample in self.samples:
			print(f"Processing sample: {sample.sample_id}")

			try:
				output_file = sample.process_image(
					color_enhancement=color_enhancement,
					remove_background=remove_background,
					crop_to_tissue=crop_to_tissue,
					background_color=background_color,
					pyramid_levels=pyramid_levels,
					min_object_coverage=min_object_coverage,
					force_recomputing=force_recomputing
				)
				processed_samples[sample.sample_id] = output_file
			except Exception as e:
				print(f"Error processing sample {sample.sample_id}: {e}")
		return processed_samples