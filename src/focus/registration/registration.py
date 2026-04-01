import os, logging, anndata, tifffile
import numpy as np
import scipy.sparse
from scipy.spatial import cKDTree
from sklearn.preprocessing import MinMaxScaler

from focus.constants import MODALITY_REGISTRATION, MODALITY_REGISTRATION_MERGED
from focus.constants import ModalityType

from focus.registration.microscopy_image import MicroscopyImageFeatureExtractor

logger = logging.getLogger(__name__)

_H5AD_COMPRESSION = "gzip"


class FeatureExtractorRegistration:
	"""
	Register an image modality to the anchor by extracting patch embeddings
	at each anchor spot location in image space.

	For each anchor spot, a patch is extracted from the image at the spot's
	position (expressed in image coordinates via alignment) and encoded into
	an embedding vector using a pretrained model.

	Currently supports ModalityType.MICROSCOPY_IMAGE as image source.

	Parameters
	----------
	path : str
		The path to the dataset folder.
	hf_token : str, optional
		HuggingFace token for downloading pretrained models.
	"""

	def __init__(self, path: str, hf_token: str = None) -> None:
		self._path = path
		self._hf_token = hf_token

	def _load_ome_tiff(self, filename: str) -> np.ndarray:
		"""Load an OME-TIFF image at full resolution, returning HWC float32 array."""
		if not os.path.exists(filename):
			raise FileNotFoundError(f"Image file not found: {filename}")

		with tifffile.TiffFile(filename) as tif:
			image_data = tif.asarray()

		# Ensure HWC format (channel dim is the smallest)
		if image_data.ndim == 3 and np.argmin(image_data.shape) == 0:
			image_data = np.transpose(image_data, (1, 2, 0))

		return image_data

	def register_dataset(
		self,
		image_files: dict[str, str],
		anchor_files: dict[str, str],
		image_name: str,
		anchor_name: str,
		min_max_rescale: bool = True,
		force_recomputing: bool = False,
		**kwargs,
	) -> dict[str, str]:
		"""
		Extract patch embeddings from image modality at anchor spot locations.

		Parameters
		----------
		image_files : dict[str, str]
			Preprocessed image files. {sample_id: ome_tiff_path}
		anchor_files : dict[str, str]
			Aligned anchor AnnData files containing obsm['{image_name}_spatial']
			which gives anchor spot positions in image coordinate space.
			{sample_id: h5ad_path}
		image_name : str
			Name of the image modality.
		anchor_name : str
			Name of the anchor modality.
		min_max_rescale : bool
			Whether to apply min-max rescaling across all samples.
		force_recomputing : bool
			Whether to recompute even if cached results exist.

		Returns
		-------
		dict[str, str]
			{sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
		"""
		common_samples = sorted(set(image_files.keys()) & set(anchor_files.keys()) - {"merged"})
		registered_files: dict[str, str] = {}

		# Determine modality type for feature extractor
		feature_extractor = MicroscopyImageFeatureExtractor(
			path=self._path,
			hf_token=self._hf_token
		)

		for sample_id in common_samples:
			logger.info(f"Registering '{image_name}' for sample '{sample_id}'")

			# Output path
			reg_dir = os.path.join(self._path, sample_id, "registration")
			os.makedirs(reg_dir, exist_ok=True)
			registered_file = MODALITY_REGISTRATION(self._path, sample_id, image_name, "h5ad")

			# Cache check
			if os.path.exists(registered_file) and not force_recomputing:
				logger.info(f"Using cached registration for sample '{sample_id}'")
				registered_files[sample_id] = registered_file
				continue

			# Load image
			image_data = self._load_ome_tiff(image_files[sample_id])

			# Load anchor spot coordinates in image space
			anchor_adata = anndata.read_h5ad(anchor_files[sample_id])
			coord_key = f'{image_name}_spatial'
			if coord_key in anchor_adata.obsm:
				patch_centers = np.asarray(anchor_adata.obsm[coord_key], dtype=np.float32)
			elif 'spatial' in anchor_adata.obsm:
				logger.warning(f"obsm['{coord_key}'] not found, falling back to obsm['spatial']")
				patch_centers = np.asarray(anchor_adata.obsm['spatial'], dtype=np.float32)
			else:
				logger.error(f"No spatial coordinates found for sample '{sample_id}', skipping.")
				continue

			# Extract features
			background_color = kwargs.get("background_color", None)
			patch_size = kwargs.get("patch_size", 224)
			patch_embeddings, center_coordinates = feature_extractor.extract_features(
				image=image_data,
				patch_centers=patch_centers,
				patch_size=patch_size,
				**({"background_color": background_color} if background_color is not None else {})
			)

			# Save registered AnnData
			adata = anndata.AnnData(
				X=patch_embeddings,
				obsm={'spatial': center_coordinates},
				obs={'sample_id': [sample_id] * center_coordinates.shape[0]}
			)
			adata.write_h5ad(registered_file, compression=_H5AD_COMPRESSION)
			registered_files[sample_id] = registered_file
			logger.debug(f"Saved {patch_embeddings.shape[0]} patch embeddings for sample '{sample_id}'")

		# Merge across samples
		registered_files = self._merge_samples(registered_files, image_name, min_max_rescale)
		return registered_files

	def _merge_samples(self, registered_files: dict[str, str], modality_name: str, min_max_rescale: bool) -> dict[str, str]:
		"""Merge per-sample registration files and optionally apply min-max rescaling."""
		sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
		if not sample_files:
			return registered_files

		merge_dir = os.path.join(self._path, "merged", "registration")
		os.makedirs(merge_dir, exist_ok=True)
		merged_file = MODALITY_REGISTRATION_MERGED(self._path, modality_name, "h5ad")

		logger.info(f"Merging registration files for '{modality_name}'")
		adata_list = []
		for sample_id, filepath in sample_files.items():
			adata = anndata.read_h5ad(filepath)
			adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
			adata_list.append(adata)

		merged = anndata.concat(adata_list)

		if min_max_rescale and merged.n_obs > 0:
			scaler = MinMaxScaler()
			merged.X = scaler.fit_transform(merged.X)

		merged.write_h5ad(merged_file, compression=_H5AD_COMPRESSION)
		registered_files["merged"] = merged_file
		return registered_files


class SpotInterpolationRegistration:
	"""
	Register a spot-based modality to the anchor by Gaussian-weighted interpolation.

	For each anchor spot (defined by its center and spot_size), finds all target
	modality spots whose aligned center falls within the anchor spot's area, then
	computes a Gaussian-weighted average of their feature vectors.

	Parameters
	----------
	path : str
		The path to the dataset folder.
	"""

	def __init__(self, path: str) -> None:
		self._path = path

	@staticmethod
	def _interpolate_features(
		anchor_coordinates: np.ndarray,
		anchor_spot_size: np.ndarray,
		target_coordinates: np.ndarray,
		target_features: np.ndarray,
	) -> np.ndarray:
		"""
		For each anchor spot, find all target spots within its area and compute
		a Gaussian-weighted average of their features.

		Parameters
		----------
		anchor_coordinates : np.ndarray
			(N_anchor, 2) coordinates of anchor spots [x, y].
		anchor_spot_size : np.ndarray
			(2,) spot dimensions [sx, sy] in micrometers.
		target_coordinates : np.ndarray
			(N_target, 2) aligned coordinates of target spots in anchor space [x, y].
		target_features : np.ndarray
			(N_target, D) feature matrix of the target modality.

		Returns
		-------
		np.ndarray
			(N_anchor, D) interpolated features at anchor spot positions.
		"""
		if scipy.sparse.issparse(target_features):
			target_features = np.asarray(target_features.todense())

		n_anchor = anchor_coordinates.shape[0]
		n_features = target_features.shape[1]
		result = np.zeros((n_anchor, n_features), dtype=np.float32)

		sx, sy = float(anchor_spot_size[0]), float(anchor_spot_size[1])
		half_sx, half_sy = sx / 2.0, sy / 2.0

		# Gaussian sigma: proportional to spot dimensions
		sigma = np.sqrt(sx * sy) / 2.0

		# Build spatial index on target coordinates for fast lookup
		tree = cKDTree(target_coordinates)

		# Search radius: diagonal of the spot rectangle
		search_radius = np.sqrt(half_sx ** 2 + half_sy ** 2)

		n_empty = 0
		for i in range(n_anchor):
			cx, cy = anchor_coordinates[i]

			# Find candidate target spots within search radius
			candidate_indices = tree.query_ball_point([cx, cy], r=search_radius)
			if not candidate_indices:
				n_empty += 1
				continue

			candidates = target_coordinates[candidate_indices]
			dx = candidates[:, 0] - cx
			dy = candidates[:, 1] - cy

			# Filter: keep only those strictly within the rectangular spot area
			within_mask = (np.abs(dx) <= half_sx) & (np.abs(dy) <= half_sy)
			valid_local = np.where(within_mask)[0]

			if len(valid_local) == 0:
				n_empty += 1
				continue

			valid_global = np.array(candidate_indices)[valid_local]
			valid_dx = dx[valid_local]
			valid_dy = dy[valid_local]

			# Gaussian weights based on distance from anchor spot center
			dist_sq = valid_dx ** 2 + valid_dy ** 2
			weights = np.exp(-dist_sq / (2.0 * sigma ** 2))
			weights_sum = weights.sum()
			if weights_sum > 0:
				weights /= weights_sum

			# Weighted average of target features
			result[i] = np.sum(target_features[valid_global] * weights[:, np.newaxis], axis=0)

		if n_empty > 0:
			logger.debug(f"{n_empty}/{n_anchor} anchor spots had no target spots within range")

		return result

	def register_dataset(
		self,
		anchor_files: dict[str, str],
		target_files: dict[str, str],
		anchor_name: str,
		target_name: str,
		min_max_rescale: bool = True,
		force_recomputing: bool = False,
	) -> dict[str, str]:
		"""
		Register a spot-based target modality to the anchor using Gaussian interpolation.

		For each anchor spot, finds aligned target spots within the anchor's spot_size
		area and computes a Gaussian-weighted average of their features.

		Parameters
		----------
		anchor_files : dict[str, str]
			Preprocessed anchor modality files (spot-based AnnData with obsm['spatial']
			and uns['spot_size']). {sample_id: h5ad_path}
		target_files : dict[str, str]
			Aligned target modality files (AnnData with obsm['{anchor_name}_spatial']
			containing target spot positions in anchor coordinate space, and X containing
			the feature matrix). {sample_id: h5ad_path}
		anchor_name : str
			Name of the anchor modality.
		target_name : str
			Name of the target modality being registered.
		min_max_rescale : bool
			Whether to apply global min-max rescaling across all samples.
		force_recomputing : bool
			Whether to recompute even if cached results exist.

		Returns
		-------
		dict[str, str]
			{sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
		"""
		common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
		registered_files: dict[str, str] = {}

		for sample_id in common_samples:
			logger.info(f"Registering '{target_name}' for sample '{sample_id}'")

			# Output path
			reg_dir = os.path.join(self._path, sample_id, "registration")
			os.makedirs(reg_dir, exist_ok=True)
			registered_file = MODALITY_REGISTRATION(self._path, sample_id, target_name, "h5ad")

			# Cache check
			if os.path.exists(registered_file) and not force_recomputing:
				logger.info(f"Using cached registration for sample '{sample_id}'")
				registered_files[sample_id] = registered_file
				continue

			# Load anchor modality: coordinates and spot_size
			anchor_adata = anndata.read_h5ad(anchor_files[sample_id])
			anchor_coords = np.asarray(anchor_adata.obsm['spatial'], dtype=np.float32)

			if 'spot_size' in anchor_adata.uns:
				spot_size = np.asarray(anchor_adata.uns['spot_size'], dtype=np.float32).flatten()
				if spot_size.size == 1:
					spot_size = np.array([float(spot_size[0]), float(spot_size[0])], dtype=np.float32)
			else:
				logger.warning(f"No spot_size in anchor for sample '{sample_id}', using default [1.0, 1.0]")
				spot_size = np.array([1.0, 1.0], dtype=np.float32)

			# Load aligned target modality: aligned coordinates and features
			target_adata = anndata.read_h5ad(target_files[sample_id])
			coord_key = f'{anchor_name}_spatial'
			if coord_key not in target_adata.obsm:
				logger.error(
					f"Target '{target_name}' sample '{sample_id}' missing obsm['{coord_key}']. "
					f"Ensure alignment was performed. Skipping."
				)
				continue

			target_coords = np.asarray(target_adata.obsm[coord_key], dtype=np.float32)
			target_features = target_adata.X
			if scipy.sparse.issparse(target_features):
				target_features = np.asarray(target_features.todense())

			logger.debug(
				f"Anchor: {anchor_coords.shape[0]} spots, spot_size={spot_size}. "
				f"Target: {target_coords.shape[0]} spots, {target_features.shape[1]} features."
			)

			# Perform Gaussian-weighted interpolation
			registered_features = self._interpolate_features(
				anchor_coordinates=anchor_coords,
				anchor_spot_size=spot_size,
				target_coordinates=target_coords,
				target_features=target_features,
			)

			# Build output AnnData at anchor positions
			adata = anndata.AnnData(
				X=registered_features,
				obsm={'spatial': anchor_coords.copy()},
				obs={'sample_id': [sample_id] * anchor_coords.shape[0]},
			)

			# Carry over var metadata from target if available
			if target_adata.var is not None and len(target_adata.var.columns) > 0:
				adata.var = target_adata.var.copy()
			if target_adata.var_names is not None and len(target_adata.var_names) == registered_features.shape[1]:
				adata.var_names = target_adata.var_names.tolist()

			adata.write_h5ad(registered_file, compression=_H5AD_COMPRESSION)
			registered_files[sample_id] = registered_file
			logger.debug(f"Saved registration for sample '{sample_id}': {registered_features.shape}")

			del anchor_adata, target_adata

		# Merge across samples
		registered_files = self._merge_samples(registered_files, target_name, min_max_rescale)
		return registered_files

	def _merge_samples(self, registered_files: dict[str, str], modality_name: str, min_max_rescale: bool) -> dict[str, str]:
		"""Merge per-sample registration files and optionally apply min-max rescaling."""
		sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
		if not sample_files:
			return registered_files

		merge_dir = os.path.join(self._path, "merged", "registration")
		os.makedirs(merge_dir, exist_ok=True)
		merged_file = MODALITY_REGISTRATION_MERGED(self._path, modality_name, "h5ad")

		logger.info(f"Merging registration files for '{modality_name}'")
		adata_list = []
		for sample_id, filepath in sample_files.items():
			adata = anndata.read_h5ad(filepath)
			adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
			adata_list.append(adata)

		merged = anndata.concat(adata_list)

		if min_max_rescale and merged.n_obs > 0:
			scaler = MinMaxScaler()
			merged.X = scaler.fit_transform(merged.X)

		merged.write_h5ad(merged_file, compression=_H5AD_COMPRESSION)
		registered_files["merged"] = merged_file
		return registered_files
