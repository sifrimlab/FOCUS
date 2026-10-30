import logging

import anndata
import numpy as np
import scipy.sparse

from focus.constants import RegistrationType, SegmentationBackgroundColor
from focus.utils import ome_tiff_pyramid_dims, load_ome_tiff_level, read_ome_physical_size_um
from focus.registration._cache import register_samples, merge_registered_samples, resolve_um_per_px
from focus.registration._footprint import footprint_membership, row_normalize
from focus.registration._readout_plan import select_encoding_level
from focus.reporting import get_reporter

logger = logging.getLogger(__name__)


def _dense(matrix) -> np.ndarray:
	return np.asarray(matrix.todense()) if scipy.sparse.issparse(matrix) else np.asarray(matrix)


class FeatureExtractorRegistration:
	"""
	Register a microscopy image to the anchor with one image embedding per anchor spot.

	Each anchor spot's footprint (its ``spot_size`` in the anchor frame, shaped by the anchor
	lattice) is mapped into the image through the alignment transform. The image is read at
	the stored pyramid level closest to the encoder's 0.5 µm per pixel and encoded by
	Prov-GigaPath in windows; every spot gets the CLS embedding of a query that attends only
	to the window tokens whose centres lie inside its footprint (see
	``MicroscopyImageFeatureExtractor.extract_spot_features``).

	Parameters
	----------
	path : str
		The path to the dataset folder.
	hf_token : str, optional
		HuggingFace token for downloading pretrained models.
	"""

	_REGISTRATION_TYPE = RegistrationType.FEATURE_EXTRACTION

	def __init__(self, path: str, hf_token: str = None) -> None:
		self._path = path
		self._hf_token = hf_token

	def register_dataset(
		self,
		image_files: dict[str, str],
		anchor_files: dict[str, str],
		image_name: str,
		anchor_name: str,
		anchor_type: str | None = None,
		force_recomputing: bool = False,
		background_color: SegmentationBackgroundColor | None = None,
		step_reporter=None,
	) -> dict[str, str]:
		"""
		Extract one embedding per anchor spot from the image modality.

		Parameters
		----------
		image_files : dict[str, str]
			Preprocessed image files. {sample_id: ome_tiff_path}
		anchor_files : dict[str, str]
			Aligned anchor AnnData files containing obsm['spatial'] (anchor frame) and
			obsm['{image_name}_spatial'] (the same spots in level-0 image pixels).
			{sample_id: h5ad_path}
		image_name : str
			Name of the image modality.
		anchor_name : str
			Name of the anchor modality.
		anchor_type : str, optional
			Modality type of the anchor. With an MSI anchor (coordinates in µm) the image's pixel
			size can be derived from the alignment when the image carries none.
		force_recomputing : bool
			Recompute even when the stored fingerprint matches.
		background_color : SegmentationBackgroundColor, optional
			Fill color of non-tissue pixels (default white).
		step_reporter : StepReporter, optional
			If provided, reports per-sample and per-spot progress to the GUI.

		Returns
		-------
		dict[str, str]
			{sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
		"""
		# Imported here so the spot and Raman engines (which share this module) never load torch.
		from focus.registration.microscopy_image import MicroscopyImageFeatureExtractor

		common_samples = sorted(set(image_files.keys()) & set(anchor_files.keys()) - {"merged"})
		reporter = step_reporter or get_reporter()
		background_color = background_color or SegmentationBackgroundColor.WHITE
		extractor = None   # loaded only when a sample actually needs encoding

		def settings(sample_id):
			size = read_ome_physical_size_um(image_files[sample_id])
			return {
				"background_color": str(background_color),
				"model": MicroscopyImageFeatureExtractor.MODEL_ID,
				"anchor_type": str(anchor_type),
				"physical_size_um": None if size is None else [float(v) for v in size],
			}

		def compute(sample_id, anchor, geom):
			nonlocal extractor
			image_path = image_files[sample_id]
			um0 = resolve_um_per_px(image_path, geom, anchor_type, reporter)
			level = select_encoding_level(um0, ome_tiff_pyramid_dims(image_path))
			if level.um_per_px is not None:
				reporter.detail(f"Encoding pyramid level {level.level} ({level.um_per_px:.3g} µm/px)")

			if extractor is None:
				reporter.step(1, 3, "Loading feature extractor")
				extractor = MicroscopyImageFeatureExtractor(path=self._path, hf_token=self._hf_token)

			image = load_ome_tiff_level(image_path, level.level)
			embeddings = extractor.extract_spot_features(
				image, anchor.mapped, geom, level, background_color=background_color, step_reporter=reporter,
			)
			del image
			adata = anndata.AnnData(
				X=embeddings,
				obsm={'spatial': anchor.mapped.copy()},
				obs={'sample_id': [sample_id] * anchor.n_obs},
			)
			extra = {"encoder": MicroscopyImageFeatureExtractor.MODEL_ID, "encoded_level": int(level.level)}
			if level.um_per_px is not None:
				extra["encoded_um_per_px"] = float(level.um_per_px)
			logger.debug(f"Saved {anchor.n_obs} spot embeddings for sample '{sample_id}'")
			return adata, extra

		registered_files = register_samples(
			path=self._path, samples=reporter.samples(common_samples),
			anchor_files=anchor_files, target_files=image_files,
			anchor_name=anchor_name, target_name=image_name,
			registration_type=self._REGISTRATION_TYPE, settings=settings, compute=compute,
			force_recomputing=force_recomputing, reporter=reporter,
		)
		# Drop the pretrained model so its parameters become collectable; the caller
		# (orchestrator._run_registration) then empties the CUDA cache via release_memory.
		del extractor

		reporter.step(3, 3, "Merging samples")
		return merge_registered_samples(
			self._path, registered_files, image_name, self._REGISTRATION_TYPE, force_recomputing=force_recomputing,
		)


class SpotInterpolationRegistration:
	"""
	Register a spot-based modality to the anchor by Gaussian-weighted interpolation.

	For each anchor spot, the target spots whose centres lie inside the anchor's footprint are
	averaged with weights ``exp(-d^2 / (2 sigma^2))``, ``sigma = sqrt(sx * sy) / 2``. The
	footprint and the distance ``d`` live in the anchor frame, where ``spot_size`` is defined:
	target positions are mapped back through the inverse of the alignment transform.

	Coordinate convention (after alignment):
	- ``anchor_files`` contains the aligned reference AnnData: ``obsm['spatial']`` (anchor
	  frame) and ``obsm['{target_name}_spatial']`` (the same spots in the target frame).
	- ``target_files`` contains the preprocessed target AnnData; its ``obsm['spatial']`` is in
	  the target frame.

	Parameters
	----------
	path : str
		The path to the dataset folder.
	"""

	_REGISTRATION_TYPE = RegistrationType.SPOT_INTERPOLATION

	def __init__(self, path: str) -> None:
		self._path = path

	def register_dataset(
		self,
		anchor_files: dict[str, str],
		target_files: dict[str, str],
		anchor_name: str,
		target_name: str,
		force_recomputing: bool = False,
		step_reporter=None,
	) -> dict[str, str]:
		"""
		Register a spot-based target modality to the anchor using Gaussian interpolation.

		Parameters
		----------
		anchor_files : dict[str, str]
			Aligned anchor (reference) modality files. {sample_id: h5ad_path}
		target_files : dict[str, str]
			Preprocessed target (non-reference) modality files. {sample_id: h5ad_path}
		anchor_name : str
			Name of the anchor (reference) modality.
		target_name : str
			Name of the target modality being registered.
		force_recomputing : bool
			Recompute even when the stored fingerprint matches.
		step_reporter : StepReporter, optional
			If provided, reports per-sample progress to the GUI.

		Returns
		-------
		dict[str, str]
			{sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
		"""
		common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
		reporter = step_reporter or get_reporter()

		def compute(sample_id, anchor, geom):
			target_adata = anndata.read_h5ad(target_files[sample_id])
			target_coords = np.asarray(target_adata.obsm['spatial'], dtype=np.float64)
			weights = row_normalize(footprint_membership(geom, target_coords, weights="gaussian"))
			logger.debug(
				f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
				f"Target: {target_coords.shape[0]} spots, {target_adata.n_vars} features."
			)
			# The same operator interpolates .X and every layer (same spots, different payloads).
			adata = anndata.AnnData(
				X=_dense(weights @ target_adata.X).astype(np.float32),
				obsm={'spatial': anchor.mapped.copy()},
				obs={'sample_id': [sample_id] * anchor.n_obs},
				layers={k: _dense(weights @ v).astype(np.float32) for k, v in target_adata.layers.items()} or None,
			)
			# Carry over var metadata from target (gene names, m/z values, etc.)
			adata.var = target_adata.var.copy()
			n_empty = int((np.diff(weights.indptr) == 0).sum())
			if n_empty:
				logger.debug(f"{n_empty}/{anchor.n_obs} anchor spots had no target spots in their footprint")
			return adata, {}

		registered_files = register_samples(
			path=self._path,
			samples=reporter.tqdm(common_samples, 1, 2, "Interpolating features onto anchor spots", unit="sample"),
			anchor_files=anchor_files, target_files=target_files,
			anchor_name=anchor_name, target_name=target_name,
			registration_type=self._REGISTRATION_TYPE, settings={}, compute=compute,
			force_recomputing=force_recomputing, reporter=reporter,
		)
		reporter.step(2, 2, "Merging samples")
		return merge_registered_samples(
			self._path, registered_files, target_name, self._REGISTRATION_TYPE, force_recomputing=force_recomputing,
		)
