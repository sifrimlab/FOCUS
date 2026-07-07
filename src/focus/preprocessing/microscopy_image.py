import os
import numpy as np
import cv2
import tifffile
import czifile
import skimage.morphology as morphology
from scipy.ndimage import binary_fill_holes
from ome_types.model import OME, Image, Pixels, Channel, TiffData, Plane

import focus.utils as utils
from focus.preprocessing._utils import StepReporter
from focus.constants import SegmentationBackgroundColor, MicroscopyImageProcessingParams
from focus.constants import MODALITY_PREPROCESSING
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality

# Supported input file extensions, ordered by priority
_SUPPORTED_EXTENSIONS = (".ome.tiff", ".ome.tif", ".qptiff", ".tiff", ".tif", ".czi")


class MicroscopyImage(BaseSample):
	"""
	Process a microscopy image to uniform the format, enhance colors,
	and prepare it for alignment/registration.

	Input: TIFF (.tiff, .tif, .ome.tiff, .ome.tif), qpTIFF (.qptiff), or CZI (.czi)
	Output: multi-resolution OME-TIFF, stored in the source file's dtype
	(uint8/uint16 passthrough, float sources stay float32), zlib-compressed with predictor.
	"""

	# Default processing parameters (all configurable via process_image)
	_CROP_MARGIN_PX = 250
	_GAUSSIAN_BLUR_KERNEL_SIZE = 251
	_MIN_OBJECT_SIZE = 500
	_CLIP_PERCENTILE = 99
	_GAMMA = 0.45
	_CONTRAST_SATURATION = 0.35
	_MAX_PYRAMID_PIXELS = 3000 * 3000  # pixel cap for the smallest pyramid level (GUI rendering)
	_DETECTION_MAX_PIXELS = 3000 * 3000  # pixel cap for the tissue-detection proxy (independent of the pyramid cap)

	def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
		super().__init__(source_path, sample_id, modality_name)

		input_dir = os.path.join(source_path, sample_id, modality_name)
		self.filename = self._find_image_file(input_dir)
		if self.filename is None:
			raise FileNotFoundError(
				f"No supported image file ({', '.join(_SUPPORTED_EXTENSIONS)}) found in {input_dir}"
			)

	@staticmethod
	def _find_image_file(directory: str) -> str | None:
		"""Find the first supported image file in a directory, by extension priority."""
		files = os.listdir(directory)
		for ext in _SUPPORTED_EXTENSIONS:
			for f in files:
				if f.lower().endswith(ext):
					return os.path.join(directory, f)
		return None

	def _load_image(self, file: str) -> tuple[np.ndarray, np.dtype]:
		"""
		Load an image from a supported file format.
		Returns a float32 array normalized to [0, 1] with shape (H, W, C),
		plus the original on-disk dtype (used to pick the output storage dtype).

		Dispatches to the appropriate loader based on file extension.
		"""
		lower = file.lower()
		if lower.endswith(".czi"):
			return self._load_czi(file)
		elif lower.endswith(".qptiff"):
			return self._load_qptiff(file)
		else:
			return self._load_tiff(file)

	def _load_tiff(self, file: str) -> tuple[np.ndarray, np.dtype]:
		"""
		Read a TIFF/OME-TIFF file and return as float32 [0,1] with channels last (H, W, C),
		plus the original on-disk dtype.
		"""
		with tifffile.TiffFile(file) as f:
			image = f.asarray()

		input_dtype = image.dtype
		image = self._normalize_image(image)
		return image, input_dtype

	def _load_czi(self, file: str) -> tuple[np.ndarray, np.dtype]:
		"""
		Read a CZI file and return as float32 [0,1] with channels last (H, W, C),
		plus the original on-disk dtype.
		"""
		with czifile.CziFile(file) as czi:
			image = czi.asarray()

		input_dtype = image.dtype

		# Squeeze extra dimensions (CZI can have 5+ dims)
		if image.ndim > 3:
			if image.shape[0] > 1:
				print("WARNING: CZI file has multiple scenes. Using only the first one.")
			while image.ndim > 3:
				image = image[0]

		image = self._normalize_image(image)
		return image, input_dtype

	def _load_qptiff(self, file: str) -> tuple[np.ndarray, np.dtype]:
		"""
		Read a qpTIFF file and return as float32 [0,1] with channels last (H, W, C),
		plus the original on-disk dtype.

		qpTIFF files (e.g. Akoya Vectra/PhenoImager) embed the full-resolution image
		alongside a downsampled pyramid and small auxiliary images (thumbnail, macro,
		label). Since FOCUS recomputes its own pyramid on output, select the single
		series/level with the most pixels rather than trusting series order.
		"""
		with tifffile.TiffFile(file) as f:
			levels = [level for series in f.series for level in series.levels]
			sizes = [utils.hw_from_axes(level.shape, getattr(level, "axes", "")) for level in levels]
			best_index = max(range(len(levels)), key=lambda i: sizes[i][0] * sizes[i][1])
			if len(levels) > 1:
				h, w = sizes[best_index]
				print(f"INFO: qpTIFF file has {len(levels)} candidate resolutions across series; using the highest-resolution one ({h}x{w}).")
			image = levels[best_index].asarray()

		input_dtype = image.dtype
		image = self._normalize_image(image)
		return image, input_dtype

	@staticmethod
	def _normalize_image(image: np.ndarray) -> np.ndarray:
		"""
		Common post-load normalization:
		1. Move channel axis to last if needed
		2. Convert to float32 [0, 1]
		3. Ensure (H, W, C) shape, clipping to 3 channels max
		"""
		# Ensure 3D: (H, W) → (H, W, 1)
		if image.ndim == 2:
			image = image[:, :, np.newaxis]

		# Move smallest dimension to last (channel axis heuristic)
		if image.ndim == 3 and image.shape[2] > min(image.shape[0], image.shape[1]):
			channel_index = np.argmin(image.shape)
			if channel_index == 0:
				image = image.transpose(1, 2, 0)
			elif channel_index == 1:
				image = image.transpose(0, 2, 1)

		# Convert to float32 and normalize to [0, 1]
		if image.dtype == np.float32 and image.max() <= 1.0:
			pass  # Already normalized float32
		else:
			max_val = np.iinfo(image.dtype).max if np.issubdtype(image.dtype, np.integer) else image.max()
			image = image.astype(np.float32)
			if max_val > 0:
				image /= float(max_val)

		# Clip to max 3 channels
		if image.shape[2] > 3:
			image = image[:, :, :3]

		return image

	@staticmethod
	def _compute_pyramid_levels(H: int, W: int) -> int:
		"""Compute how many pyramid levels are needed so the smallest level fits within _MAX_PYRAMID_PIXELS."""
		import math
		total = H * W
		if total <= MicroscopyImage._MAX_PYRAMID_PIXELS:
			return 1
		return math.ceil(math.log(total / MicroscopyImage._MAX_PYRAMID_PIXELS, 4)) + 1

	# Maps the resolved on-disk storage dtype to the OME-XML Pixels "type" attribute.
	# ome_types expects the OME PixelType spelling ("float", not "float32").
	_OME_PIXEL_TYPE = {
		np.dtype(np.uint8): "uint8",
		np.dtype(np.uint16): "uint16",
		np.dtype(np.float32): "float",
	}

	@staticmethod
	def _resolve_storage_dtype(input_dtype: np.dtype) -> np.dtype:
		"""Map the source image's dtype to the dtype used to store the output OME-TIFF."""
		input_dtype = np.dtype(input_dtype)
		if np.issubdtype(input_dtype, np.floating):
			return np.dtype(np.float32)
		if input_dtype in (np.dtype(np.uint8), np.dtype(np.uint16)):
			return input_dtype
		# Uncommon integer depths (int16, uint32, ...): fall back to a safe high-precision default.
		return np.dtype(np.uint16)

	@staticmethod
	def _quantize_to_dtype(img_float01: np.ndarray, dtype: np.dtype) -> np.ndarray:
		"""Convert a float32 [0, 1] array to the given storage dtype."""
		if dtype == np.dtype(np.float32):
			return img_float01.astype(np.float32)
		max_val = np.iinfo(dtype).max
		return np.clip(np.round(img_float01 * max_val), 0, max_val).astype(dtype)

	def _save_image_pyramid(self, img: np.ndarray, output_file: str, input_dtype: np.dtype) -> str:
		"""
		Saves an RGB/multi-channel image as a multi-resolution OME-TIFF containing
		multiple independent images, one for each resolution level.
		Uses separate top-level IFDs (not SubIFDs) and zlib compression,
		following the reference implementation for maximum compatibility.

		The number of levels is computed automatically so the smallest level
		does not exceed _MAX_PYRAMID_PIXELS total pixels.

		Pixel data is generated and resampled in float32 for quality, then quantized
		to the resolved storage dtype (matching the source file's bit depth) only at
		write time, with the TIFF predictor matched to that dtype.

		Parameters
		----------
		img : np.ndarray
			The input image as a NumPy array of shape (H, W, C) or (H, W) and dtype float32.
		output_file : str
			The path to the output OME-TIFF file.
		input_dtype : np.dtype
			The dtype of the original source image, used to pick the output storage dtype.
		"""
		assert img.dtype == np.float32, "Expecting float32 array"

		storage_dtype = self._resolve_storage_dtype(input_dtype)
		predictor = 3 if storage_dtype == np.dtype(np.float32) else 2
		ome_type = self._OME_PIXEL_TYPE[storage_dtype]

		# Normalize to [H,W,C] shape
		if img.ndim == 2:
			img = img[..., np.newaxis]  # Grayscale -> [H,W,1]
		H_base, W_base, C = img.shape
		levels = self._compute_pyramid_levels(H_base, W_base)
		is_rgb = (C == 3)

		# 1. Generate pyramid (resize preserves shape type)
		pyramid_data = []
		for i in range(levels):
			scale = 0.5 ** i
			h_scaled = max(1, int(H_base * scale))
			w_scaled = max(1, int(W_base * scale))
			resized = cv2.resize(img, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
			# Re-add channel axis if cv2.resize squeezed it for 1-channel images
			if resized.ndim == 2:
				resized = resized[..., np.newaxis]
			pyramid_data.append(resized)

		# 2. Build OME-XML
		ome = OME()
		ifd_counter = 0
		for i, level_img in enumerate(pyramid_data):
			H, W, CC = level_img.shape

			if is_rgb:
				# RGB interleaved
				image_block = Image(
					id=f"Image:{i}", name=f"ResolutionLevel_{i}",
					pixels=Pixels(
						id=f"Pixels:{i}", dimension_order="XYCZT", type=ome_type,
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
						id=f"Pixels:{i}", dimension_order="XYCZT", type=ome_type,
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
					data = self._quantize_to_dtype(level_img, storage_dtype)
					tif.write(
						data,
						description=description,
						photometric='rgb',
						metadata={'axes': 'YXC'},
						compression="zlib",
						predictor=predictor
					)
				else:
					# Write each channel slice separately as minisblack
					for ch_idx in range(C):
						ch_img = level_img[:, :, ch_idx]
						data = self._quantize_to_dtype(ch_img, storage_dtype)
						description = xml_metadata if c == 0 and ch_idx == 0 else None
						tif.write(
							data,
							description=description,
							photometric='minisblack',
							metadata={'axes': 'YX'},
							compression="zlib",
							predictor=predictor
						)

		return output_file

	def _detect_tissue_mask(self, image: np.ndarray,
		min_object_coverage: float = 0.01,
		blur_kernel_size: int = 251,
		min_object_size: int = 500,
		clip_percentile: int = 99
	) -> np.ndarray:
		"""
		Segment tissue vs. background (Otsu-based) on `image` at its current resolution,
		preserving tissue areas larger than image_area * min_object_coverage.

		Parameters
		----------
		image : np.ndarray
			Input RGB float32 image of shape (H, W, 3) in [0, 1].
		min_object_coverage : float
			Minimum tissue area relative to image area.
		blur_kernel_size : int
			Gaussian blur kernel size (must be odd).
		min_object_size : int
			Minimum connected component size in pixels to keep.
		clip_percentile : int
			Percentile for intensity clipping before thresholding.

		Returns
		-------
		np.ndarray
			Boolean tissue mask, same (H, W) as `image`.
		"""
		# Ensure blur kernel is odd
		if blur_kernel_size % 2 == 0:
			blur_kernel_size += 1

		# Convert to uint8 for mask computation (avoids float intermediaries)
		image_uint8 = (image * 255).astype(np.uint8)

		# Replace black pixels with white to avoid thresholding artifacts
		black_pixels = np.all(image_uint8 == 0, axis=-1)
		image_uint8[black_pixels] = 255

		# Grayscale → invert (white bg becomes black)
		gray = cv2.cvtColor(image_uint8, cv2.COLOR_RGB2GRAY)
		gray = cv2.bitwise_not(gray)
		del image_uint8  # Free the uint8 RGB copy

		# Clip at percentile to reduce oversaturation impact
		clip_value = np.percentile(gray, clip_percentile)
		blurred = cv2.GaussianBlur(
			np.clip(gray, None, clip_value).astype(np.uint8),
			(blur_kernel_size, blur_kernel_size), 0
		)

		# Otsu threshold on blurred, apply to original grayscale
		otsu_thresh, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
		del blurred
		_, thresh = cv2.threshold(gray, int(otsu_thresh), 255, cv2.THRESH_BINARY)
		del gray

		# Morphological cleanup
		mask_clean = morphology.remove_small_objects(thresh.astype(bool), min_size=min_object_size)
		del thresh
		segmentation_mask = binary_fill_holes(mask_clean)
		del mask_clean

		# Refine with contour-based area filtering
		seg_uint8 = segmentation_mask.astype(np.uint8) * 255
		contours, _ = cv2.findContours(seg_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

		if contours:
			image_area = seg_uint8.shape[0] * seg_uint8.shape[1]
			area_threshold = min_object_coverage * image_area
			large_contours = [c for c in contours if cv2.contourArea(c) >= area_threshold]

			tissue_mask = np.zeros_like(seg_uint8)
			cv2.drawContours(tissue_mask, large_contours, contourIdx=-1, color=255, thickness=cv2.FILLED)
			segmentation_mask = tissue_mask.astype(bool)
		else:
			print("Warning: No contours found; cannot refine background mask.")
		del seg_uint8

		return segmentation_mask

	def _compute_tissue_mask(self, image: np.ndarray,
		min_object_coverage: float = 0.01,
		blur_kernel_size: int = 251,
		min_object_size: int = 500,
		clip_percentile: int = 99
	) -> tuple[np.ndarray, float]:
		"""
		Detect the tissue mask on a downsampled proxy for speed on very large images.

		Segmentation (Gaussian blur, Otsu threshold, morphological cleanup) is run on a
		proxy capped at _DETECTION_MAX_PIXELS rather than the full-resolution image, since
		a tissue/background boundary is a smooth, low-frequency shape that doesn't need
		full-resolution input to locate accurately.

		Returns
		-------
		tuple of (mask, scale)
			mask : boolean tissue mask at `scale` resolution relative to `image`.
			scale : proxy_size / full_size, in (0, 1]. 1.0 means no downsampling occurred.
		"""
		import math
		H, W = image.shape[:2]
		scale = min(1.0, math.sqrt(self._DETECTION_MAX_PIXELS / (H * W)))
		if scale < 1.0:
			proxy = cv2.resize(image, (max(1, round(W * scale)), max(1, round(H * scale))), interpolation=cv2.INTER_AREA)
			if proxy.ndim == 2:
				proxy = proxy[..., np.newaxis]
			k = max(3, int(round(blur_kernel_size * scale)) | 1)  # odd, matches physical blur extent
			m = max(1, int(round(min_object_size * scale * scale)))  # area scales with scale**2
		else:
			proxy, k, m = image, blur_kernel_size, min_object_size
		mask = self._detect_tissue_mask(proxy, min_object_coverage, k, m, clip_percentile)
		return mask, scale

	def _remove_background(self, image: np.ndarray, mask: np.ndarray, scale: float,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE
	) -> np.ndarray:
		"""
		Apply a tissue mask to a full-resolution image, filling non-tissue pixels with
		the background color.

		Parameters
		----------
		image : np.ndarray
			Input RGB float32 image of shape (H, W, 3) in [0, 1].
		mask : np.ndarray
			Boolean tissue mask at `scale` resolution relative to `image` (see `_compute_tissue_mask`).
		scale : float
			proxy_size / full_size for `mask`, in (0, 1]. 1.0 means `mask` already matches `image`.
		background_color : SegmentationBackgroundColor
			Color to fill the background with.

		Returns
		-------
		np.ndarray
			Image with background replaced.
		"""
		if background_color == SegmentationBackgroundColor.WHITE:
			bg_fill = np.float32([1.0, 1.0, 1.0])
		elif background_color == SegmentationBackgroundColor.BLACK:
			bg_fill = np.float32([0.0, 0.0, 0.0])
		else:
			raise ValueError(f"Unsupported background color: {background_color}")

		if scale < 1.0:
			H, W = image.shape[:2]
			mask = cv2.resize(mask.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)

		# Apply mask: keep tissue, fill background
		output = np.empty_like(image)
		output[mask] = image[mask]
		output[~mask] = bg_fill

		return output

	def _crop_image(self, image: np.ndarray, mask: np.ndarray, scale: float,
		margin: int = 250
	) -> np.ndarray:
		"""
		Crop the image to the bounding box of the tissue mask plus a margin.

		Parameters
		----------
		image : np.ndarray
			Input RGB float32 image of shape (H, W, 3).
		mask : np.ndarray
			Boolean tissue mask at `scale` resolution relative to `image` (see `_compute_tissue_mask`).
		scale : float
			proxy_size / full_size for `mask`, in (0, 1]. 1.0 means `mask` already matches `image`.
		margin : int
			Pixel margin to add around the bounding box (in full-resolution pixels).

		Returns
		-------
		np.ndarray
			Cropped image.
		"""
		rows = np.any(mask, axis=1)
		cols = np.any(mask, axis=0)
		if not np.any(rows) or not np.any(cols):
			raise ValueError("The image appears to be entirely background; cannot crop.")

		ymin, ymax = np.where(rows)[0][[0, -1]]
		xmin, xmax = np.where(cols)[0][[0, -1]]

		if scale < 1.0:
			ymin, ymax, xmin, xmax = (int(round(v / scale)) for v in (ymin, ymax, xmin, xmax))

		ymin = max(0, ymin - margin)
		ymax = min(image.shape[0] - 1, ymax + margin)
		xmin = max(0, xmin - margin)
		xmax = min(image.shape[1] - 1, xmax + margin)

		return image[ymin:ymax + 1, xmin:xmax + 1, :]

	def preview_image(self) -> np.ndarray:
		"""Load and return a preview of the microscopy image as float32 (H, W, C)."""
		image, _ = self._load_image(self.filename)
		return image

	def process_image(self,
		color_enhancement: bool = True,
		remove_background: bool = True,
		crop_to_tissue: bool = True,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		min_object_coverage: float = 0.01,
		force_recomputing: bool = False,
		gaussian_blur_kernel_size: int = _GAUSSIAN_BLUR_KERNEL_SIZE,
		min_object_size: int = _MIN_OBJECT_SIZE,
		clip_percentile: int = _CLIP_PERCENTILE,
		crop_margin: int = _CROP_MARGIN_PX,
		gamma: float = _GAMMA,
		contrast_saturation: float = _CONTRAST_SATURATION
	) -> str:
		"""
		Preprocess a microscopy image: load, enhance, remove background, crop, save as OME-TIFF.

		Parameters
		----------
		color_enhancement : bool
			Whether to enhance colors using gamma correction and contrast stretching.
		remove_background : bool
			Whether to remove the background using Otsu thresholding.
		crop_to_tissue : bool
			Whether to crop the image to the tissue bounding box.
		background_color : SegmentationBackgroundColor
			Color to fill the background with after removal.
		min_object_coverage : float
			Minimum tissue area fraction to keep during background removal (0-1).
		force_recomputing : bool
			Whether to force recomputation even if the output exists.
		gaussian_blur_kernel_size : int
			Gaussian blur kernel size for background detection (must be odd).
		min_object_size : int
			Minimum connected component size in pixels to keep.
		clip_percentile : int
			Percentile for intensity clipping before thresholding.
		crop_margin : int
			Pixel margin around the tissue bounding box when cropping.
		gamma : float
			Gamma value for gamma correction (< 1 brightens, > 1 darkens).
		contrast_saturation : float
			Percentage of pixels to saturate during contrast stretching.

		Returns
		-------
		str
			Path to the output OME-TIFF file.
		"""

		output_ome_tiff = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, 'ome.tiff')

		reporter = getattr(self, '_step_reporter', None) or StepReporter()

		if not force_recomputing and os.path.exists(output_ome_tiff):
			print(f"Processed image already exists. Using cached results.")
			return output_ome_tiff

		# 1. Load
		reporter.step(f"1/5 - Loading image from {self.filename}")
		image, input_dtype = self._load_image(self.filename)

		# 2. Color enhancement
		if color_enhancement:
			reporter.step(f"2/5 - Enhancing colors (gamma={gamma}, saturation={contrast_saturation})")
			image = utils.gamma_correction(image, gamma=gamma)
			image = utils.enhance_contrast(image, saturated_pixels=contrast_saturation, max_stat_pixels=self._DETECTION_MAX_PIXELS)
		else:
			reporter.step(f"2/5 - Color enhancement not required")

		# Ensure float32 after enhancement
		if image.dtype != np.float32:
			image = image.astype(np.float32)

		# 3. Background removal + 4. Crop share one low-resolution tissue-detection pass
		if remove_background or crop_to_tissue:
			reporter.step(f"3/5 - Detecting tissue (downsampled proxy)")
			mask, scale = self._compute_tissue_mask(
				image,
				min_object_coverage=min_object_coverage,
				blur_kernel_size=gaussian_blur_kernel_size,
				min_object_size=min_object_size,
				clip_percentile=clip_percentile
			)
			if remove_background:
				reporter.step(f"3/5 - Removing background")
				image = self._remove_background(image, mask, scale, background_color=background_color)
			else:
				reporter.step(f"3/5 - Background removal not required")

			if crop_to_tissue:
				reporter.step(f"4/5 - Cropping to tissue area (margin={crop_margin}px)")
				image = self._crop_image(image, mask, scale, margin=crop_margin)
			else:
				reporter.step(f"4/5 - Cropping not required")
		else:
			reporter.step(f"3/5 - Background removal not required")
			reporter.step(f"4/5 - Cropping not required")

		# 5. Save
		n_levels = self._compute_pyramid_levels(*image.shape[:2])
		reporter.step(f"5/5 - Saving OME-TIFF ({n_levels} pyramid levels, cap={self._MAX_PYRAMID_PIXELS} px)")
		self._save_image_pyramid(image, output_ome_tiff, input_dtype)
		return output_ome_tiff


class MicroscopyImageDataset(BaseDataset):
	"""Handle a collection of MicroscopyImage samples."""

	def __init__(self, path: str, samples: list[MicroscopyImage]):
		super().__init__(path, samples)
		for sample in samples:
			if not isinstance(sample, MicroscopyImage):
				raise ValueError(f"Invalid sample: {sample}. Expected MicroscopyImage.")

	def process_dataset(self,
		color_enhancement: bool = True,
		remove_background: bool = True,
		crop_to_tissue: bool = True,
		background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
		min_object_coverage: float = 0.01,
		force_recomputing: bool = False,
		gaussian_blur_kernel_size: int = MicroscopyImage._GAUSSIAN_BLUR_KERNEL_SIZE,
		min_object_size: int = MicroscopyImage._MIN_OBJECT_SIZE,
		clip_percentile: int = MicroscopyImage._CLIP_PERCENTILE,
		crop_margin: int = MicroscopyImage._CROP_MARGIN_PX,
		gamma: float = MicroscopyImage._GAMMA,
		contrast_saturation: float = MicroscopyImage._CONTRAST_SATURATION,
		step_reporter=None
	) -> dict[str, str]:
		"""
		Preprocess all microscopy images in the dataset.
		All parameters are forwarded to each sample's process_image method.

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
			sample._step_reporter = reporter
			try:
				output_file = sample.process_image(
					color_enhancement=color_enhancement,
					remove_background=remove_background,
					crop_to_tissue=crop_to_tissue,
					background_color=background_color,
					min_object_coverage=min_object_coverage,
					force_recomputing=force_recomputing,
					gaussian_blur_kernel_size=gaussian_blur_kernel_size,
					min_object_size=min_object_size,
					clip_percentile=clip_percentile,
					crop_margin=crop_margin,
					gamma=gamma,
					contrast_saturation=contrast_saturation
				)
				processed_samples[sample.sample_id] = output_file
			except Exception as e:
				print(f"Error processing sample {sample.sample_id}: {e}")
		return processed_samples


# --- Modality Registration ---

def _create_microscopy_samples(path, sample_ids, modality_name, settings):
	return [
		MicroscopyImage(source_path=path, sample_id=sid, modality_name=modality_name)
		for sid in sample_ids
	]

def _create_microscopy_dataset(path, samples, settings):
	return MicroscopyImageDataset(path=path, samples=samples)

def _extract_microscopy_settings(settings):
	return {
		'color_enhancement': settings.get(MicroscopyImageProcessingParams.COLOR_ENHANCEMENT, True),
		'remove_background': settings.get(MicroscopyImageProcessingParams.REMOVE_BACKGROUND, True),
		'crop_to_tissue': settings.get(MicroscopyImageProcessingParams.CROP_TO_TISSUE, True),
		'background_color': settings.get(MicroscopyImageProcessingParams.BACKGROUND_COLOR, SegmentationBackgroundColor.WHITE),
		'min_object_coverage': settings.get(MicroscopyImageProcessingParams.MIN_OBJECT_COVERAGE, 0.01),
		'force_recomputing': settings.get(MicroscopyImageProcessingParams.FORCE_RECOMPUTING, False),
		'gaussian_blur_kernel_size': settings.get(MicroscopyImageProcessingParams.GAUSSIAN_BLUR_KERNEL_SIZE, MicroscopyImage._GAUSSIAN_BLUR_KERNEL_SIZE),
		'min_object_size': settings.get(MicroscopyImageProcessingParams.MIN_OBJECT_SIZE, MicroscopyImage._MIN_OBJECT_SIZE),
		'clip_percentile': settings.get(MicroscopyImageProcessingParams.CLIP_PERCENTILE, MicroscopyImage._CLIP_PERCENTILE),
		'crop_margin': settings.get(MicroscopyImageProcessingParams.CROP_MARGIN, MicroscopyImage._CROP_MARGIN_PX),
		'gamma': settings.get(MicroscopyImageProcessingParams.GAMMA, MicroscopyImage._GAMMA),
		'contrast_saturation': settings.get(MicroscopyImageProcessingParams.CONTRAST_SATURATION, MicroscopyImage._CONTRAST_SATURATION),
	}

register_modality('microscopy_image', ModalityHandler(
	create_samples=_create_microscopy_samples,
	create_dataset=_create_microscopy_dataset,
	extract_settings=_extract_microscopy_settings,
))
