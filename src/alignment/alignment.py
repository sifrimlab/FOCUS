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

    def __init__(self, path: str, reference_modality: dict, target_modality: dict, force_recompute: bool = False) -> None:
        if type(path) != str or type(reference_modality) != dict or type(target_modality) != dict or type(force_recompute) != bool:
            raise TypeError("Invalid input types. Please check the input types.")

        self._path = path
        self._reference_modality = reference_modality
        self._target_modality = target_modality
        self._force_recompute = force_recompute

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

    def _align_dataset_thread(self) -> None:
        # Process each sample
        for sample_id in self._common_samples:

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

    def align_dataset(self) -> dict[str, np.ndarray]:
        '''
        Align the target modality to the reference modality for all common samples.

        Returns
        -------
        dict[str, np.ndarray]
            A dictionary with keys as sample identifiers and values as aligned coordinates as numpy arrays of shape (N, 2).
        '''


        # Start the alignment process in a separate thread
        alignment_thread = threading.Thread(target=self._align_dataset_thread, daemon=True)
        alignment_thread.start()

        # Enable the GUI (this will block until the GUI is closed)
        self._gui_interface.enable_gui()

        return self._aligned_coordinates