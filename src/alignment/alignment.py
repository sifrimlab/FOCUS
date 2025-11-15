import os, tifffile, threading, anndata, copy
import numpy as np

from GUI.direct_mapping_alignment import DirectMappingAlignmentGUI

class DirectMappingAligner:
    '''
    This class handle the alignment between two modalities using direct coordinate mapping.
    The reference modality is supposed to be a high resulution image while the target modality is a set of points that
    are mapped to the reference modality coordinate system.
    The reference modality is assumed to be a OME TIFF file, while the target modality is provided as AnnData.
    For each sample, an alignment is performed.

    Parameters
    ----------
    path : str
        The path to the dataset folder
    reference_modality : dict
        The reference modality files from the processing module
    target_modality : dict
        The target modality files from the processing module
    force_recompute : bool, optional
        If True, forces the recomputation of the alignment even if the alignment file already exists (default is False)
    '''

    def __init__(self,
            path: str,
            reference_modality: dict,
            target_modality: dict,
            reference_modality_name: str,
            target_modality_name: str
        ) -> None:
        if type(path) != str or type(reference_modality) != dict or type(target_modality) != dict:
            raise TypeError("Invalid input types. Please check the input types.")
    
        if type(reference_modality_name) != str or type(target_modality_name) != str:   
            raise TypeError("Invalid input types. Please check the input types.")

        self._path = path
        self._reference_modality = reference_modality
        self._target_modality = target_modality
        self._reference_modality_name = reference_modality_name
        self._target_modality_name = target_modality_name

        # Only align samples that are present in both modalities
        reference_samples = set(self._reference_modality.keys())
        target_samples = set(self._target_modality.keys())
        self._common_samples = list(reference_samples.intersection(target_samples))

        # Define an event to signal when all the samples are processed
        self._dataset_completed_event = threading.Event()

        # Define a dictionary to store the aligned results
        self._aligned_coordinates: dict[str, np.ndarray] = {}

        # Define the GUI interface
        self._gui_interface = DirectMappingAlignmentGUI(samples=self._common_samples, dataset_completed_event=self._dataset_completed_event)

    def _load_ome_tiff(self, filename: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        '''
        Load an OME TIFF file and return the image data, the pixel size and the origin.

        Parameters
        ----------
        filename : str
            The path to the OME TIFF file

        Returns
        -------
        tuple[np.ndarray, np.ndarray, np.ndarray]
            A tuple containing:
            - The image data from the lowest pyramidal resolution
            - The shape of the lowest resolution level
            - The shape of the original resolution level
        '''

        if type(filename) != str:
            raise TypeError("Invalid input type. Please check the input type.")

        if not os.path.exists(filename):
            raise FileNotFoundError(f"The specified file does not exist: {filename}")

        # Read the lowest resolution level of the OME TIFF file
        with tifffile.TiffFile(filename) as tif:
            lowest_level = tif.series[-1]
            image_data = lowest_level.asarray()
            lowest_shape = lowest_level.shape
            original_shape = tif.series[0].shape

        # Convert the image to Uint8 if necessary
        if image_data.dtype != np.uint8:
            image_data = (image_data * 255).astype(np.uint8)

        # Check if the channel dim is the first or the last (the smallest should be the channel dim)
        if np.argmin(image_data.shape) == 0:
            image_data = np.transpose(image_data, (1, 2, 0))  # HWC format

        # If the image data is hyperdimensional, convert to RGB by applying NMF with three factors
        if image_data.ndim == 3 and image_data.shape[-1] > 3:
            #from sklearn.decomposition import NMF

            #n_channels = image_data.shape[-1]
            #reshaped_image = image_data.reshape(-1, n_channels)  # Shape (num_pixels, n_channels)

            #nmf_model = NMF(n_components=3, init='random', random_state=0)
            #W = nmf_model.fit_transform(reshaped_image)  # Shape (num_pixels, 3)
            #H = nmf_model.components_  # Shape (3, n_channels)

            #rgb_image = W.reshape(lowest_shape[0], lowest_shape[1], 3)
            #rgb_image = (rgb_image / np.max(rgb_image) * 255).astype(np.uint8)
            #image_data = rgb_image

            image_data = image_data[..., 32:32+3]

        return image_data, lowest_shape, original_shape
    
    def _load_anndata_coordinates(self, filename: str) -> tuple[np.ndarray, np.ndarray]:
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
        if 'spatial' not in adata.obsm:
            raise ValueError("The AnnData file does not contain spatial coordinates in obsm['spatial'].")

        coordinates = adata.obsm['spatial']
        raster_size = adata.uns['raster_size'] if 'raster_size' in adata.uns else np.array([1.0, 1.0], dtype=np.float32)
        return coordinates, raster_size

    def _align_dataset_thread(self, **kwargs) -> None:
        force_recomputing = kwargs.get("force_recomputing", False)

        # Process each sample
        for sample_id in self._common_samples:

            # Check if there are cached results
            if force_recomputing == False:
                alignment_folder = os.path.join(self._path, sample_id, "alignment")
                aligned_target_file = os.path.join(alignment_folder, f"{sample_id}_processed_aligned.h5ad")

                if os.path.exists(aligned_target_file) == True:
                    # If the file exits, check if there is the registration we are computing
                    adata = anndata.read_h5ad(aligned_target_file)
                    if f'{self._reference_modality_name}_spatial' in adata.obsm_keys():
                        continue

            reference_file = self._reference_modality[sample_id]
            target_file = self._target_modality[sample_id]

            # Load reference image
            reference_image, lowest_shape, original_shape = self._load_ome_tiff(reference_file)

            # Load target coordinates
            target_coordinates, raster_size = self._load_anndata_coordinates(target_file)
    
            # Launch the GUI for alignment
            aligned_coordinates = self._gui_interface.align_sample(
                sample_id=sample_id,
                reference_image=reference_image,
                target_coordinates=target_coordinates,
                raster_size=raster_size
            )

            # The aligned coordinates refers to the lowest resolution level, we need to scale them to the original resolution
            scale_factors = np.array([original_shape[0] / lowest_shape[0], original_shape[1] / lowest_shape[1]], dtype=np.float32)
            aligned_coordinates = aligned_coordinates * scale_factors

            self._aligned_coordinates[sample_id] = copy.deepcopy(aligned_coordinates)

        # Set the dataset completed event to disable the GUI
        self._dataset_completed_event.set()

    def align_dataset(self, force_recomputing: bool = False) -> dict[str, str]:
        '''
        Align the target modality to the reference modality for all common samples.
        This method enables the Alignment GUI and starts the alignment process in a separate thread.
        Once the alignment is completed, it saves the aligned coordinates back to the AnnData files
        and generates a merged aligned dataset.

        Parameters
        ----------
        force_recomputing: bool
            If True, force the re-alignment of all the samples even if there are cached results
            If False, skip the re-alignment for samples with cached results.

        Returns
        -------
        aligned_samples : dict[str, str]
            A dictionary where keys are sample IDs (including "merged") and values are the paths to the aligned AnnData files.
        '''

        aligned_samples: dict[str, str] = {}

        # Start the alignment process in a separate thread
        alignment_thread = threading.Thread(
            name = "Align Dataset Thread",
            target=self._align_dataset_thread,
            kwargs={"force_recomputing": force_recomputing},
            daemon=True
        )
        alignment_thread.start()

        # Enable the GUI (this will block until the GUI is closed)
        self._gui_interface.enable_gui()

        # For each aligned sample, load the AnnData file and store the aligned coordinates
        for sample_id, aligned_coords in self._aligned_coordinates.items():
            processed_target_file = self._target_modality[sample_id]

            alignment_folder = os.path.join(self._path, sample_id, "alignment")
            os.makedirs(alignment_folder, exist_ok=True)
            aligned_target_file = os.path.join(alignment_folder, f"{sample_id}_processed_aligned.h5ad")
            
            # If no aligned file exits load it from the processing step
            if os.path.exists(aligned_target_file) == False:
                adata = anndata.read_h5ad(processed_target_file)
            else:
                adata = anndata.read_h5ad(aligned_target_file)

            # Save the aligned coordinates
            adata.obsm[f'{self._reference_modality_name}_spatial'] = aligned_coords
            
            adata.write_h5ad(aligned_target_file)

        # Load all the aligned AnnData involved in the dataset to generate a final aligned dataset
        adata_list: list[anndata.AnnData] = []
        for sample_id in self._common_samples:
            alignment_folder = os.path.join(self._path, sample_id, "alignment")
            aligned_target_file = os.path.join(alignment_folder, f"{sample_id}_processed_aligned.h5ad")
            adata_list.append(
                anndata.read_h5ad(aligned_target_file)
            )

            # Save this file for the result
            aligned_samples[sample_id] = aligned_target_file

        # Generate the merged aligned dataset
        merged_aligned_adata = anndata.concat(adata_list, axis=0)
        alignment_folder = os.path.join(self._path, "merged", "alignment")
        os.makedirs(alignment_folder, exist_ok=True)
        merged_aligned_file = os.path.join(alignment_folder, f"{self._target_modality_name}_merged_processed_aligned.h5ad")
        merged_aligned_adata.write_h5ad(merged_aligned_file)
        aligned_samples["merged"] = merged_aligned_file

        return aligned_samples