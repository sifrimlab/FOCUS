import os, tifffile, cv2, timm, torch, huggingface_hub, tqdm, anndata, czifile
import numpy as np
import scipy.ndimage as ndi
import skimage.filters as filters
import skimage.morphology as morphology
from skimage import color
import matplotlib.pyplot as plt
from ome_types.model import OME, Image, Pixels, Channel, TiffData, Plane, Color
from scipy.ndimage import binary_fill_holes

import utils as utils
from constants import SegmentationBackgroundColor

class PatchEmbeddingExtractor:
	def __init__(self, hf_token: str = None):

		self.hf_token = hf_token
		self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

		huggingface_hub.login(token=self.hf_token)

		# Create the patch encoder model
		self.patch_encoder = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
		self.patch_encoder.eval()
		self.patch_encoder.to(self.device)

	def extract_patches(self, img: np.ndarray, patch_size: int = 224, patch_centers: np.ndarray = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
		"""
		Extract patches from an image. If patch_centers is None, extracts non-overlapping patches.
		If patch_centers is provided, extracts patches centered at those coordinates.
		
		Parameters
		----------
		img : np.ndarray
			The input image as a NumPy array of shape (H, W, C).
		patch_size : int
			The size of the patches to extract (default is 224).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the patch centers.
			If None, non-overlapping patches are extracted across the entire image.
		
		Returns
		-------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		top_left_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch.
		center_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the center of each patch.
		"""
		
		# Handle different formats
		if img.ndim == 2:
			img = img[..., None]
		h, w, c = img.shape
		if c == 1:
			img = np.repeat(img, 3, axis=2)
		if c == 4:
			img = img[..., :3]
		
		half_patch = patch_size // 2
		
		if patch_centers is not None:
			# Extract patches centered at provided coordinates
			patch_centers = np.asarray(patch_centers, dtype=np.float32)
			n_patches = patch_centers.shape[0]
			
			patches = []
			top_left_coords = []
			center_coords = []
			
			for i in range(n_patches):
				cx, cy = patch_centers[i]
				
				# Compute top-left corner
				x0 = int(cx - half_patch)
				y0 = int(cy - half_patch)
				
				# Clamp to image boundaries
				x0 = max(0, min(x0, w - patch_size))
				y0 = max(0, min(y0, h - patch_size))
				
				# Extract patch
				patch = img[y0:y0+patch_size, x0:x0+patch_size, :]
				
				# Handle edge cases where patch might be smaller than patch_size
				if patch.shape[0] < patch_size or patch.shape[1] < patch_size:
					padded = np.zeros((patch_size, patch_size, 3), dtype=img.dtype)
					padded[:patch.shape[0], :patch.shape[1], :] = patch
					patch = padded
				
				patches.append(patch)
				top_left_coords.append([x0, y0])
				
				# Compute actual center (might differ slightly from requested if clamped)
				actual_center_x = x0 + half_patch
				actual_center_y = y0 + half_patch
				center_coords.append([actual_center_x, actual_center_y])
			
			patches = np.array(patches, dtype=np.float32)
			top_left_coordinates = np.array(top_left_coords, dtype=np.float32)
			center_coordinates = np.array(center_coords, dtype=np.float32)
			
		else:
			# Extract non-overlapping patches
			n_patches_y = h // patch_size
			n_patches_x = w // patch_size
			
			# Crop image to fit exact number of patches
			h_crop = n_patches_y * patch_size
			w_crop = n_patches_x * patch_size
			img = img[:h_crop, :w_crop, :]
			
			# Reshape into patches using stride tricks
			patches = img.reshape(n_patches_y, patch_size, n_patches_x, patch_size, 3)
			patches = patches.transpose(0, 2, 1, 3, 4)  # (n_y, n_x, h, w, c)
			patches = patches.reshape(-1, patch_size, patch_size, 3).astype(np.float32)
			
			# Generate top-left coordinates
			y_coords = np.arange(n_patches_y) * patch_size
			x_coords = np.arange(n_patches_x) * patch_size
			xx, yy = np.meshgrid(x_coords, y_coords)
			top_left_coordinates = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.float32)
			
			# Compute center coordinates
			center_coordinates = top_left_coordinates + half_patch
		
		return patches, top_left_coordinates, center_coordinates

	def filter_empty_patches(self, patches: np.ndarray, topleft_coordinates: np.ndarray, center_coordinates: np.ndarray, background_color: SegmentationBackgroundColor) -> tuple[np.ndarray, np.ndarray]:
		"""
		Filter out patches that are empty (background). A patch is considered empty if the 99% of its pixels are
		the background color.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		topleft_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.
		center_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the center of each patch
			in the original image.
		background_color : SegmentationBackgroundColor
			The background color to use for filtering.
		
		Returns
		----------
		filtered_patches : np.ndarray
			A NumPy array containing only the non-empty patches.
		filtered_coordinates : np.ndarray
			A NumPy array containing the coordinates of the non-empty patches.
		"""

		if background_color == SegmentationBackgroundColor.WHITE:
			bg_color = np.array([1.0, 1.0, 1.0], dtype=np.float32)
		elif background_color == SegmentationBackgroundColor.BLACK:
			bg_color = np.array([0.0, 0.0, 0.0], dtype=np.float32)
		else:
			raise ValueError(f"Unsupported background color: {background_color}")

		# Calculate the number of background pixels in each patch
		bg_mask = np.all(np.isclose(patches, bg_color, atol=1e-3), axis=-1)  # shape (N, patch_size, patch_size)
		bg_pixel_counts = np.sum(bg_mask, axis=(1, 2))  # shape (N,)

		# Determine threshold for filtering (99% background)
		patch_area = patches.shape[1] * patches.shape[2]
		threshold = patch_area * 0.99

		# Filter patches and coordinates
		valid_indices = np.where(bg_pixel_counts < threshold)[0]
		filtered_patches = patches[valid_indices]
		filtered_topleft_coordinates = topleft_coordinates[valid_indices]
		filtered_center_coordinates = center_coordinates[valid_indices]

		return filtered_patches, filtered_topleft_coordinates, filtered_center_coordinates

	def extract_patch_embeddings(self, patches: np.ndarray) -> np.ndarray:
		"""
		Extract embeddings from the image patches using a pre-trained model.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		topleft_coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.

		Returns
		----------
		patch_embeddings : np.ndarray
			A NumPy array of shape (N, embedding_size) containing the patch embeddings before slide refinement.
		"""

		# Convert patches and coordinates to torch tensors
		patches_tensor = torch.from_numpy(patches).permute(0, 3, 1, 2).to(self.device)  # shape (N, C, H, W)

		# Create a Dataset and a DataLoader
		dataset = torch.utils.data.TensorDataset(patches_tensor)
		dataloader = torch.utils.data.DataLoader(dataset, batch_size=32, shuffle=False)

		embeddings: list[np.ndarray] = []

		# Extract embeddings for the current level
		with torch.inference_mode():
			for batch in dataloader:
				input_tensor = batch[0].to(self.device)                      			# Shape [B, 3, 224, 224]
				embeddings.append(self.patch_encoder(input_tensor).cpu().numpy())       # Shape [B, 1536]

		embeddings: np.ndarray = np.concatenate(embeddings, axis=0)  					# Shape [N, 1536]

		return embeddings

class MicroscopyImage():
	def __init__(self, source_path: str, sample_id: str, modality_name: str, patch_extractor: PatchEmbeddingExtractor | None = None) -> None:
		'''
		Process a microscopy image to uniform the format, enhance the colors prepare it for registration.
		'''

		# Check if the input path exists and we can read
		if not os.path.exists(source_path):
			raise ValueError(f"The path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise ValueError(f"The path {source_path} is not readable.")

		if patch_extractor is not None and isinstance(patch_extractor, PatchEmbeddingExtractor) == False:
			raise ValueError(f"Invalid patch_extractor: {patch_extractor}. Expected an instance of PatchEmbeddingExtractor.")


		self.source_path = source_path
		self.sample_id = sample_id
		self.modality_name = modality_name
		self.patch_extractor = patch_extractor

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

	@property
	def patch_extractor(self) -> PatchEmbeddingExtractor | None:
		return self._patch_extractor

	@patch_extractor.setter
	def patch_extractor(self, value: PatchEmbeddingExtractor | None) -> None:
		if value is not None and not isinstance(value, PatchEmbeddingExtractor):
			raise ValueError(f"Invalid patch_extractor: {value}. Expected an instance of PatchEmbeddingExtractor or None.")
		self._patch_extractor = value

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

		# Ensure that the image has at most 3 channels
		if image.shape[-1] > 3:
			image = image[:, :, :3]

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

		assert img.ndim == 3 and img.shape[2] == 3, "Expecting RGB image [H,W,3]"
		assert img.dtype == np.float32, "Expecting float32 array"

		H_base, W_base, _ = img.shape

		# 1. Generate the downscaled RGB images for the pyramid
		pyramid_data = []
		for i in range(levels):
			scale = 0.5 ** i
			h_scaled = max(1, int(H_base * scale))
			w_scaled = max(1, int(W_base * scale))
			resized = cv2.resize(img, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
			pyramid_data.append(resized)

		# 2. Build the OME-XML with separate <Image> entries, each with interleaved RGB pixels
		ome = OME()
		ifd_counter = 0
		for i, level_img in enumerate(pyramid_data):
			H, W, _ = level_img.shape

			image_block = Image(
				id=f"Image:{i}",
				name=f"ResolutionLevel_{i}",
				pixels=Pixels(
					id=f"Pixels:{i}",
					dimension_order="XYCZT",
					type="float",
					size_x=W,
					size_y=H,
					size_z=1,
					size_c=1,  # Single channel since RGB pixels are interleaved
					size_t=1,
					interleaved=True,  # Interleaved RGB
					channels=[
						Channel(id=f"Channel:{i}:0", name="RGB", samples_per_pixel=3),
					],
					planes=[
						Plane(the_c=0, the_z=0, the_t=0)
					],
					tiff_data_blocks=[
						TiffData(ifd=ifd_counter, plane_count=1)
					],
				)
			)
			ome.images.append(image_block)
			ifd_counter += 1

		xml_metadata = ome.to_xml()

		# 3. Write all image planes sequentially to a single TIFF file
		import tifffile

		with tifffile.TiffWriter(output_file, bigtiff=True) as tif:
			for c, level_img in enumerate(pyramid_data):
				# level_img shape is (H, W, 3), interleaved RGB float32 pixels
				description = xml_metadata if c == 0 else None
				tif.write(
					level_img,
					description=description,
					photometric='rgb',
					metadata={'axes': 'YXC'},  # Y: rows, X: columns, C: channels interleaved
					compression="zlib"
				)

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
		output_ome_tiff = os.path.join(self.output_folder, f"{self.sample_id}_processed.ome.tiff")

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

	def compute_embeddings(self, 
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		patch_size: int = 224,
		patch_centers: np.ndarray = None,
		force_recomputing: bool = False
	) -> anndata.AnnData:
		'''
		Use the patch extractor to compute patch embeddings for the image.
		
		Parameters
		----------
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.
		patch_size : int
			The size of the patches to use for background removal (default is 224).
		patch_centers : np.ndarray, optional
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the patch centers to extract.
			If None, non-overlapping patches are extracted across the entire image foreground.
		force_recomputing : bool
			Whether to force recomputation of the embeddings even if the output files already exist (default is False).

		Returns
		-------
		adata : anndata.AnnData
			An AnnData object containing the patch embeddings and coordinates.
		'''

		if self.patch_extractor is None:
			raise ValueError("Patch extractor is not defined for this MicroscopyImage instance.")

		# Check if the processed image exists
		if not os.path.exists(os.path.join(self.output_folder, f"{self.sample_id}_processed.ome.tiff")):
			raise ValueError(f"The processed image does not exist for sample {self.sample_id}. Please run process_image() first.")
		
		print(f"2/2 - Computing patch embeddings")
		
		# Check if the embeddings already exist
		if force_recomputing == False and os.path.exists(os.path.join(self.output_folder, f"{self.sample_id}_patch_embeddings.h5ad")):
			print(f"2/2 - Patch Embeddings already exist. Using cached results.")
			adata = anndata.read_h5ad(os.path.join(self.output_folder, f"{self.sample_id}_patch_embeddings.h5ad"))
			return adata
		
		# Load the processed image
		with tifffile.TiffFile(os.path.join(self.output_folder, f"{self.sample_id}_processed.ome.tiff")) as f:
			image = f.asarray()
			# Get the first image (highest resolution)
			if image.ndim > 3:
				image = image[0]

		# Extract patch embeddings
		patches, topleft_coordinates, center_coordinates = self.patch_extractor.extract_patches(image, patch_size, patch_centers)
		patches, topleft_coordinates, center_coordinates = self.patch_extractor.filter_empty_patches(patches, topleft_coordinates, center_coordinates, background_color)
		patch_embeddings = self.patch_extractor.extract_patch_embeddings(patches)

		# Save the AnnData file with patch embeddings
		adata = anndata.AnnData(patch_embeddings)
		adata.obs_names = [f"{self.sample_id}_{idx}" for idx in range(adata.n_obs)]
		adata.obsm['spatial'] = center_coordinates
		adata.obsm['topleft_coordinates'] = topleft_coordinates
		adata.uns['patch_size'] = patch_size
		adata.obs['sample_id'] = self.sample_id
		adata.write_h5ad(os.path.join(self.output_folder, f"{self.sample_id}_patch_embeddings.h5ad"))
		return adata
	
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