import os, tifffile, threading, anndata
import numpy as np
from PIL import Image
from sklearn.decomposition import NMF

from focus.constants import MODALITY_ALIGNMENT, MODALITY_ALIGNMENT_MERGED
from focus.constants import ModalityType

from focus.GUI.direct_mapping_alignment import DirectMappingAlignmentGUI

# Perceptually distinct palette for Leiden cluster coloring (up to 26 clusters, then cycles)
_CLUSTER_PALETTE = [
	"#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
	"#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
	"#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
	"#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
	"#393b79", "#637939", "#8c6d31", "#843c39", "#7b4173",
	"#5254a3",
]

_H5AD_COMPRESSION = "gzip"

# Modality type groupings
_IMAGE_MODALITIES = [ModalityType.MICROSCOPY_IMAGE, ModalityType.RAMAN]
_SPOT_MODALITIES = [ModalityType.MSI, ModalityType.ST]


def _generate_cluster_colors(labels: np.ndarray) -> dict[str, str]:
	"""Generate a color map for cluster labels using a perceptually distinct palette."""
	unique_labels = sorted(set(str(l) for l in labels))
	return {label: _CLUSTER_PALETTE[i % len(_CLUSTER_PALETTE)] for i, label in enumerate(unique_labels)}


def _image_to_rgb_uint8(image_data: np.ndarray, lowest_shape: tuple, original_shape: tuple) -> tuple[np.ndarray, tuple, tuple]:
	"""
	Convert loaded image data to RGB uint8 for GUI display.

	Handles:
	- dtype conversion to uint8
	- channel axis detection and transposition to HWC
	- 1 channel (grayscale) → RGB by triplication
	- 2 channels → pad with zeros to 3
	- 3 channels → keep as-is
	- 4+ channels → NMF reduction to 3 components

	Returns (image_rgb_uint8, lowest_shape_hwc, original_shape_hwc).
	"""
	# Convert to uint8 if needed.
	# Always use min-max normalisation: never assume a specific input range such as
	# [0, 1] for float32, because tifffile may return SubIFD data in any numeric
	# range (uint8 0-255, uint16 0-65535, float32 0-1, etc.).
	if image_data.dtype != np.uint8:
		arr = image_data.astype(np.float32)
		dmin, dmax = float(arr.min()), float(arr.max())
		if dmax > dmin:
			image_data = ((arr - dmin) / (dmax - dmin) * 255.0).astype(np.uint8)
		else:
			image_data = np.zeros_like(arr, dtype=np.uint8)
	else:
		# If uint8 but very low range (e.g. [0, 1]), re-scale for GUI visibility
		dmin, dmax = int(image_data.min()), int(image_data.max())
		if 0 < dmax < 10:  # Arbitrary threshold for "very low dynamic range"
			arr = image_data.astype(np.float32)
			image_data = ((arr - dmin) / (dmax - dmin) * 255.0).astype(np.uint8)

	# Ensure HWC format
	if image_data.ndim == 2:
		# Grayscale → (H, W, 1)
		image_data = image_data[:, :, np.newaxis]
	elif image_data.ndim == 3 and np.argmin(image_data.shape) == 0:
		# Channels-first → HWC
		image_data = np.transpose(image_data, (1, 2, 0))
		lowest_shape = (lowest_shape[1], lowest_shape[2], lowest_shape[0])
		original_shape = (original_shape[1], original_shape[2], original_shape[0])

	n_channels = image_data.shape[-1]

	if n_channels == 1:
		# Grayscale → RGB
		image_data = np.repeat(image_data, 3, axis=-1)
	elif n_channels == 2:
		# 2-channel → pad with zeros to RGB
		pad = np.zeros((*image_data.shape[:2], 1), dtype=np.uint8)
		image_data = np.concatenate([image_data, pad], axis=-1)
	elif n_channels == 3:
		pass  # Already RGB
	else:
		# 4+ channels → NMF reduction to 3 components
		h, w = image_data.shape[:2]
		reshaped = image_data.reshape(-1, n_channels).astype(np.float32)
		nmf_model = NMF(n_components=3, init='nndsvda', random_state=42, max_iter=300)
		W = nmf_model.fit_transform(reshaped)
		wmax = W.max()
		if wmax > 0:
			W = (W / wmax * 255).astype(np.uint8)
		else:
			W = np.zeros_like(W, dtype=np.uint8)
		image_data = W.reshape(h, w, 3)

	return image_data, lowest_shape, original_shape


class DirectMappingAligner:
	"""
	Align two modalities using interactive coordinate mapping via a web GUI.

	Supports all modality combinations:
	- IMAGE ↔ IMAGE: crops reference to aligned bounding box (output: OME-TIFF)
	- IMAGE → SPOT: stores aligned coordinates in AnnData obsm (output: H5AD)
	- SPOT → SPOT: stores aligned coordinates in AnnData obsm (output: H5AD)

	For image-based modalities, the lowest pyramid level is displayed in the GUI,
	but aligned coordinates are scaled back to full resolution for the output.
	"""

	def __init__(self,
			path: str,
			reference_modality: dict,
			target_modality: dict,
			reference_modality_name: str,
			target_modality_name: str,
			reference_modality_type: str,
			target_modality_type: str
		) -> None:

		if not isinstance(path, str) or not isinstance(reference_modality, dict) or not isinstance(target_modality, dict):
			raise TypeError("Invalid input types.")
		if not isinstance(reference_modality_name, str) or not isinstance(target_modality_name, str):
			raise TypeError("Invalid input types.")
		if reference_modality_type not in ModalityType.list() or target_modality_type not in ModalityType.list():
			raise ValueError("Invalid modality type.")

		self._path = path
		self._reference_modality = reference_modality
		self._target_modality = target_modality
		self._reference_modality_name = reference_modality_name
		self._target_modality_name = target_modality_name
		self._reference_modality_type = reference_modality_type
		self._target_modality_type = target_modality_type

		# Only align samples present in both modalities
		common = set(reference_modality.keys()) & set(target_modality.keys())
		common.discard("merged")
		self._common_samples = sorted(common)

		self._dataset_completed_event = threading.Event()
		self._aligned_coordinates: dict[str, np.ndarray] = {}
		self._gui_interface = DirectMappingAlignmentGUI(
			dataset_size=len(self._common_samples),
			dataset_completed_event=self._dataset_completed_event
		)

	# --- Data Loading ---

	def _load_ome_tiff(self, filename: str) -> tuple[np.ndarray, tuple, tuple]:
		"""
		Load an OME-TIFF file, returning the lowest pyramid level as RGB uint8.

		Handles SubIFD-based pyramids (new format, written with subifds + ome=True),
		direct SubIFD page access (fallback when series.levels doesn't expose SubIFDs),
		and multi-series pyramids (old format with a separate series per level).

		Returns
		-------
		tuple of (image_rgb, lowest_shape, original_shape)
			image_rgb: uint8 array (H, W, 3) at lowest pyramid resolution
			lowest_shape: (H_low, W_low[, C]) shape of the loaded level
			original_shape: (H_orig, W_orig[, C]) shape of the full-resolution level
		"""
		if not os.path.exists(filename):
			raise FileNotFoundError(f"File not found: {filename}")

		with tifffile.TiffFile(filename) as tif:
			series0 = tif.series[0]
			original_shape = series0.shape

			# Priority 1: SubIFD pyramid via series.levels (modern tifffile with ome=True + subifds)
			if len(series0.levels) > 1:
				lowest = series0.levels[-1]
				image_data = lowest.asarray()
				lowest_shape = lowest.shape

			# Priority 2: direct SubIFD page access — handles ome=True + subifds written by
			# tifffile versions that don't expose SubIFDs through series.levels
			elif tif.pages[0].pages:
				lowest_page = tif.pages[0].pages[-1]
				image_data = lowest_page.asarray()
				lowest_shape = image_data.shape

			# Priority 3: separate top-level series per pyramid level (old ome_types format)
			elif len(tif.series) > 1:
				lowest = tif.series[-1]
				image_data = lowest.asarray()
				lowest_shape = lowest.shape

			# Priority 4: single-level file (no pyramid at all)
			else:
				image_data = series0.asarray()
				lowest_shape = series0.shape

		# Squeeze leading singleton OME dimensions (e.g. T=1, Z=1 from TZCYX)
		# so that all shapes are ≤ 3D before passing to _image_to_rgb_uint8.
		while image_data.ndim > 3 and image_data.shape[0] == 1:
			image_data = image_data[0]
		while len(lowest_shape) > 3 and lowest_shape[0] == 1:
			lowest_shape = lowest_shape[1:]
		while len(original_shape) > 3 and original_shape[0] == 1:
			original_shape = original_shape[1:]

		return _image_to_rgb_uint8(image_data, lowest_shape, original_shape)

	def _load_anndata_spots(self, filename: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
		"""
		Load spatial data from an AnnData file for GUI display.

		Returns
		-------
		tuple of (coordinates, spot_size, foreground_mask, leiden_labels, color_map)
			coordinates: (N, 2) float32 spatial coordinates
			spot_size: (2,) float32 spot dimensions [x, y]
			foreground_mask: (N,) bool foreground indicator
			leiden_labels: (N,) str cluster labels
			color_map: dict mapping cluster label → hex color
		"""
		if not os.path.exists(filename):
			raise FileNotFoundError(f"File not found: {filename}")

		adata = anndata.read_h5ad(filename)

		if 'spatial' not in adata.obsm:
			raise ValueError(f"AnnData file missing .obsm['spatial']: {filename}")
		coordinates = np.asarray(adata.obsm['spatial'], dtype=np.float32)

		# Spot size (matching preprocessing convention)
		if 'spot_size' in adata.uns:
			spot_size = np.asarray(adata.uns['spot_size'], dtype=np.float32).flatten()
			if spot_size.size == 1:
				spot_size = np.array([float(spot_size[0]), float(spot_size[0])], dtype=np.float32)
		else:
			spot_size = np.array([1.0, 1.0], dtype=np.float32)

		# Foreground mask
		if 'foreground' in adata.obs:
			foreground_mask = np.asarray(adata.obs['foreground'].values, dtype=bool)
		else:
			foreground_mask = np.ones(adata.n_obs, dtype=bool)

		# Leiden clustering labels
		if 'leiden' in adata.obs:
			leiden_labels = np.asarray(adata.obs['leiden'].values, dtype=str)
		elif 'clusters' in adata.obs:
			leiden_labels = np.asarray(adata.obs['clusters'].values, dtype=str)
		else:
			leiden_labels = np.zeros(adata.n_obs, dtype=str)

		# Generate color map for the cluster labels
		color_map = _generate_cluster_colors(leiden_labels)

		del adata
		return coordinates, spot_size, foreground_mask, leiden_labels, color_map

	# --- GUI Data Preparation ---

	def _prepare_image_data(self, filename: str, modality_name: str):
		"""Prepare image modality data for the GUI. Returns (metadata, payload, scale_factors)."""
		image, lowest_shape, original_shape = self._load_ome_tiff(filename)
		payload = Image.fromarray(image)
		metadata = {
			"modality_type": "IMAGE",
			"modality_name": modality_name,
			"image_shape": [int(lowest_shape[0]), int(lowest_shape[1])]
		}
		scale_factors = np.array([
			original_shape[0] / lowest_shape[0],
			original_shape[1] / lowest_shape[1]
		])
		return metadata, payload, scale_factors

	def _prepare_spot_data(self, filename: str, modality_name: str):
		"""Prepare spot modality data for the GUI. Returns (metadata, payload, scale_factors)."""
		coordinates, spot_size, foreground_mask, leiden_labels, color_map = self._load_anndata_spots(filename)

		# Build stable integer mapping for cluster labels (consecutive ints starting at 0)
		unique_labels = sorted(set(str(l) for l in leiden_labels))
		label_to_int = {lbl: idx for idx, lbl in enumerate(unique_labels)}

		payload = [
			{
				"spatial": coord.tolist(),
				"class": label_to_int[str(label)],
				"foreground": bool(fg),
				"color": color_map.get(str(label), _CLUSTER_PALETTE[0])
			}
			for coord, label, fg in zip(coordinates, leiden_labels, foreground_mask)
		]
		metadata = {
			"modality_type": "SPOT",
			"modality_name": modality_name,
			"spot_size": spot_size.tolist(),
			"color_map": color_map
		}
		# Spot coordinates are already in physical space, no scaling needed
		scale_factors = np.array([1.0, 1.0])
		return metadata, payload, scale_factors

	def _prepare_modality_data(self, filename: str, modality_name: str, modality_type: str):
		"""Dispatch to the appropriate loader based on modality type."""
		if modality_type in _IMAGE_MODALITIES:
			return self._prepare_image_data(filename, modality_name)
		elif modality_type in _SPOT_MODALITIES:
			return self._prepare_spot_data(filename, modality_name)
		else:
			raise ValueError(f"Unsupported modality type: {modality_type}")

	# --- Alignment Thread ---

	def _align_dataset_thread(self, **kwargs) -> None:
		try:
			force_recomputing = kwargs.get("force_recomputing", False)

			for sample_index, sample_id in enumerate(self._common_samples):
				# Check cache
				if not force_recomputing:
					aligned_target_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")
					if os.path.exists(aligned_target_file):
						adata = anndata.read_h5ad(aligned_target_file)
						if f'{self._reference_modality_name}_spatial' in adata.obsm.keys():
							del adata
							continue
						del adata

				# Prepare data for both modalities
				ref_metadata, ref_payload, ref_scale_factors = self._prepare_modality_data(
					self._reference_modality[sample_id],
					self._reference_modality_name,
					self._reference_modality_type
				)
				tgt_metadata, tgt_payload, _ = self._prepare_modality_data(
					self._target_modality[sample_id],
					self._target_modality_name,
					self._target_modality_type
				)

				# Launch GUI for this sample (blocks until user confirms)
				alignment_result = self._gui_interface.align_sample(
					sample_id=sample_id,
					sample_index=sample_index + 1,
					reference_metadata=ref_metadata,
					target_metadata=tgt_metadata,
					reference_payload=ref_payload,
					target_payload=tgt_payload
				)

				# Parse alignment result
				aligned_coordinates = self._parse_alignment_result(alignment_result, tgt_payload)

				if aligned_coordinates is not None:
					# Scale coordinates from display resolution to original resolution
					# aligned_coordinates: (N, 2) where col 0 = x, col 1 = y
					# ref_scale_factors: [y_scale, x_scale]
					aligned_coordinates[:, 0] *= ref_scale_factors[1]  # x
					aligned_coordinates[:, 1] *= ref_scale_factors[0]  # y
					self._aligned_coordinates[sample_id] = aligned_coordinates.copy()

		except Exception as e:
			import traceback
			print(f"[Alignment] Thread error: {e}\n{traceback.format_exc()}", flush=True)
			self._gui_interface.set_error(str(e))
		finally:
			self._dataset_completed_event.set()

	@staticmethod
	def _parse_alignment_result(alignment_result: dict, target_payload) -> np.ndarray | None:
		"""Extract aligned coordinates from the GUI result."""
		if "spots" in alignment_result:
			spots = alignment_result["spots"]
			num_spots = len(target_payload) if isinstance(target_payload, list) else (
				max((s.get("id", 0) for s in spots), default=-1) + 1 if spots else 0
			)
			if num_spots <= 0:
				return None

			coords = np.full((num_spots, 2), np.nan, dtype=np.float32)
			for spot in spots:
				idx = spot.get("id")
				px, py = spot.get("pixel_x"), spot.get("pixel_y")
				if idx is not None and 0 <= idx < num_spots and px is not None and py is not None:
					coords[idx] = [px, py]
			return coords

		elif "corner_pixels" in alignment_result:
			return np.array(alignment_result["corner_pixels"], dtype=np.float32)

		return None

	# --- Save Results ---

	def _save_image_to_image(self, aligned_samples: dict) -> dict:
		"""IMAGE → IMAGE: crop reference to aligned bounding box, save as OME-TIFF with compression."""
		for sample_id, aligned_coords in self._aligned_coordinates.items():
			reference_file = self._reference_modality[sample_id]

			min_x, max_x = int(np.nanmin(aligned_coords[:, 0])), int(np.nanmax(aligned_coords[:, 0]))
			min_y, max_y = int(np.nanmin(aligned_coords[:, 1])), int(np.nanmax(aligned_coords[:, 1]))

			with tifffile.TiffFile(reference_file) as tif:
				series = tif.series[0]
				img_shape = series.shape
				h, w = img_shape[-2], img_shape[-1]

				min_x, min_y = max(0, min_x), max(0, min_y)
				max_x, max_y = min(w, max_x), min(h, max_y)

				if min_x >= max_x or min_y >= max_y:
					print(f"Invalid crop for sample {sample_id}")
					continue

				slices = [slice(None)] * len(img_shape)
				slices[-2] = slice(min_y, max_y)
				slices[-1] = slice(min_x, max_x)
				crop_data = series.asarray()[tuple(slices)]

			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "ome.tiff")

			tifffile.imwrite(aligned_file, crop_data, compression='zlib')
			aligned_samples[sample_id] = aligned_file

		return aligned_samples

	def _save_spot_alignment(self, aligned_samples: dict) -> dict:
		"""IMAGE→SPOT or SPOT→SPOT: store aligned coords in AnnData, save with compression.

		The aligned file accumulates obsm keys across multiple alignment passes (one per
		modality pair). If an aligned file already exists for this target, it is loaded
		and updated rather than recreated from scratch, so previously added obsm keys are
		preserved.
		"""
		for sample_id, aligned_coords in self._aligned_coordinates.items():
			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")

			# Load existing aligned file to preserve previously added obsm keys;
			# fall back to the preprocessed target file on the first pass.
			if os.path.exists(aligned_file):
				adata = anndata.read_h5ad(aligned_file)
			else:
				adata = anndata.read_h5ad(self._target_modality[sample_id])

			adata.obsm[f'{self._reference_modality_name}_spatial'] = aligned_coords.astype(np.float32)
			adata.write_h5ad(aligned_file, compression=_H5AD_COMPRESSION)
			del adata

			aligned_samples[sample_id] = aligned_file

		# Generate merged aligned dataset
		aligned_files = []
		for sample_id in self._common_samples:
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")
			if os.path.exists(aligned_file):
				aligned_files.append(aligned_file)
				aligned_samples[sample_id] = aligned_file

		if aligned_files:
			alignment_folder = os.path.join(self._path, "merged", "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")

			anndata.experimental.concat_on_disk(
				aligned_files, merged_file,
				merge="same", uns_merge="same"
			)
			aligned_samples["merged"] = merged_file

		return aligned_samples

	# --- Public API ---

	def uniform_aligned_dataset(self, force_recomputing: bool = False) -> dict[str, str]:
		"""
		Produce an aligned dataset without GUI interaction (passthrough alignment).
		Used when the target modality's coordinates are already expressed in the
		reference modality's coordinate system (e.g. 10x Visium spots on H&E).

		The obsm key for this pair is added to the shared aligned file for the target
		modality, preserving any obsm keys added by previous alignment passes.

		Returns
		-------
		dict[str, str]
			Maps sample IDs (and "merged") to aligned file paths.
		"""
		aligned_samples: dict[str, str] = {}
		obsm_key = f'{self._reference_modality_name}_spatial'

		for sample_id, processed_target_file in self._target_modality.items():
			if sample_id == "merged":
				continue

			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")

			# Check if this specific obsm key already exists (may have been set by a previous pass)
			needs_write = force_recomputing
			if not needs_write:
				if os.path.exists(aligned_file):
					tmp = anndata.read_h5ad(aligned_file)
					needs_write = obsm_key not in tmp.obsm
					del tmp
				else:
					needs_write = True

			if needs_write:
				# Load existing aligned file to preserve other obsm keys; fall back to preprocessed.
				if os.path.exists(aligned_file) and not force_recomputing:
					adata = anndata.read_h5ad(aligned_file)
				else:
					adata = anndata.read_h5ad(processed_target_file)
				adata.obsm[obsm_key] = adata.obsm['spatial'].copy()
				adata.write_h5ad(aligned_file, compression=_H5AD_COMPRESSION)
				del adata

		# Build merged dataset
		aligned_files = []
		for sample_id in self._common_samples:
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")
			aligned_files.append(aligned_file)
			aligned_samples[sample_id] = aligned_file

		merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
		aligned_samples["merged"] = merged_file

		if not os.path.exists(merged_file) or force_recomputing:
			alignment_folder = os.path.join(self._path, "merged", "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			anndata.experimental.concat_on_disk(
				aligned_files, merged_file,
				merge="same", uns_merge="same"
			)

		return aligned_samples

	def is_alignment_needed(self, force_recomputing: bool = False) -> bool:
		"""Return True if at least one sample still needs to be aligned."""
		if force_recomputing:
			return len(self._common_samples) > 0
		for sample_id in self._common_samples:
			aligned_target_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")
			if not os.path.exists(aligned_target_file):
				return True
			adata = anndata.read_h5ad(aligned_target_file)
			has_key = f'{self._reference_modality_name}_spatial' in adata.obsm.keys()
			del adata
			if not has_key:
				return True
		return False

	def collect_aligned_files(self) -> dict[str, str]:
		"""Return paths to already-aligned files without starting the GUI."""
		aligned_samples: dict[str, str] = {}
		is_target_image = self._target_modality_type in _IMAGE_MODALITIES
		file_ext = "ome.tiff" if is_target_image else "h5ad"
		for sample_id in self._common_samples:
			aligned_file = MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, file_ext)
			if os.path.exists(aligned_file):
				aligned_samples[sample_id] = aligned_file
		if not is_target_image:
			merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
			if os.path.exists(merged_file):
				aligned_samples["merged"] = merged_file
		return aligned_samples

	def align_dataset(self, force_recomputing: bool = False, on_gui_done=None) -> dict[str, str]:
		"""
		Align the target modality to the reference using the interactive GUI.

		Starts the alignment GUI, processes all samples, then saves results.

		Parameters
		----------
		force_recomputing : bool
			If True, re-run alignment even if cached results exist.
		on_gui_done : callable, optional
			Called immediately after the alignment GUI server shuts down and
			before file saving begins.  Use this to emit a progress update so
			callers know the pipeline is still running while files are written.

		Returns
		-------
		dict[str, str]
			Maps sample IDs (and "merged") to aligned file paths.
		"""
		aligned_samples: dict[str, str] = {}

		if not self._common_samples:
			return aligned_samples

		# Start alignment in background thread
		alignment_thread = threading.Thread(
			name="Align Dataset Thread",
			target=self._align_dataset_thread,
			kwargs={"force_recomputing": force_recomputing},
			daemon=True
		)
		alignment_thread.start()

		# Block until GUI completes
		self._gui_interface.enable_gui()

		# Notify caller that the GUI is done and file saving is about to start
		if on_gui_done is not None:
			on_gui_done()

		# Determine combination and save
		is_ref_image = self._reference_modality_type in _IMAGE_MODALITIES
		is_target_image = self._target_modality_type in _IMAGE_MODALITIES
		is_ref_spot = self._reference_modality_type in _SPOT_MODALITIES
		is_target_spot = self._target_modality_type in _SPOT_MODALITIES

		if is_ref_image and is_target_image:
			aligned_samples = self._save_image_to_image(aligned_samples)

		elif (is_ref_image and is_target_spot) or (is_ref_spot and is_target_spot):
			aligned_samples = self._save_spot_alignment(aligned_samples)

		elif is_ref_spot and is_target_image:
			print("Reference Spot and Target Image alignment is not implemented yet.")

		return aligned_samples
