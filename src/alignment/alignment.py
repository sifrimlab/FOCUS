import os, tifffile
import numpy as np
import matplotlib.pyplot as plt

from sklearn.decomposition import PCA

from src.preprocessing.microscopy_image import gamma_correction, enhance_contrast
from src.constants import ModalityParameters, ModalityType

def generate_msi_image(path: str) -> np.ndarray[np.float32]:
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

def read_microscopy_image(path: str) -> np.ndarray[np.float32]:
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

    output = tifffile.imread(os.path.join(path, "processed", f"{sample_id}_processed.tiff"), dtype = np.float32)
    return output
    
def align_modality_to_anchor(path: str, target_modality: dict, anchor_modality: dict, target_spacing: tuple[float, float], anchor_spacing: tuple[float, float]) -> None:
    '''
    Align the target modality (moving image) to the anchor modality (fixed image) using Elastix.
    This method must be used after the preprocessing steps because it relies on those artifacts.
    The output of this method is a Boolean mask that indicates the region of the anchor covered by the target.

    Parameters
    ----------
    path : str
        The path to the data source directory
    target_modality : dict
        The target modality settings from the configuration file
    anchor_modality : dict
        The anchor modality settings from the configuration file
    target_spacing : tuple[float, float]
        The target spacing in µm
    anchor_spacing : tuple[float, float]
        The anchor spacing in µm
    '''

    if type(path) != str or type(target_modality) != dict or type(anchor_modality) != dict or type(target_spacing) != tuple or type(anchor_spacing) != tuple:
        raise TypeError("Invalid input types. Please check the input types.")
    if len(target_spacing) != 2 or len(anchor_spacing) != 2:
        raise ValueError("Invalid input values. Please check the input values.")
    if target_modality[ModalityParameters.MODALITY_NAME] == anchor_modality[ModalityParameters.MODALITY_NAME]:
        raise ValueError("The target and anchor modalities must be different. Please check the input values.")
    
    # Check the type of both modalities, if they are not images, convert them to images
    if target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
        target_image = read_microscopy_image(os.path.join(path, target_modality[ModalityParameters.MODALITY_NAME]))
    elif target_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
        target_image = generate_msi_image(os.path.join(path, target_modality[ModalityParameters.MODALITY_NAME]))
    else:
        raise ValueError("Invalid target modality type. Please check the input values.")
    
    if anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MICROSCOPY_IMAGE:
        anchor_image = read_microscopy_image(os.path.join(path, anchor_modality[ModalityParameters.MODALITY_NAME]))
    elif anchor_modality[ModalityParameters.MODALITY_TYPE] == ModalityType.MSI:
        anchor_image = generate_msi_image(os.path.join(path, anchor_modality[ModalityParameters.MODALITY_NAME]))
    else:
        raise ValueError("Invalid anchor modality type. Please check the input values.")
    
    plt.figure()
    plt.imshow(target_image)

    plt.figure()
    plt.imshow(anchor_image)