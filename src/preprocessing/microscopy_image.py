import os, tifffile, cv2, timm, torch, huggingface_hub, tqdm, anndata
import numpy as np
import scipy.ndimage as ndi
import skimage.filters as filters
import skimage.morphology as morphology
from skimage import color
from ome_types.model import OME, Image, Pixels, Channel, TiffData, Plane, Color

from .gigapath.slide_encoder import create_model

import utils as utils
from constants import ContainerEngine, SegmentationBackgroundColor

class PatchEmbeddingExtractor:
	def __init__(self, hf_token: str = None):

		self.hf_token = hf_token
		self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

		huggingface_hub.login(token=self.hf_token)

		# Create the patch encoder model
		self.patch_encoder = timm.create_model("hf_hub:prov-gigapath/prov-gigapath", pretrained=True)
		self.patch_encoder.eval()
		self.patch_encoder.to(self.device)

		# Create the slide encoder model
		self.slide_encoder = create_model("hf_hub:prov-gigapath/prov-gigapath", "gigapath_slide_enc12l768d", 1536)
		self.slide_encoder.eval()
		self.slide_encoder.to(self.device)

	def extract_patches(self, img: np.ndarray, patch_size: int = 224) -> tuple[np.ndarray, np.ndarray]:
		"""
		Extract non-overlapping patches from an image. Returns the patches and the top-left coordinates
		of each patch in the original image.

		Parameters
		----------
		img : np.ndarray
			The input image as a NumPy array of shape (H, W, C).
		patch_size : int
			The size of the patches to extract (default is 224).
		
		Returns
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.
		"""

		# Handle different formats
		if img.ndim == 2:
			img = img[..., None]
		h, w, c = img.shape
		if c == 1:
			img = np.repeat(img, 3, axis=2)
		if c == 4:
			img = img[..., :3]
		
		# Calculate number of patches
		n_patches_y = h // patch_size
		n_patches_x = w // patch_size
		
		# Crop image to fit exact number of patches
		h_crop = n_patches_y * patch_size
		w_crop = n_patches_x * patch_size
		img = img[:h_crop, :w_crop, :]
		
		# Reshape into patches using stride tricks (very fast!)
		patches = img.reshape(n_patches_y, patch_size, n_patches_x, patch_size, 3)
		patches = patches.transpose(0, 2, 1, 3, 4)  # (n_y, n_x, h, w, c)
		patches = patches.reshape(-1, patch_size, patch_size, 3).astype(np.float32)
		
		# Generate coordinates
		y_coords = np.arange(n_patches_y) * patch_size
		x_coords = np.arange(n_patches_x) * patch_size
		xx, yy = np.meshgrid(x_coords, y_coords)
		coordinates = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.float32)
		
		return patches, coordinates

	def filter_empty_patches(self, patches: np.ndarray, coordinates: np.ndarray, background_color: SegmentationBackgroundColor) -> tuple[np.ndarray, np.ndarray]:
		"""
		Filter out patches that are empty (background). A patch is considered empty if the 99% of its pixels are
		the background color.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
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
		filtered_coordinates = coordinates[valid_indices]

		return filtered_patches, filtered_coordinates

	def extract_patch_embeddings(self, patches: np.ndarray, coordinates: np.ndarray) -> np.ndarray:
		"""
		Extract embeddings from the image patches using a pre-trained model.

		Parameters
		----------
		patches : np.ndarray
			A NumPy array of shape (N, patch_size, patch_size, C) containing the extracted patches.
		coordinates : np.ndarray
			A NumPy array of shape (N, 2) containing the (x, y) coordinates of the top-left corner of each patch
			in the original image.

		Returns
		----------
		embeddings : np.ndarray
			A NumPy array of shape (N, embedding_size) containing the extracted embeddings.
		"""

		# Convert patches and coordinates to torch tensors
		patches_tensor = torch.from_numpy(patches).permute(0, 3, 1, 2).to(self.device)  # shape (N, C, H, W)
		coordinates_tensor = torch.from_numpy(coordinates).to(self.device)  # shape (N, 2)

		# Create a Dataset and a DataLoader
		dataset = torch.utils.data.TensorDataset(patches_tensor)
		dataloader = torch.utils.data.DataLoader(dataset, batch_size=32, shuffle=False)

		embeddings = []

		# Extract embeddings for the current level
		with torch.inference_mode():
			for batch in tqdm.tqdm(dataloader, desc=f"Extracting patch embeddings", unit="batch"):
				input_tensor = batch[0].to(self.device)                      	# Shape [B, 3, 224, 224]
				embeddings.append(self.patch_encoder(input_tensor))        		# Shape [B, 1536]

		embeddings: torch.Tensor = torch.cat(embeddings, dim=0)  				# Shape [N, 1536]

		# Refine the embeddings using the slide encoder
		slide_embeddings: list = []
		with torch.inference_mode():
			with torch.amp.autocast(device_type="cuda"):
				slide_emb = self.slide_encoder(embeddings.unsqueeze(0), coordinates_tensor.unsqueeze(0), context_enriched_patch_tokens = True)  # Shape [1, 1536]
				slide_embeddings.append(slide_emb[0].squeeze(0).cpu().numpy())

		slide_embeddings = np.concatenate(slide_embeddings, axis=0)

		return slide_embeddings


class MicroscopyImage():
	def __init__(self, source_path: str, sample_id: str, modality_name: str, patch_extractor: PatchEmbeddingExtractor) -> None:
		'''
		Process a microscopy image to uniform the format, enhance the colors prepare it for registration.
		'''

		# Check if the input path exists and we can read
		if not os.path.exists(source_path):
			raise ValueError(f"The path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise ValueError(f"The path {source_path} is not readable.")

		if isinstance(patch_extractor, PatchEmbeddingExtractor) == False:
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
	
	def _save_image_pyramid(self, img: np.ndarray, output_file: str, levels: int = 4):
		"""
		Saves an RGB image as a fully compliant OME-TIFF containing multiple 
		independent images, one for each resolution level.

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

		# 1. Generate the four downscaled RGB images
		pyramid_data = []
		for i in range(levels):
			scale = 0.5 ** i
			h_scaled = max(1, int(H_base * scale))
			w_scaled = max(1, int(W_base * scale))
			resized = cv2.resize(img, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
			pyramid_data.append(resized)

		# 2. Build the OME-XML with four separate and fully compliant <Image> blocks
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
					size_c=3,
					size_t=1,
					interleaved=False,
					channels=[
						# --- FIX: Explicitly set samples_per_pixel=1 ---
						Channel(id=f"Channel:{i}:0", name="R", color=Color("red"), samples_per_pixel=1),
						Channel(id=f"Channel:{i}:1", name="G", color=Color("green"), samples_per_pixel=1),
						Channel(id=f"Channel:{i}:2", name="B", color=Color("blue"), samples_per_pixel=1),
					],
					planes=[Plane(the_c=c, the_z=0, the_t=0) for c in range(3)],
					tiff_data_blocks=[TiffData(ifd=ifd_counter, plane_count=3)],
				)
			)
			ome.images.append(image_block)
			ifd_counter += 3

		xml_metadata = ome.to_xml()

		# 3. Write all image planes sequentially to a single TIFF file
		with tifffile.TiffWriter(output_file, bigtiff=True) as tif:
			for c, level_img in enumerate(pyramid_data):
				plane_data = level_img
				description = xml_metadata if c == 0 else None
				tif.write(
					plane_data,
					description=description,
					photometric='rgb',
					metadata={'axes': 'YXC'},
					compression="zlib"
				)
					
		return output_file

	def _remove_background(self, image: np.ndarray, background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE, min_tissue_area: int = 0) -> np.ndarray:
		"""
		Remove background from an H&E image, preserving all tissue areas
		and cropping the output to the tissue bounding box.
		
		Args:
			image (np.ndarray): Input image [H, W, C], float32, 0-1.
			background_color (tuple): Color to use for background replacement (length 3, float between 0–1).
			min_tissue_area (int): Minimum size (in px) for tissue objects to keep (removes dust/small artefacts).
			
		Returns:
			Cropped np.ndarray with background replaced by background_color.
		"""
		assert image.ndim == 3 and image.shape[2] == 3

		if background_color == SegmentationBackgroundColor.WHITE:
			background_color = (1.0, 1.0, 1.0)
		elif background_color == SegmentationBackgroundColor.BLACK:
			background_color = (0.0, 0.0, 0.0)
		else:
			raise ValueError(f"Unsupported background color: {background_color}")

		# 1. Convert to grayscale or use LAB
		lab = color.rgb2lab(image)
		# Tissue (non-glass) is typically darker in L and a/b spread
		# Simple norm of a* and b* channels captures colorfulness (tissue regions)
		ab_norm = np.linalg.norm(lab[...,1:], axis=-1)
		normed = (ab_norm - ab_norm.min()) / (ab_norm.max() - ab_norm.min())
		
		# 2. Threshold to get binary tissue mask
		threshold = filters.threshold_otsu(normed)
		mask = normed > threshold  # True where tissue, False where background
		
		# 3. Morphological closing (fills holes, connects fragments)
		mask = morphology.remove_small_objects(mask, min_size=min_tissue_area)
		mask = morphology.binary_closing(mask, morphology.disk(7))
		mask = ndi.binary_fill_holes(mask)
		mask = mask.astype(bool)
		
		# 4. Apply the mask
		output_image = image.copy()
		for c in range(3):
			output_image[..., c][~mask] = background_color[c]
		
		return output_image
	
	def process_image(self, 
		color_enhancement: bool = True,
		remove_background: bool = True,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		patch_size: int = 224,
		pyramid_levels: int = 3,
		min_tissue_area: int = 5000
		) -> None:
		'''
		Preprocess a microscopy image by removing the background and enhancing the colors.
		The result is saved as a multi-resolution OME-TIFF file.

		Parameters
		----------
		remove_background : bool
			Whether to remove the background using Meta SAM2 (default is True).
		color_enhancement : bool
			Whether to enhance the colors using gamma correction and contrast enhancement (default is True).
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.
		patch_size : int
			The size of the patches to use for background removal (default is 224).
		pyramid_levels : int
			The number of pyramid levels to save in the output OME-TIFF (default is 3).
		'''

		# Check the inputs
		if type(color_enhancement) is not bool:
			raise ValueError(f"Invalid value for color_enhancement: {color_enhancement}. Expected a boolean.")
		if background_color not in SegmentationBackgroundColor.list():
			raise ValueError(f"Invalid value for background_color: {background_color}. Expected one of {SegmentationBackgroundColor.list()}.")
		
		if type(patch_size) is not int or patch_size <= 0:
			raise ValueError(f"Invalid value for patch_size: {patch_size}. Expected a positive integer.")
		if type(pyramid_levels) is not int or pyramid_levels <= 0:
			raise ValueError(f"Invalid value for pyramid_levels: {pyramid_levels}. Expected a positive integer.")
		
		# Load the input file
		image = self._load_tiff(self.filename)

		# Enhance colors if needed
		if color_enhancement:
			image = utils.gamma_correction(image)
			image = utils.enhance_contrast(image)

		# Force the image to be float32
		image = image.astype(np.float32)

		# Remove background if needed
		if remove_background:
			image = self._remove_background(image, background_color=background_color, min_tissue_area=min_tissue_area)

		# Extract patch embeddings
		patches, coordinates = self.patch_extractor.extract_patches(image, patch_size)
		patches, coordinates = self.patch_extractor.filter_empty_patches(patches, coordinates, background_color)
		embeddings = self.patch_extractor.extract_patch_embeddings(patches, coordinates)

		# Save the processed image as a multi-resolution OME-TIFF
		self._save_image_pyramid(image, os.path.join(self.output_folder, f"{self.sample_id}_{self.modality_name}_processed.ome.tiff"), levels=pyramid_levels)

		# Save the AnnData file with patch embeddings
		adata = anndata.AnnData(embeddings)
		adata.obsm['spatial'] = coordinates
		adata.write_h5ad(os.path.join(self.output_folder, f"{self.sample_id}_{self.modality_name}.h5ad"))
	