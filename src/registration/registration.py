import os, tifffile, anndata
import numpy as np
from sklearn.preprocessing import MinMaxScaler

from constants import MODALITY_PREPROCESSING, MODALITY_ALIGNMENT, MODALITY_ALIGNMENT_MERGED, MODALITY_REGISTRATION, MODALITY_REGISTRATION_MERGED
from constants import ModalityType

from registration.microscopy_image import MicroscopyImageFeatureExtractor

class FeatureExtractorRegistration:
	'''
	This class handle the registration step extracting features from the reference modality using
	the aligned coordinates from the target modality.

	Parameters
	----------
	path : str
		The path to the dataset folder
	hf_token : str, optional
		The HuggingFace token to use for downloading models (default is None)
	'''

	def __init__(self,
			path: str,
			hf_token: str = None
		) -> None:

		self._path = path
		self._hf_token = hf_token

	def _load_ome_tiff(self, filename: str) -> np.ndarray:
		'''
		Load an OME TIFF file and return the image data, the pixel size and the origin.

		Parameters
		----------
		filename : str
			The path to the OME TIFF file

		Returns
		-------
		image_data : np.ndarray
			The image data from the lowest pyramidal resolution
		'''

		if type(filename) != str:
			raise TypeError("Invalid input type. Please check the input type.")

		if not os.path.exists(filename):
			raise FileNotFoundError(f"The specified file does not exist: {filename}")

		# Read the lowest resolution level of the OME TIFF file
		with tifffile.TiffFile(filename) as tif:

			# Load the full resolution image
			image_data = tif.asarray()

		# Convert the image to Uint8 if necessary
		if image_data.dtype != np.uint8:
			image_data = (image_data * 255).astype(np.uint8)

		# Check if the channel dim is the first or the last (the smallest should be the channel dim)
		if np.argmin(image_data.shape) == 0:
			image_data = np.transpose(image_data, (1, 2, 0))  # HWC format

		return image_data
	
	def _load_anndata_coordinates(self, filename: str, reference_modality_name: str) -> np.ndarray:
		'''
		Load the spatial coordinates from an AnnData file.

		Parameters
		----------
		filename : str
			The path to the AnnData file

		Returns
		-------
		coordinates, raster_size: tuple[np.ndarray, np.ndarray]
			A tuple containing:
			- The spatial coordinates as a numpy array of shape (N, 2)
			- The raster size as a numpy array of shape (2,)
		'''

		if type(filename) != str:
			raise TypeError("Invalid input type. Please check the input type.")

		if not os.path.exists(filename):
			raise FileNotFoundError(f"The specified file does not exist: {filename}")

		adata = anndata.read_h5ad(filename)
		if f'{reference_modality_name}_spatial' not in adata.obsm:
			raise ValueError(f"The AnnData file does not contain spatial coordinates in obsm['{reference_modality_name}_spatial'].")

		coordinates = adata.obsm[f'{reference_modality_name}_spatial']
		return coordinates

	def register_dataset(
		self,
		reference_modality: dict[str, dict[str, str]],
		target_modality: dict[str, str],
		reference_modality_type: dict[str, ModalityType],
		target_modality_name: str,
		min_max_rescale: bool = True,
		force_recomputing: bool = False
	) -> dict[str, str]:
		'''
		Extract features from the reference modality using the aligned coordinates from the target modality.

		Parameters
		----------
		reference_modality : dict[str, dict[str, str]]
			The reference modality files from the processing module. Expected format:
			{reference_modality_name: {sample_id: file_path, ...}, ...}
		target_modality : dict[str, str]
			The target modality files from the registration module. Expected format:
			{sample_id: file_path, ...}
		reference_modality_type: dict[str, ModalityType]
			The names of the reference modalities. Expected format:
			{reference_modality_name: ModalityType, ...}
		target_modality_name : str
			The name of the target modality.
		min_max_rescale : bool, optional
			Whether to apply min-max rescaling to the extracted features (default is True)
		force_recomputing : bool, optional
			Whether to force recomputing the registration even if it already exists (default is False)

		Returns
		-------
		registered_samples : dict[str, dict[str, str]]
			The registered samples AnnData files. Expected format:
			{reference_modality_name: {sample_id: file_path, ...}, ...}
		'''

		registered_samples: dict[str, dict[str, str]] = {}

		# For each reference modality
		for reference_modality_name, samples in reference_modality.items():

			# Extract the modality type
			modality_type = reference_modality_type[reference_modality_name]
			if modality_type not in ModalityType.list():
				raise ValueError(f"Invalid modality type: {modality_type} for modality {reference_modality_name}")
			
			# Initiate the feature extractor according to the modality type
			if modality_type == ModalityType.MICROSCOPY_IMAGE:
				feature_extractor = MicroscopyImageFeatureExtractor(
					path=self._path,
					hf_token=self._hf_token
				)
			
			print(f"Registering modality '{reference_modality_name}' of type {modality_type} to target modality '{target_modality_name}'.")
			registered_samples[reference_modality_name] = {}

			# Register each sample
			for sample_id, reference_file in samples.items():

				print(f"Processing sample {sample_id}")

				# Check if the output directory exists, if not create it
				if os.path.exists(os.path.join(self._path, sample_id, "registration")) == False:
					os.makedirs(os.path.join(self._path, sample_id, "registration"))

				# Check if the registered file already exists
				registered_file = MODALITY_REGISTRATION(self._path, sample_id, reference_modality_name, "h5ad")
				if os.path.exists(registered_file) and not force_recomputing:
					print(f"Registered file already exists for sample '{sample_id}' and modality '{reference_modality_name}'. Using cached results.")
					registered_samples[reference_modality_name][sample_id] = registered_file
					continue

				# Check if there is a target modality file for this sample
				if sample_id not in target_modality:
					print(f"Warning: No target modality file found for sample '{sample_id}'. Extracting features from non aligned coordinates.")
					patch_centers = None
				else:
					target_file = target_modality[sample_id]
					patch_centers = self._load_anndata_coordinates(
						filename=target_file,
						reference_modality_name=reference_modality_name
					)

				# Load the reference modality data
				image_data = self._load_ome_tiff(reference_file)

				# Extract features using the aligned coordinates
				patch_embeddings, center_coordinates = feature_extractor.extract_features(
					image=image_data,
					patch_centers=patch_centers
				)

				# Save the extracted features and coordinates to an AnnData file
				adata = anndata.AnnData(
					X=patch_embeddings,
					obsm={f'spatial': center_coordinates},
					obs={'sample_id': sample_id}
				)
				adata.write_h5ad(registered_file)
				registered_samples[reference_modality_name][sample_id] = registered_file

		# Generate merged modality file
		if os.path.exists(os.path.join(self._path, 'merged', "registration")) == False:
			os.makedirs(os.path.join(self._path, 'merged', "registration"))

		for reference_modality_name in registered_samples.keys():
			merged_registered_file = MODALITY_REGISTRATION_MERGED(self._path, reference_modality_name, "h5ad")

			print(f"Generating merged registered file for modality '{reference_modality_name}'.")

			# List of AnnData objects to merge
			adata_list = []

			for sample_id, registered_file in registered_samples[reference_modality_name].items():
				adata = anndata.read_h5ad(registered_file)
				adata.obs_names = [f"{sample_id}_{idx}" for idx in range(adata.n_obs)]
				adata_list.append(adata)
				
			# Concatenate all AnnData objects
			merged_adata = anndata.concat(adata_list)

			# Apply global normalization of the embeddings
			if min_max_rescale:
				scaler = MinMaxScaler()
				merged_adata.X = scaler.fit_transform(merged_adata.X)

			merged_adata.write_h5ad(merged_registered_file)
			registered_samples[reference_modality_name]['merged'] = merged_registered_file

		return registered_samples

			



				