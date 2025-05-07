import os, tifffile
import numpy as np
import matplotlib.pyplot as plt

from sklearn.decomposition import PCA

from preprocessing.microscopy_image import gamma_correction, enhance_contrast
from constants import ModalityParameters, ModalityType

from alignment.elastix_engine import ElastixEngine
from GUI.landmark_selection import LandmarkSelectionGUI

from skimage.transform import downscale_local_mean      #TODO: Remove this import

class Aligner:
    '''
    This class is used to align the target modality to the anchor modality using Elastix.
    The target modality is the moving image and the anchor modality is the fixed image.
    The output of this class is a Boolean mask that indicates the region of the anchor covered by the target.

    Parameters
    ----------
    path : str
        The path to the data source directory
    load_landmarks : bool, optional
        If True, load the landmarks from the file (default is False)
    load_alignment_transformation : bool, optional
        If True, load the alignment transformation from the file (default is False)
    '''

    def __init__(self, path: str, load_landmarks: bool = False, load_alignment_transformation: bool = False) -> None:
        
        # TODO: Implement a way to load landmarks and/or transformation from a file

        if type(path) != str or type(load_landmarks) != bool or type(load_alignment_transformation) != bool:
            raise TypeError("Invalid input type. Please check the input types.")

        self._path = path

        if load_landmarks == False:
            self._fixed_landmarks, self._moving_landmarks = [], []
            self._moving_image_xflip, self._moving_image_yflip = False, False
        
        if load_alignment_transformation == False:
            self._transformation_parameters = None

    def _get_landmarks_from_gui(self, fixed_landmarks: np.ndarray, moving_landmarks: np.ndarray, moving_image_xflip: bool = False, moving_image_yflip: bool = False) -> None:
        '''
        Callback function to get the landmarks from the GUI, once the user clicks to confirm.
        All the checks are performed in the GUI class.

        Parameters
        ----------
        fixed_landmarks : np.ndarray
            The fixed landmarks
        moving_landmarks : np.ndarray
            The moving landmarks
        moving_image_xflip : bool, optional
            If True, the moving image is flipped in the x direction (default is False)
        moving_image_yflip : bool, optional
            If True, the moving image is flipped in the y direction (default is False)
        '''

        if type(fixed_landmarks) != np.ndarray or type(moving_landmarks) != np.ndarray:
            raise TypeError("Invalid input type. Please check the input types.")
        
        self._fixed_landmarks = fixed_landmarks
        self._moving_landmarks = moving_landmarks
        self._moving_image_xflip = moving_image_xflip
        self._moving_image_yflip = moving_image_yflip

    def _generate_msi_image(self, path: str) -> np.ndarray[np.float32]:
        '''
        Generate an RGB image from the processed MSI data
        
        Parameters
        ----------
        path : str
            The path to the data source directory
        
        Returns
        ----------
        np.ndarray[np.float32]
            The generated RGB image
        '''

        # Check if there are the processed MSI data
        if not os.path.exists(os.path.join(path, "processed")):
            raise FileNotFoundError(f"The path {path} does not exist or do not contain processed data. Please check the input values.")
        
        sample_id = path.split('/')[-2]

        # Load the required files
        intensities = np.load(os.path.join(path, "processed", f"{sample_id}_intensities.npy"))
        coordinates = np.load(os.path.join(path, "processed", f"{sample_id}_coordinates.npy"))

        # Compute a 3-dimensional PCA to generate an RGB-like image
        pca = PCA(n_components = 3)
        intensities = pca.fit_transform(intensities)
        intensities = np.array(intensities, dtype = np.float32)

        # Define the output image (consider h, w, c to meet matplotlib requirements)
        image_shape = (np.max(coordinates[:, 1]) - np.min(coordinates[:, 1]) + 1, np.max(coordinates[:, 0]) - np.min(coordinates[:, 0]) + 1, 3)
        output = np.zeros(image_shape, dtype = np.float32)

        # Normalize the intensities between 0 and 1 for each channel
        intensities[:, 0] = (intensities[:, 0] - np.min(intensities[:, 0])) / (np.max(intensities[:, 0]) - np.min(intensities[:, 0]))
        intensities[:, 1] = (intensities[:, 1] - np.min(intensities[:, 1])) / (np.max(intensities[:, 1]) - np.min(intensities[:, 1]))
        intensities[:, 2] = (intensities[:, 2] - np.min(intensities[:, 2])) / (np.max(intensities[:, 2]) - np.min(intensities[:, 2]))

        # Apply gamma correction to each channel
        intensities[:, 0] = gamma_correction(intensities[:, 0], gamma = 0.8)
        intensities[:, 1] = gamma_correction(intensities[:, 1], gamma = 0.8)
        intensities[:, 2] = gamma_correction(intensities[:, 2], gamma = 0.8)

        # Apply contrast enhancement to each channel
        intensities[:, 0] = enhance_contrast(intensities[:, 0])
        intensities[:, 1] = enhance_contrast(intensities[:, 1])
        intensities[:, 2] = enhance_contrast(intensities[:, 2])

        # Create the image
        for index, (x, y) in enumerate(coordinates):
            output[y - 1, x - 1, :] = intensities[index]        #NOTE: The indexes are inverted to meet the matplotlib requirements

        return output

    def _read_microscopy_image(self, path: str) -> np.ndarray[np.float32]:
        '''
        Read the microscopy image from the processed data

        Parameters
        ----------
        path : str
            The path to the data source directory

        Returns
        ----------
        np.ndarray[np.float32]
            The microscopy image
        '''


        # Check if there are the processed MSI data
        if not os.path.exists(os.path.join(path, "processed")):
            raise FileNotFoundError(f"The path {path} does not exist or do not contain processed data. Please check the input values.")
        
        sample_id = path.split('/')[-2]

        output = tifffile.imread(os.path.join(path, "processed", f"{sample_id}_processed.tiff"))
        return output
        
    def align_modality_to_anchor(self, target_modality: dict, anchor_modality: dict, target_spacing: tuple[float, float], anchor_spacing: tuple[float, float]) -> None:
        '''
        Align the target modality (moving image) to the anchor modality (fixed image) using Elastix.
        This method must be used after the preprocessing steps because it relies on those artifacts.
        The output of this method is a Boolean mask that indicates the region of the anchor covered by the target.

        Parameters
        ----------
        target_modality : dict
            The target modality settings from the configuration file
        anchor_modality : dict
            The anchor modality settings from the configuration file
        target_spacing : tuple[float, float]
            The target spacing in µm
        anchor_spacing : tuple[float, float]
            The anchor spacing in µm
        '''

        if type(target_modality) != dict or type(anchor_modality) != dict or type(target_spacing) != tuple or type(anchor_spacing) != tuple:
            raise TypeError("Invalid input types. Please check the input types.")
        if len(target_spacing) != 2 or len(anchor_spacing) != 2:
            raise ValueError("Invalid input values. Please check the input values.")
        if target_modality[ModalityParameters.MODALITY_NAME] == anchor_modality[ModalityParameters.MODALITY_NAME]:
            raise ValueError("The target and anchor modalities must be different. Please check the input values.")
        
        # Check the type of both modalities, if they are not images, convert them to images
        if target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
            target_image = self._read_microscopy_image(os.path.join(self._path, target_modality[ModalityParameters.MODALITY_NAME]))
        elif target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
            target_image = self._generate_msi_image(os.path.join(self._path, target_modality[ModalityParameters.MODALITY_NAME]))
        else:
            raise ValueError("Invalid target modality type. Please check the input values.")
        
        if anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
            anchor_image = self._read_microscopy_image(os.path.join(self._path, anchor_modality[ModalityParameters.MODALITY_NAME]))
        elif anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
            anchor_image = self._generate_msi_image(os.path.join(self._path, anchor_modality[ModalityParameters.MODALITY_NAME]))
        else:
            raise ValueError("Invalid anchor modality type. Please check the input values.")

        # Verify the presence of landmarks. If missing, request the user to pick them from the GUI
        if len(self._fixed_landmarks) == 0 or len(self._moving_landmarks) == 0:
            print("Landmarks not found. Please select them through the GUI.")

            # Create the GUI and wait for the user to select them
            landmarks_gui = LandmarkSelectionGUI(
               fixed_image = anchor_image,
                moving_image = target_image,
                save_landmarks_callback = self._get_landmarks_from_gui,
            )

            landmarks_gui.enable_gui()

        # Check if the moving image has to be flipped
        if self._moving_image_xflip:
            target_image = np.flip(target_image, axis = 1)
        if self._moving_image_yflip:
            target_image = np.flip(target_image, axis = 0)

        # Create the Elastix engine
        engine = ElastixEngine(
            path = os.path.join(self._path, "alignment", target_modality[ModalityParameters.MODALITY_NAME]),
            fixed_image = anchor_image,
            moving_image = target_image,
            fixed_spacing = anchor_spacing,
            moving_spacing = target_spacing
        )

        # Scaling offset compared to the metadata used
        aligned_image, scaling_offset = engine.align_images(
            transformations = ["rigid"],
            fixed_points = self._fixed_landmarks,
            moving_points = self._moving_landmarks,
        )

        #TMP: PRodurre immagine per Jelle

        # Get a boolean mask of the aligned image
        aligned_mask = np.zeros((aligned_image.shape[0:2]), dtype = np.bool_)
        aligned_mask[np.max(aligned_image, axis=2) >= 0] = True

        indexes = np.argwhere(aligned_mask)
        row_start, col_start = indexes.min(axis=0)
        row_end, col_end = indexes.max(axis=0) + 1

        # Cut the anchor image based on the aligned mask
        cut_anchor_image = np.zeros((row_end - row_start, col_end - col_start, anchor_image.shape[2]), dtype = np.float32)
        cut_anchor_image = anchor_image[row_start:row_end, col_start:col_end]

        # Compute the scaling factor
        scaling_factor = (
            int((cut_anchor_image.shape[0] / target_image.shape[0]) * 1),
            int((cut_anchor_image.shape[1] / target_image.shape[1]) * 1),
            1
        )

        # Downscale the cut anchor image to the target image size
        resulting_image = downscale_local_mean(cut_anchor_image, scaling_factor)

        # Save the resulting image
        output_path = os.path.join(self._path, "alignment", target_modality[ModalityParameters.MODALITY_NAME], "resulting_image.tiff")
        if not os.path.exists(os.path.dirname(output_path)):
            os.makedirs(os.path.dirname(output_path))
        tifffile.imwrite(output_path, resulting_image)


