import os, json, h5py, tifffile, threading, anndata, logging
import numpy as np
from PIL import Image
from sklearn.decomposition import NMF

from focus.constants import MODALITY_ALIGNMENT, MODALITY_ALIGNMENT_MERGED
from focus.constants import ModalityType, IMAGE_MODALITY_TYPES, SPOT_MODALITY_TYPES
from focus.utils import write_h5ad_compat, concat_on_disk_compat, read_merged_sample_ids, hw_from_axes

from focus.GUI.direct_mapping_alignment import DirectMappingAlignmentGUI
from focus.alignment.spot_payload import pack_spots, acquisition_basis
from focus.spot_colors import cluster_colors, label_order
from focus.provenance import (
	cached_fingerprint, content_signature, fingerprint, read_ome_annotations,
	PIXEL_DIGEST_KEY, CROP_OFFSET_KEY, SUPERSEDED_SIGNATURE_KEY,
)
from focus.reporting import get_reporter

logger = logging.getLogger("focus.alignment")

_H5AD_COMPRESSION = "gzip"

# Modality type groupings (shared with the orchestrator and config validation)
_IMAGE_MODALITIES = IMAGE_MODALITY_TYPES

# Alignment steps of one modality pair, as (index, total, name) for StepReporter.
_STEP_ALIGN = (1, 3, "Aligning samples")
_STEP_SAVE = (2, 3, "Saving aligned coordinates")
_STEP_MERGE = (3, 3, "Merging aligned samples")


def _first(pair: tuple):
	"""Sample id of a (sample_id, value) loop item."""
	return pair[0]
_SPOT_MODALITIES = SPOT_MODALITY_TYPES


def _generate_cluster_colors(labels: np.ndarray) -> dict[str, str]:
	"""Generate a color map for cluster labels using a perceptually distinct palette."""
	return cluster_colors(labels)


def _image_to_rgb_uint8(image_data: np.ndarray, axes: str | None) -> np.ndarray:
	"""
	Convert loaded image data to an RGB uint8 (H, W, 3) array for GUI display.

	The channel / Y / X axes are located from the OME-TIFF ``axes`` metadata rather than
	guessed positionally, so multi-channel channel-first images (e.g. Raman 'CYX') and
	channel-last RGB images (e.g. 'YXC') are both handled correctly.

	Handles:
	- collapsing extra axes (T, Z, …) by taking their first index
	- dtype conversion to uint8
	- moving the channel axis last (HWC)
	- 1 channel (grayscale) → RGB by triplication
	- 2 channels → pad with zeros to 3
	- 3 channels → keep as-is
	- 4+ channels → NMF reduction to 3 components
	"""
	# The colour axis is 'C' (separate channels, e.g. Raman) or 'S' (samples-per-pixel of an
	# interleaved RGB, how tifffile labels microscopy). Use the axes string only when it lines
	# up with the array; otherwise fall back to the positional heuristic further below.
	_COLOUR = ("C", "S")
	axes = (axes or "").upper()
	use_axes = len(axes) == image_data.ndim and "Y" in axes and "X" in axes

	if use_axes:
		# Collapse any non-spatial, non-colour axes (T, Z, …) by selecting the first index,
		# until the array is at most 3-D (Y, X[, colour]).
		while image_data.ndim > 3:
			drop = next((i for i, ax in enumerate(axes) if ax not in ("Y", "X", *_COLOUR)), 0)
			image_data = np.take(image_data, 0, axis=drop)
			axes = axes[:drop] + axes[drop + 1:]
	else:
		# No usable axes metadata: squeeze leading singleton dims to get to ≤ 3-D.
		while image_data.ndim > 3 and image_data.shape[0] == 1:
			image_data = image_data[0]

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

	# Ensure (H, W, colour): move the colour axis last.
	if image_data.ndim == 2:
		# Grayscale → (H, W, 1)
		image_data = image_data[:, :, np.newaxis]
	elif use_axes:
		colour = [i for i, ax in enumerate(axes) if ax in _COLOUR]
		c = colour[0] if colour else int(np.argmin(image_data.shape))
		if c != image_data.ndim - 1:
			image_data = np.moveaxis(image_data, c, -1)
	elif image_data.ndim == 3 and int(np.argmin(image_data.shape)) == 0:
		# No usable axes metadata: fall back to the channels-first positional heuristic.
		image_data = np.transpose(image_data, (1, 2, 0))

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

	return image_data


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
			target_modality_type: str,
			exact_spots: bool = False,
			reporter=None,
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
		self._exact_spots = exact_spots
		# Steps: 1/3 Aligning samples, 2/3 Saving aligned coordinates, 3/3 Merging aligned samples.
		self._reporter = reporter or get_reporter()

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

	# --- Output paths ---

	def _aligned_output_path(self, sample_id: str) -> str:
		"""Path of this pair's aligned artefact.

		Single source of truth for the writers and for every cache check, so a lookup can
		never disagree with what was written.

		IMAGE reference + IMAGE target: the artefact is the fixed (class-internal
		``reference_modality``, i.e. the pipeline's non-reference) image cropped to the
		overlap, so it carries THAT modality's name.

		SPOT reference: the artefact is the reference modality's own AnnData, which
		accumulates one ``obsm['{target}_spatial']`` key per target, so it carries the
		reference modality's name.
		"""
		if (self._reference_modality_type in _IMAGE_MODALITIES
				and self._target_modality_type in _IMAGE_MODALITIES):
			return MODALITY_ALIGNMENT(self._path, sample_id, self._reference_modality_name, "ome.tiff")
		return MODALITY_ALIGNMENT(self._path, sample_id, self._target_modality_name, "h5ad")

	# --- Data Loading ---

	def _load_ome_tiff(self, filename: str) -> tuple[np.ndarray, tuple[int, int], tuple[int, int]]:
		"""
		Load an OME-TIFF file, returning the lowest pyramid level as RGB uint8 for display
		plus the true (H, W) of both the display level and the full-resolution level.

		Handles SubIFD-based pyramids (new format, written with subifds + ome=True),
		direct SubIFD page access (fallback when series.levels doesn't expose SubIFDs),
		and multi-series pyramids (old format with a separate series per level).

		The (H, W) dimensions are derived from the OME ``axes`` metadata (via
		``hw_from_axes``), so they are correct for both channel-first ('CYX', e.g. Raman)
		and channel-last ('YXC', e.g. RGB microscopy) layouts. This is what makes the
		display->full-resolution ``scale_factors`` accurate for multi-channel images.

		Returns
		-------
		tuple of (image_rgb, (H_low, W_low), (H_full, W_full))
			image_rgb: uint8 array (H, W, 3) at lowest pyramid resolution
			(H_low, W_low): height/width of the loaded (display) level
			(H_full, W_full): height/width of the full-resolution level
		"""
		if not os.path.exists(filename):
			raise FileNotFoundError(f"File not found: {filename}")

		with tifffile.TiffFile(filename) as tif:
			series0 = tif.series[0]
			original_shape = series0.shape
			original_axes = getattr(series0, "axes", "") or ""

			# Priority 1: SubIFD pyramid via series.levels (modern tifffile with ome=True + subifds)
			if len(series0.levels) > 1:
				lowest = series0.levels[-1]
				image_data = lowest.asarray()
				lowest_shape = lowest.shape
				lowest_axes = getattr(lowest, "axes", "") or original_axes

			# Priority 2: direct SubIFD page access — handles ome=True + subifds written by
			# tifffile versions that don't expose SubIFDs through series.levels
			elif tif.pages[0].pages:
				lowest_page = tif.pages[0].pages[-1]
				image_data = lowest_page.asarray()
				lowest_shape = image_data.shape
				lowest_axes = getattr(lowest_page, "axes", "") or ""

			# Priority 3: separate top-level series per pyramid level (old ome_types format)
			elif len(tif.series) > 1:
				lowest = tif.series[-1]
				image_data = lowest.asarray()
				lowest_shape = lowest.shape
				lowest_axes = getattr(lowest, "axes", "") or original_axes

			# Priority 4: single-level file (no pyramid at all)
			else:
				image_data = series0.asarray()
				lowest_shape = series0.shape
				lowest_axes = original_axes

		# Identify the true (H, W) of each level from OME axes metadata — never positional
		# guessing — so the scale is correct regardless of channel-axis position/count.
		H_full, W_full = hw_from_axes(original_shape, original_axes)
		H_low, W_low = hw_from_axes(lowest_shape, lowest_axes)

		image_rgb = _image_to_rgb_uint8(image_data, lowest_axes)
		return image_rgb, (H_low, W_low), (H_full, W_full)

	def _load_anndata_spots(self, filename: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict]:
		"""
		Load spatial data from an AnnData file for GUI display.

		Returns
		-------
		tuple of (coordinates, spot_size, foreground_mask, cluster_labels, color_map)
			coordinates: (N, 2) float32 spatial coordinates
			spot_size: (2,) float32 spot dimensions [x, y]
			cluster_labels: (N,) str cluster labels
			color_map: dict mapping cluster label → hex color
		"""
		if not os.path.exists(filename):
			raise FileNotFoundError(f"File not found: {filename}")

		adata = anndata.read_h5ad(filename, backed="r")

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

		# Cluster labels (preprocessing writes 'cluster'; 'leiden'/'clusters' kept as
		# fallbacks so files processed by older versions still display).
		if 'cluster' in adata.obs:
			cluster_labels = np.asarray(adata.obs['cluster'].values, dtype=str)
		elif 'leiden' in adata.obs:
			cluster_labels = np.asarray(adata.obs['leiden'].values, dtype=str)
		elif 'clusters' in adata.obs:
			cluster_labels = np.asarray(adata.obs['clusters'].values, dtype=str)
		else:
			cluster_labels = np.zeros(adata.n_obs, dtype=str)

		# Generate color map for the cluster labels
		color_map = _generate_cluster_colors(cluster_labels)
		self._spot_basis = acquisition_basis(adata)
		adata.file.close()
		del adata
		return coordinates, spot_size, foreground_mask, cluster_labels, color_map

	# --- GUI Data Preparation ---

	def _prepare_image_data(self, filename: str, modality_name: str):
		"""Prepare image modality data for the GUI.

		Returns (metadata, payload, full_coordinates, scale_factors).
		full_coordinates is None for IMAGE modalities.
		"""
		image, (H_low, W_low), (H_full, W_full) = self._load_ome_tiff(filename)
		payload = Image.fromarray(image)
		metadata = {
			"modality_type": "IMAGE",
			"modality_name": modality_name,
			"image_shape": [int(H_low), int(W_low)]
		}
		# Display (lowest level) -> full-resolution scale, per axis [y_scale, x_scale].
		scale_factors = np.array([
			H_full / H_low,
			W_full / W_low
		])
		return metadata, payload, None, scale_factors

	def _prepare_spot_data(self, filename: str, modality_name: str):
		"""Native centers and footprints; compact transport never pools labels or geometry."""
		if self._exact_spots:
			return self._prepare_exact_spot_data(filename, modality_name)
		coordinates, spot_size, foreground, labels, colors = self._load_anndata_spots(filename)
		unique = label_order(labels)
		indices = {label: i for i, label in enumerate(unique)}
		classes = np.fromiter((indices[str(label)] for label in labels), dtype=np.uint32, count=len(labels))
		metadata = {"modality_type": "SPOT", "modality_name": modality_name,
			"spot_size": spot_size.tolist(), "color_map": colors,
			"cluster_labels": unique,
			"spot_encoding": "FSP1", "spot_count": len(coordinates)}
		basis = getattr(self, '_spot_basis', None)
		if basis is not None:
			metadata['spot_basis'] = basis
		valid = np.flatnonzero(np.isfinite(coordinates).all(axis=1))
		if not len(valid):
			raise ValueError(f"No finite display coordinates in {filename}")
		# Reorder delivery for mesh culling, preserving every original center and row ID.
		xy = coordinates[valid]
		tiles = np.floor((xy - xy.min(axis=0)) / (np.maximum(spot_size, 1e-6) * 32)).astype(np.int64)
		order = valid[np.lexsort((tiles[:, 1], tiles[:, 0]))]
		metadata['spot_count'] = len(order)
		return metadata, pack_spots(coordinates[order], classes[order], foreground[order], order), coordinates, np.array([1.0, 1.0])

	def _prepare_exact_spot_data(self, filename: str, modality_name: str):
		"""Microgrid: foreground spots only, at native positions, in a single class."""
		coordinates, spot_size, foreground, _, _ = self._load_anndata_spots(filename)
		foreground_only = bool(foreground.any())
		indices = np.flatnonzero(foreground) if foreground_only else np.arange(len(coordinates))
		colors = cluster_colors(['0'])
		metadata = {"modality_type": "SPOT", "modality_name": modality_name,
			"spot_size": spot_size.tolist(), "foreground_only": foreground_only,
			"cluster_labels": ['0'], "color_map": colors,
			"spot_encoding": "FSP1", "spot_count": len(indices)}
		payload = pack_spots(coordinates[indices], np.zeros(len(indices), dtype=np.uint32), foreground[indices], indices)
		return metadata, payload, coordinates, np.array([1.0, 1.0])

	def _prepare_modality_data(self, filename: str, modality_name: str, modality_type: str):
		"""Dispatch to the appropriate loader based on modality type.

		Always returns a 4-tuple (metadata, payload, full_coordinates, scale_factors).
		full_coordinates is None for IMAGE modalities.
		"""
		if modality_type in _IMAGE_MODALITIES:
			return self._prepare_image_data(filename, modality_name)
		elif modality_type in _SPOT_MODALITIES:
			return self._prepare_spot_data(filename, modality_name)
		else:
			raise ValueError(f"Unsupported modality type: {modality_type}")

	def _fixed_annotations(self, sample_id) -> dict:
		"""FOCUS provenance of the fixed layer (empty unless it is an annotated FOCUS image)."""
		if self._reference_modality_type not in _IMAGE_MODALITIES:
			return {}
		return read_ome_annotations(self._reference_modality[sample_id]) or {}

	def _crop_offset(self, sample_id) -> np.ndarray:
		"""
		Pixel offset ``(x, y)`` of the fixed image's origin in its source image.

		Preprocessing crops microscopy images to the tissue and records where the crop starts.
		A microscopy image without that record (written before it existed, reachable only when
		preprocessing is skipped) gets offset 0 with a warning.
		"""
		value = self._fixed_annotations(sample_id).get(CROP_OFFSET_KEY)
		if value is not None:
			return np.array([float(v) for v in value.split(",")], dtype=np.float32)
		if self._reference_modality_type == ModalityType.MICROSCOPY_IMAGE and not getattr(self, "_warned_offset", False):
			self._warned_offset = True
			(getattr(self, "_reporter", None) or get_reporter()).warning(
				f"'{self._reference_modality_name}' records no crop offset (preprocessed before it was stored): "
				f"pre-aligned coordinates are used without one"
			)
		return np.zeros(2, dtype=np.float32)

	def _input_key(self, sample_id, pre_aligned: bool = False):
		"""
		Fingerprint of the inputs an alignment was made on.

		Each side is identified by ``content_signature``: the level-0 pixel digest of a FOCUS
		image, the preprocessing fingerprint of an AnnData, otherwise the file's stat. A
		pre-aligned image pair also depends on the image's crop offset.
		"""
		payload = {"version": 1, "fixed": content_signature(self._reference_modality[sample_id]),
			"moving": content_signature(self._target_modality[sample_id])}
		if pre_aligned and self._reference_modality_type in _IMAGE_MODALITIES:
			payload["offset"] = [float(v) for v in self._crop_offset(sample_id)]
		return fingerprint(payload)

	def _legacy_input_key(self, sample_id):
		"""
		The key an alignment made on the file a regenerated image replaced would carry, or None.

		Preprocessing records the replaced file's stat signature only when the regenerated
		image's level-0 pixels are identical to it, so a match proves the alignment still holds.
		"""
		superseded = self._fixed_annotations(sample_id).get(SUPERSEDED_SIGNATURE_KEY)
		if not superseded:
			return None
		return fingerprint({"version": 1, "fixed": json.loads(superseded),
			"moving": content_signature(self._target_modality[sample_id])})

	def _restamp_input_key(self, path: str, key: str) -> None:
		"""Replace this pair's stored input key in the aligned file, in place."""
		with h5py.File(path, "r+") as f:
			if "uns/focus_alignment_inputs" in f:
				anndata.io.write_elem(f["uns/focus_alignment_inputs"], self._reference_modality_name, key)
			else:
				anndata.io.write_elem(f["uns"], "focus_alignment_inputs", {self._reference_modality_name: key})

	def _cache_matches(self, sample_id, pre_aligned: bool = False):
		"""
		True when the stored alignment of ``sample_id`` still matches its inputs.

		The stored input key is compared for pre-aligned pairs, pairs involving ST, and pairs
		whose fixed layer is a FOCUS image with a pixel digest (against a fingerprinted anchor).
		A manual alignment made on an image that preprocessing later regenerated with identical
		pixels is accepted through the replaced file's signature, and its key is re-stamped.
		"""
		path = self._aligned_output_path(sample_id)
		if not os.path.exists(path):
			return False
		if self._target_modality_type in _IMAGE_MODALITIES:
			return True
		name = self._reference_modality_name
		with h5py.File(path, 'r') as f:
			if f"obsm/{name}_spatial" not in f:
				return False
			key = f"uns/focus_alignment_inputs/{name}"
			stored = None
			if key in f:
				stored = f[key][()]
				stored = stored.decode() if isinstance(stored, bytes) else str(stored)

		fixed_is_image = self._reference_modality_type in _IMAGE_MODALITIES
		annotations = self._fixed_annotations(sample_id)
		digested = PIXEL_DIGEST_KEY in annotations
		has_st = ModalityType.ST in (self._target_modality_type, self._reference_modality_type)
		checked = (pre_aligned or has_st
			or (digested and cached_fingerprint(self._target_modality[sample_id]) is not None))
		if not checked:
			return True

		expected = self._input_key(sample_id, pre_aligned)
		if stored == expected:
			return True
		if not pre_aligned and fixed_is_image:
			legacy = self._legacy_input_key(sample_id)
			if legacy is not None and (stored == legacy or (stored is None and not has_st)):
				self._restamp_input_key(path, expected)
				return True
		if digested and stored is not None and not pre_aligned:
			warned = self.__dict__.setdefault("_warned_changed", set())
			if sample_id not in warned:
				warned.add(sample_id)
				(getattr(self, "_reporter", None) or get_reporter()).warning(
					f"'{name}' changed since it was aligned: the alignment must be redone"
				)
		return False

	def _fresh_aligned_adata(self, sample_id):
		# Always refresh counts/labels/metadata from preprocessing. Preserve other pair
		# coordinates only when observation identity and native geometry still agree.
		adata = anndata.read_h5ad(self._target_modality[sample_id])
		path = self._aligned_output_path(sample_id)
		if os.path.exists(path):
			old = anndata.read_h5ad(path, backed='r')
			if (old.obs_names.equals(adata.obs_names) and 'spatial' in old.obsm
					and np.array_equal(old.obsm['spatial'], adata.obsm['spatial'])):
				for key, values in old.obsm.items():
					if key.endswith('_spatial'):
						adata.obsm[key] = values.copy()
				adata.uns['focus_alignment_inputs'] = dict(old.uns.get('focus_alignment_inputs', {}))
			old.file.close()
		return adata

	# --- Alignment Thread ---

	def _align_dataset_thread(self, **kwargs) -> None:
		try:
			force_recomputing = kwargs.get("force_recomputing", False)

			is_target_image = self._target_modality_type in _IMAGE_MODALITIES
			samples = self._reporter.tqdm(self._common_samples, *_STEP_ALIGN, unit="sample")
			for sample_index, sample_id in enumerate(samples):
				if not force_recomputing and self._cache_matches(sample_id):
					self._reporter.cached()
					continue

				# Prepare data for both modalities
				ref_metadata, ref_payload, _, ref_scale_factors = self._prepare_modality_data(
					self._reference_modality[sample_id],
					self._reference_modality_name,
					self._reference_modality_type
				)
				tgt_metadata, tgt_payload, tgt_full_coords, _ = self._prepare_modality_data(
					self._target_modality[sample_id],
					self._target_modality_name,
					self._target_modality_type
				)

				# Launch GUI for this sample (blocks until user confirms).
				# Only the display payload is sent to the browser; full coordinates stay here.
				alignment_result = self._gui_interface.align_sample(
					sample_id=sample_id,
					sample_index=sample_index + 1,
					reference_metadata=ref_metadata,
					target_metadata=tgt_metadata,
					reference_payload=ref_payload,
					target_payload=tgt_payload
				)

				# Parse alignment result; pass full coordinates so the transform is applied
				# to every spot in the original dataset, not just the displayed subset.
				aligned_coordinates = self._parse_alignment_result(
					alignment_result, tgt_payload, tgt_full_coords
				)

				if aligned_coordinates is not None:
					# Scale coordinates from display resolution to original resolution
					# aligned_coordinates: (N, 2) where col 0 = x, col 1 = y
					# ref_scale_factors: [y_scale, x_scale]
					aligned_coordinates[:, 0] *= ref_scale_factors[1]  # x
					aligned_coordinates[:, 1] *= ref_scale_factors[0]  # y
					self._aligned_coordinates[sample_id] = aligned_coordinates.copy()

		except Exception as e:
			logger.debug("Alignment thread error", exc_info=True)
			self._reporter.warning(f"Alignment failed: {e}")
			self._gui_interface.set_error(str(e))
		finally:
			self._dataset_completed_event.set()

	@staticmethod
	def _parse_alignment_result(
		alignment_result: dict,
		target_payload,
		full_coordinates: np.ndarray | None = None,
	) -> np.ndarray | None:
		"""Extract aligned coordinates from the GUI result.

		Parameters
		----------
		alignment_result : dict
			Payload POSTed by the frontend on /confirm.
		target_payload : bytes | list[dict] | Image.Image
			The display payload that was sent to the browser (FSP1 bytes for spot modalities).
		full_coordinates : np.ndarray | None
			Full (N, 2) float32 coordinate array for SPOT modalities.  When present and the
			frontend supplies a ``transform_matrix`` key, the 3×3 matrix is applied directly to
			every spot, so the result never depends on which spots the browser drew.
		"""
		# Preferred path: frontend sends the 3×3 matrix, applied to every original spot.
		if "transform_matrix" in alignment_result and full_coordinates is not None:
			# The frontend serialises a gl-matrix mat3, which is COLUMN-MAJOR, so it must be
			# reshaped column-first (order="F"). A plain row-major reshape(3, 3) would build the
			# TRANSPOSE of the true matrix — transposing the linear part and, critically, pushing
			# the translation into the homogeneous row where it is silently dropped. That is
			# harmless only when the translation is ~0 (no flip, origin-anchored data); a flip
			# needs a large repositioning translation, so dropping it sends every spot into the
			# negative quadrant (off-image -> empty registered features).
			mat = np.array(alignment_result["transform_matrix"], dtype=np.float64).reshape(3, 3, order="F")
			n = len(full_coordinates)
			hom = np.column_stack([full_coordinates.astype(np.float64), np.ones(n)])
			out = (mat @ hom.T).T                     # (n, 3) homogeneous
			w = out[:, 2:3]
			w[w == 0] = 1.0                           # guard; affine transforms keep w == 1
			transformed = out[:, :2] / w              # divide-through handles projective ('distort') too
			return transformed.astype(np.float32)

		if "spots" in alignment_result:
			# Fallback only: SPOT→SPOT / IMAGE→SPOT always send a transform_matrix (handled above).
			spots = alignment_result["spots"]
			# Use the full coordinate count when available so the output array is always
			# sized to match the AnnData obs dimension, not the (possibly smaller) display subset.
			if full_coordinates is not None:
				num_spots = len(full_coordinates)
			elif isinstance(target_payload, list):
				num_spots = len(target_payload)
			else:
				num_spots = max((s.get("id", 0) for s in spots), default=-1) + 1 if spots else 0
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
		"""IMAGE → IMAGE: crop the fixed image to the aligned bounding box, save as OME-TIFF.

		The bounding box comes from the moving layer's mapped corners, so the crop keeps the
		region of the fixed image that the moving image covers. The output carries the fixed
		modality's name (see ``_aligned_output_path``).
		"""
		for sample_id, aligned_coords in self._reporter.tqdm(
				list(self._aligned_coordinates.items()), *_STEP_SAVE, unit="sample", id_of=_first):
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
					self._reporter.warning("Invalid crop: the aligned layers do not overlap; no output written")
					continue

				slices = [slice(None)] * len(img_shape)
				slices[-2] = slice(min_y, max_y)
				slices[-1] = slice(min_x, max_x)
				crop_data = series.asarray()[tuple(slices)]

			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = self._aligned_output_path(sample_id)

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
		for sample_id, aligned_coords in self._reporter.tqdm(
				list(self._aligned_coordinates.items()), *_STEP_SAVE, unit="sample", id_of=_first):
			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = self._aligned_output_path(sample_id)

			adata = self._fresh_aligned_adata(sample_id)

			adata.obsm[f'{self._reference_modality_name}_spatial'] = aligned_coords.astype(np.float32)
			adata.uns.setdefault('focus_alignment_inputs', {})[self._reference_modality_name] = self._input_key(sample_id)
			write_h5ad_compat(adata, aligned_file, compression=_H5AD_COMPRESSION)
			del adata

			aligned_samples[sample_id] = aligned_file

		# Generate merged aligned dataset
		aligned_files = []
		for sample_id in self._common_samples:
			aligned_file = self._aligned_output_path(sample_id)
			if os.path.exists(aligned_file):
				aligned_files.append(aligned_file)
				aligned_samples[sample_id] = aligned_file

		if aligned_files:
			merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
			rebuild = not os.path.exists(merged_file) or bool(self._aligned_coordinates)
			self._reporter.step(*_STEP_MERGE, cached=not rebuild)
			if rebuild:
				alignment_folder = os.path.join(self._path, "merged", "alignment")
				os.makedirs(alignment_folder, exist_ok=True)
				concat_on_disk_compat(
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

		For an image reference, the coordinates are in the source image's pixel frame: the
		preprocessed image's crop offset is subtracted so they land on the preprocessed pixels.

		The obsm key for this pair is added to the shared aligned file for the target
		modality, preserving any obsm keys added by previous alignment passes.

		Returns
		-------
		dict[str, str]
			Maps sample IDs (and "merged") to aligned file paths.
		"""
		aligned_samples: dict[str, str] = {}
		changed = False
		obsm_key = f'{self._reference_modality_name}_spatial'

		per_sample = [(sid, f) for sid, f in self._target_modality.items() if sid != "merged"]
		for sample_id, processed_target_file in self._reporter.tqdm(per_sample, *_STEP_ALIGN, unit="sample", id_of=_first):

			alignment_folder = os.path.join(self._path, sample_id, "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			aligned_file = self._aligned_output_path(sample_id)

			needs_write = force_recomputing or not self._cache_matches(sample_id, pre_aligned=True)
			if not needs_write:
				self._reporter.cached()
			else:
				adata = self._fresh_aligned_adata(sample_id)
				coords = adata.obsm['spatial'].copy()
				if self._reference_modality_type in _IMAGE_MODALITIES:
					# Pre-aligned coordinates are in the source image's pixel frame; the preprocessed
					# image starts where preprocessing cropped it.
					coords = (np.asarray(coords, dtype=np.float32) - self._crop_offset(sample_id)).astype(np.float32)
				adata.obsm[obsm_key] = coords
				adata.uns.setdefault('focus_alignment_inputs', {})[self._reference_modality_name] = self._input_key(
					sample_id, pre_aligned=True)
				write_h5ad_compat(adata, aligned_file, compression=_H5AD_COMPRESSION)
				changed = True
				del adata

		# Build merged dataset
		aligned_files = []
		for sample_id in self._common_samples:
			aligned_file = self._aligned_output_path(sample_id)
			if os.path.exists(aligned_file):
				aligned_files.append(aligned_file)
			aligned_samples[sample_id] = aligned_file

		merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
		aligned_samples["merged"] = merged_file

		rebuild = not os.path.exists(merged_file) or force_recomputing or changed
		self._reporter.step(*_STEP_MERGE, cached=not rebuild)
		if rebuild:
			alignment_folder = os.path.join(self._path, "merged", "alignment")
			os.makedirs(alignment_folder, exist_ok=True)
			concat_on_disk_compat(
				aligned_files, merged_file,
				merge="same", uns_merge="same"
			)

		return aligned_samples

	def is_alignment_needed(self, force_recomputing: bool = False) -> bool:
		"""Return True if at least one sample still needs to be aligned."""
		if force_recomputing:
			return len(self._common_samples) > 0
		return any(not self._cache_matches(sample_id) for sample_id in self._common_samples)

	def needs_merged_build(self) -> bool:
		"""Return True if per-sample files exist but the merged file is absent."""
		is_target_image = self._target_modality_type in _IMAGE_MODALITIES
		if is_target_image:
			return False
		merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
		if os.path.exists(merged_file):
			return False
		for sample_id in self._common_samples:
			if os.path.exists(self._aligned_output_path(sample_id)):
				return True
		return False

	def collect_aligned_files(self) -> dict[str, str]:
		"""Return paths to already-aligned files without starting the GUI.

		Creates the merged file from per-sample files if it is missing.
		"""
		aligned_samples: dict[str, str] = {}
		is_target_image = self._target_modality_type in _IMAGE_MODALITIES
		self._reporter.step(*_STEP_ALIGN, cached=True)
		self._reporter.step(*_STEP_SAVE, cached=True)
		aligned_files = []
		for sample_id in self._common_samples:
			aligned_file = self._aligned_output_path(sample_id)
			if os.path.exists(aligned_file):
				aligned_samples[sample_id] = aligned_file
				if not is_target_image:
					aligned_files.append(aligned_file)
		if aligned_files:
			merged_file = MODALITY_ALIGNMENT_MERGED(self._path, self._target_modality_name, "h5ad")
			active_ids = set(self._common_samples)
			merged_ids = read_merged_sample_ids(merged_file) if os.path.exists(merged_file) else None
			self._reporter.step(*_STEP_MERGE, cached=merged_ids == active_ids)
			if merged_ids != active_ids:
				alignment_folder = os.path.join(self._path, "merged", "alignment")
				os.makedirs(alignment_folder, exist_ok=True)
				concat_on_disk_compat(
					aligned_files, merged_file,
					merge="same", uns_merge="same"
				)
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
			# Unreachable through the CLI/GUI: config validation rejects an image-based
			# reference paired with a spot-based modality before the pipeline starts.
			self._reporter.warning(
				f"Aligning image reference '{self._target_modality_name}' to spot modality "
				f"'{self._reference_modality_name}' is not supported; no aligned output was written."
			)

		return aligned_samples
