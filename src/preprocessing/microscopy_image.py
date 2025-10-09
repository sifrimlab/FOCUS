import copy, os, tifffile, cv2, subprocess
import numpy as np
import matplotlib.pyplot as plt
import skimage.io as skio
import skimage.exposure
from ome_types.model import OME, Image, Pixels, Channel, TiffData, Plane, Color

import utils as utils
from constants import ContainerEngine, SegmentationBackgroundColor

class MicroscopyImage():
	def __init__(self, source_path: str, sample_id: str, modality_name: str, container_engine: ContainerEngine) -> None:
		'''
		Process a microscopy image to uniform the format, enhance the colors prepare it for registration.
		'''

		# Check if the input path exists and we can read
		if not os.path.exists(source_path):
			raise ValueError(f"The path {source_path} does not exist.")
		if not os.access(source_path, os.R_OK):
			raise ValueError(f"The path {source_path} is not readable.")
		
		# Check that the container engine is supported
		if container_engine not in ContainerEngine.list():
			raise ValueError(f"Unsupported container engine: {container_engine}. Supported engines are: {ContainerEngine.list()}")

		self.source_path = source_path
		self.sample_id = sample_id
		self.modality_name = modality_name
		self.container_engine = container_engine

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

		return image
	
	def _remove_background(self, input_image: np.ndarray, background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE) -> np.ndarray:
		'''
		Remove the background from the image using Meta SAM2.

		Parameters
		----------
		input_image : np.ndarray
			The input RGB image from which to remove the background.
		background_color : SegmentationBackgroundColor
			The color used to fill the background after removal. This is usefull to match the requirements of futher processing steps.

		Returns
		----------
		image : np.ndarray
			The image with the background removed.
		'''

		# Save a grayscale version of the image to use for segmentation
		grayscale_image = skimage.color.rgb2gray(input_image)
		grayscale_image = (grayscale_image * 255).astype(np.uint8)
		np.save(os.path.join(self.output_folder, "grayscale_mosaic.npy"), grayscale_image)

		# Get the tools absolute path
		tools_basedir = os.path.abspath(__file__).replace("src/preprocessing/microscopy_image.py", "tools")

		# Check if the SAM2 container is available
		if self.container_engine == ContainerEngine.DOCKER:
			subprocess.run(["docker", "build", "-f", os.path.join(tools_basedir, "SAM2", "Dockerfile"), "-t", "sam2:latest", os.path.join(tools_basedir, "SAM2")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		elif self.container_engine == ContainerEngine.PODMAN:
			subprocess.run(["podman", "build", "-f", os.path.join(tools_basedir, "SAM2", "Dockerfile"), "-t", "sam2:latest", os.path.join(tools_basedir, "SAM2")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		elif self.container_engine == ContainerEngine.SINGULARITY:
			if not os.path.exists(os.path.join(tools_basedir, "SAM2", "sam2.sif")):
				raise FileNotFoundError(f"SAM2 Singularity image not found at {os.path.join(tools_basedir, 'SAM2', 'sam2.sif')}")
			
		# Execute SAM2 to remove the background
		if self.container_engine == ContainerEngine.DOCKER:
			subprocess.run([
				"docker", "run", "--rm", "-v",
				f"{self.output_folder}:/data/",
				"sam2:latest"
			])
		elif self.container_engine == ContainerEngine.PODMAN:
			subprocess.run([
				"podman", "run", "--rm", "-v",
				f"{self.output_folder}:/data/",
				"sam2:latest"
			])
		elif self.container_engine == ContainerEngine.SINGULARITY:
			subprocess.run([
				"singularity", "exec", "--bind",
				f"{self.output_folder}:/data/",
				os.path.join(tools_basedir, "SAM2", "sam2.sif"), "sam2"
			])
		else:
			raise RuntimeError(f"Unsupported container engine: {self.container_engine}. Supported engines are: {ContainerEngine.list()}")

		# Load the segmentation mask
		segmentation_mask = np.load(os.path.join(self.output_folder, "segmentation_mask.npy"))

		# Apply the segmentation mask to the input image
		segmented_image = np.zeros_like(input_image)

		if background_color == SegmentationBackgroundColor.WHITE:
			segmented_image = input_image * segmentation_mask[:, :, np.newaxis] + (1 - segmentation_mask[:, :, np.newaxis])
		else:
			segmented_image = input_image * segmentation_mask[:, :, np.newaxis]

		return segmented_image

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
					compression=('deflate', 9)
				)
					
		return output_file

	def process_image(self, remove_background: bool = True, color_enhancement: bool = True, background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE) -> None:
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
		'''

		# Check the inputs
		if type(remove_background) is not bool:
			raise ValueError(f"Invalid value for remove_background: {remove_background}. Expected a boolean.")
		if type(color_enhancement) is not bool:
			raise ValueError(f"Invalid value for color_enhancement: {color_enhancement}. Expected a boolean.")
		if background_color not in SegmentationBackgroundColor.list():
			raise ValueError(f"Invalid value for background_color: {background_color}. Expected one of {SegmentationBackgroundColor.list()}.")
		
		# Load the input file
		image = self._load_tiff(self.filename)

		# Enhance colors if needed
		if color_enhancement:
			image = utils.gamma_correction(image)
			image = utils.enhance_contrast(image)

		# Remove the background if needed
		if remove_background:
			image = self._remove_background(image, background_color)

		# Save the processed image as a multi-resolution OME-TIFF
		self._save_image_pyramid(image, os.path.join(self.output_folder, f"{self.sample_id}_{self.modality_name}_processed.ome.tiff"), levels=4)
	