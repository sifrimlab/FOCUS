import os, tifffile
import numpy as np
import matplotlib.pyplot as plt

from sklearn.decomposition import PCA, NMF

from utils import gamma_correction, enhance_contrast
from constants import ModalityParameters, ModalityType, AlignmentSettings, TransformationType, RegistrationSettings, RegistrationType, DecompositionMethod

from alignment.elastix_engine import ElastixEngine
from GUI.landmark_selection import LandmarkSelectionGUI

import cv2      #TODO: Remove this import
from scipy.ndimage import affine_transform

class Aligner:
    '''
    This class is used to align the target modality to the anchor modality using Elastix.
    The target modality is the moving image and the anchor modality is the fixed image.
    The output of this class is a Boolean mask that indicates the region of the anchor covered by the target.

    Parameters
    ----------
    source_folder : str
        The source_folder to the data source directory
    sample_id : str
        The sample_id of the data source
    load_landmarks : bool, optional
        If True, load the landmarks from the file (default is False)
    load_alignment_transformation : bool, optional
        If True, load the alignment transformation from the file (default is False)
    '''

    def __init__(self, source_folder: str, sample_id: str, load_landmarks: bool = False, load_alignment_transformation: bool = False) -> None:
        
        # TODO: Implement a way to load landmarks and/or transformation from a file

        if type(source_folder) != str or type(sample_id) != str or type(load_landmarks) != bool or type(load_alignment_transformation) != bool:
            raise TypeError("Invalid input type. Please check the input types.")

        self._source_folder = source_folder
        self._sample_id = sample_id
        self._preprocessing_folder = os.path.join(self._source_folder, self._sample_id, "preprocessing")
        self._alignment_folder = os.path.join(self._source_folder, self._sample_id, "alignment")

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

    def _generate_msi_image(self, path: str, decomposition_method: DecompositionMethod = DecompositionMethod.PCA) -> np.ndarray[np.float32]:
        '''
        Generate an RGB image from the processed MSI data
        
        Parameters
        ----------
        path : str
            The path to the preprocessed MSI data
        decomposition_method : DecompositionMethod
            The decomposition method to use to generate the RGB image (default is PCA)
        
        Returns
        ----------
        np.ndarray[np.float32]
            The generated RGB image
        '''

        # Check if there are the processed MSI data
        if not os.path.exists(os.path.join(path)):
            raise FileNotFoundError(f"The path {path} does not exist or do not contain processed data. Please check the input values.")
        

        # Load the required files
        intensities: np.ndarray = np.load(os.path.join(path, f"{self._sample_id}_intensities_matrix.npy"))
        matrix_shape = intensities.shape

        # Reshape the intensities to a 2D array
        intensities = intensities.reshape(-1, intensities.shape[-1])

        # Create a mask to remove empty pixels
        empty_mask = np.all(intensities == 0, axis = 1)
        intensities = intensities[~empty_mask]

        # Compute a 3-dimensional embedding to generate an RGB-like image
        if decomposition_method == DecompositionMethod.PCA:
            engine = PCA(n_components = 3, svd_solver = 'randomized', random_state = 0)
        elif decomposition_method == DecompositionMethod.NMF:
            engine = NMF(n_components = 3, init = 'random', random_state = 0, max_iter = 1000)
        else:
            raise ValueError("Invalid decomposition method. Please check the input values.")
        
        intensities = engine.fit_transform(intensities)

        # Normalize the intensities between 0 and 1 for each channel - Visualization purpose only
        intensities[:, 0] = (intensities[:, 0] - np.min(intensities[:, 0])) / (np.max(intensities[:, 0]) - np.min(intensities[:, 0]))
        intensities[:, 1] = (intensities[:, 1] - np.min(intensities[:, 1])) / (np.max(intensities[:, 1]) - np.min(intensities[:, 1]))
        intensities[:, 2] = (intensities[:, 2] - np.min(intensities[:, 2])) / (np.max(intensities[:, 2]) - np.min(intensities[:, 2]))

        # Reconstruct the original intensities array
        reconstructed_intensities = np.zeros((matrix_shape[0] * matrix_shape[1], 3))
        reconstructed_intensities[~empty_mask] = intensities
        reconstructed_intensities = reconstructed_intensities.reshape(matrix_shape[0], matrix_shape[1], 3)

        # Swap X and Y axes to meet matplotlib requirements
        reconstructed_intensities = np.swapaxes(reconstructed_intensities, 0, 1)

        return reconstructed_intensities.astype(np.float32)

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
        if not os.path.exists(os.path.join(path)):
            raise FileNotFoundError(f"The path {path} does not exist or do not contain processed data. Please check the input values.")
        
        output = tifffile.imread(os.path.join(path, f"{self._sample_id}.tiff"))
        return output
        
    def align_modality_to_anchor(self, target_modality: dict, anchor_modality: dict) -> None:
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
        '''

        if type(target_modality) != dict or type(anchor_modality) != dict:
            raise TypeError("Invalid input types. Please check the input types.")
        if target_modality[ModalityParameters.MODALITY_NAME] == anchor_modality[ModalityParameters.MODALITY_NAME]:
            raise ValueError("The target and anchor modalities must be different. Please check the input values.")
        
        # Check the type of both modalities, if they are not images, convert them to images
        if target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
            target_image = self._read_microscopy_image(os.path.join(self._preprocessing_folder, target_modality[ModalityParameters.MODALITY_NAME]))
        elif target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
            target_image = self._generate_msi_image(os.path.join(self._preprocessing_folder, target_modality[ModalityParameters.MODALITY_NAME]))
        else:
            raise ValueError("Invalid target modality type. Please check the input values.")
        
        if anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
            anchor_image = self._read_microscopy_image(os.path.join(self._preprocessing_folder, anchor_modality[ModalityParameters.MODALITY_NAME]))
        elif anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
            anchor_image = self._generate_msi_image(os.path.join(self._preprocessing_folder, anchor_modality[ModalityParameters.MODALITY_NAME]))
        else:
            raise ValueError("Invalid anchor modality type. Please check the input values.")

        # Verify the presence of landmarks. If missing, request the user to pick them from the GUI
        if len(self._fixed_landmarks) == 0 or len(self._moving_landmarks) == 0:
            print("Landmarks not found. Loading the GUI to allow hand-picking...")

            # Create the GUI and wait for the user to select them
            landmarks_gui = LandmarkSelectionGUI(
               fixed_image = anchor_image,
                moving_image = target_image,
                save_landmarks_callback = self._get_landmarks_from_gui,
                image_size_cap = None # TODO: Correct the bugs to use this feature
            )

            print("Please select the landmarks opening this link in your browser: http://localhost:5000/")
            landmarks_gui.enable_gui()

        # Check if the moving image has to be flipped
        if self._moving_image_xflip:
            target_image = np.flip(target_image, axis = 1)
        if self._moving_image_yflip:
            target_image = np.flip(target_image, axis = 0)

        # Create the Elastix engine
        engine = ElastixEngine(
            path = os.path.join(self._alignment_folder, target_modality[ModalityParameters.MODALITY_NAME]),
            fixed_image = anchor_image,
            moving_image = target_image
        )

        # Scaling offset compared to the metadata used
        aligned_image, scaling_offset, output_parameters = engine.align_images(
            transformations = target_modality[ModalityParameters.ALIGNMENT_SETTINGS][AlignmentSettings.TRANSFORMATIONS],
            fixed_points = self._fixed_landmarks,
            moving_points = self._moving_landmarks,
        )



        #TODO: Remove this part and move it to registration component
        if target_modality[ModalityParameters.REGISTRATION_SETTINGS][RegistrationSettings.TYPE] == RegistrationType.RESOLUTION_SCALING_TO_TARGET:
            # Get a boolean mask of the aligned image
            aligned_mask = np.zeros((aligned_image.shape[0:2]), dtype = np.bool_)
            aligned_mask[np.max(aligned_image, axis=2) >= 0] = True

            cpy = np.copy(anchor_image)
            cpy[aligned_mask == False] = 0

            a = engine.invert_transformation(cpy, output_parameters[0])

            aligned_mask = np.zeros((a.shape[0:2]), dtype = np.bool_)
            aligned_mask[np.max(a, axis=2) > 0] = True

            indexes = np.argwhere(aligned_mask)
            row_start, col_start = indexes.min(axis=0)
            row_end, col_end = indexes.max(axis=0) + 1

            # Cut the anchor image based on the aligned mask
            cut_anchor_image = np.zeros((row_end - row_start, col_end - col_start, anchor_image.shape[2]), dtype = np.float32)
            cut_anchor_image = a[row_start:row_end, col_start:col_end]

            # Compute the scaling factor
            scaling_factor = (
                target_image.shape[1],
                target_image.shape[0]
            )

            # Downscale the cut anchor image to the target image size
            resulting_image = cv2.resize(cut_anchor_image, scaling_factor, interpolation = cv2.INTER_CUBIC)

            # Normalize the resulting image taking into accont that there are negative values
            resulting_image = (resulting_image - np.min(resulting_image)) / (np.max(resulting_image) - np.min(resulting_image))
            resulting_image = np.clip(resulting_image, 0, 1)


            # Save the resulting image
            output_path = os.path.join(self._alignment_folder, anchor_modality[ModalityParameters.MODALITY_NAME], "resulting_image.tiff")
            if not os.path.exists(os.path.dirname(output_path)):
                os.makedirs(os.path.dirname(output_path))
            tifffile.imwrite(output_path, resulting_image)
        elif target_modality[ModalityParameters.REGISTRATION_SETTINGS][RegistrationSettings.TYPE] == RegistrationType.RESOLUTION_SCALING_TO_ANCHOR:
            # Save the aligned image as a tiff file
            output_path = os.path.join(self._alignment_folder, target_modality[ModalityParameters.MODALITY_NAME], "resulting_image.tiff")
            if not os.path.exists(os.path.dirname(output_path)):
                os.makedirs(os.path.dirname(output_path))
            tifffile.imwrite(output_path, aligned_image)

