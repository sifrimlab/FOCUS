import numpy as np
import tifffile, os
import matplotlib.pyplot as plt
from skimage.transform import downscale_local_mean


from sklearn.decomposition import PCA

from .registration import Registration

def enhance_contrast(channel: np.ndarray, saturated_pixels: float = 0.35) -> np.ndarray:
    '''
    Enhance the contrast of a single channel image by stretching the histogram.
    Add a small amount of saturated pixels to improve the contrast.

    Parameters
    ----------
    channel : np.ndarray[np.uint8]
        The channel to enhance.
    saturated_pixels : float
        The amount of saturated pixels to add. Default is 0.35%.
    '''

    # Convert to float32
    channel = channel.astype(np.float32)

    mask = channel > 0
    result = np.zeros_like(channel, dtype=np.float32)

    if np.any(mask):
        # Compute the pixels to saturate
        p_low, p_high = np.percentile(channel[mask], (saturated_pixels, 100 - saturated_pixels))

        # Stretch the histogram
        rescaled_channel = np.clip(channel[mask], p_low, p_high)

        result[mask] = (rescaled_channel - p_low) / (p_high - p_low)

    return result

def gamma_correction(channel: np.ndarray, gamma: float = 0.45) -> np.ndarray:
    '''
    Apply gamma correction to a single channel image.

    Parameters
    ----------
    image : np.ndarray[np.uint8]
        The image to correct.
    gamma : float
        The gamma value to use. Default is 0.45.
    '''

    channel = channel.astype(np.float32)
    channel = np.power(channel, gamma)
    return channel


def generate_maldi_image(maldi_data: np.ndarray, row2grid: np.ndarray) -> np.ndarray:

    # Compute PCA to reduce the dimensionality to 3 channels
    pca = PCA(n_components = 3)
    maldi_data = pca.fit_transform(maldi_data)

    # Define an RGB image
    image_shape = (np.max(row2grid[:, 0]) - np.min(row2grid[:, 0]) + 1, np.max(row2grid[:, 1]) - np.min(row2grid[:, 1]) + 1, 3)
    image = np.zeros(image_shape, dtype = np.uint8)

    # Convert the input into Float32
    maldi_data = np.array(maldi_data, dtype = np.float32)

    # Normalize the input between 0 and 1 for each channel
    maldi_data[:, 0] = (maldi_data[:, 0] - np.min(maldi_data[:, 0])) / (np.max(maldi_data[:, 0]) - np.min(maldi_data[:, 0]))
    maldi_data[:, 1] = (maldi_data[:, 1] - np.min(maldi_data[:, 1])) / (np.max(maldi_data[:, 1]) - np.min(maldi_data[:, 1]))
    maldi_data[:, 2] = (maldi_data[:, 2] - np.min(maldi_data[:, 2])) / (np.max(maldi_data[:, 2]) - np.min(maldi_data[:, 2]))

    # Apply gamma correction to each channel
    maldi_data[:, 0] = gamma_correction(maldi_data[:, 0], gamma = 0.8)
    maldi_data[:, 1] = gamma_correction(maldi_data[:, 1], gamma = 0.8)
    maldi_data[:, 2] = gamma_correction(maldi_data[:, 2], gamma = 0.8)

    # Apply contrast enhancement to each channel
    maldi_data[:, 0] = enhance_contrast(maldi_data[:, 0])
    maldi_data[:, 1] = enhance_contrast(maldi_data[:, 1])
    maldi_data[:, 2] = enhance_contrast(maldi_data[:, 2])

    # Rescale the input to 0-255
    maldi_data = (maldi_data * 255).astype(np.uint8)

    # Create the image
    for index, (x, y) in enumerate(row2grid):
        image[x - 1, y - 1, :] = maldi_data[index]

    # Matplotlib switch h and w so we need to swap axes
    image = image.swapaxes(0, 1)

    return image
        
class RegistrationMaldiToHE(Registration):
    
    def __init__(self, maldi: np.ndarray, he: np.ndarray, spacing_maldi: np.ndarray, spacing_he: np.ndarray, path: str) -> None:
        
        super().__init__(he, maldi, spacing_he, spacing_maldi, path)

def register_maldi_to_he(path, sample, spacing_he, spacing_maldi, invert_maldi: bool=False):
    
    # Load the MALDI data and compute an RGB image (PCA)
    maldi_data = np.load(f"{path}/{sample}/maldi/{sample}_intensities.npy")
    maldi_row2grid = np.load(f"{path}/{sample}/maldi/{sample}_coordinates.npy")
    maldi_image = generate_maldi_image(maldi_data, maldi_row2grid)
    
    # Load the cropped Microscopy image
    he_image = tifffile.imread(f'{path}/{sample}/h&e/{sample}_crop.tiff')

    # Perform the registration
    reg = RegistrationMaldiToHE(maldi_image, he_image, spacing_maldi, spacing_he, f"{path}/registration/{sample}/maldi")
    reg.set_error_measure('AdvancedMattesMutualInformation')
    registered_maldi_mask = reg.compute_transformation(only_affine=True, only_fiducials=False)

    scaling_factor = (int(spacing_maldi[0] / spacing_he[0]), int(spacing_maldi[1] / spacing_he[1]), 1)

    #TODO: Consider that the pixels are uint8, so they cannot be -1 and if they are 0 there will be empty spots
    aligned_cropped_image = np.copy(he_image)
    registered_maldi_mask = np.where(registered_maldi_mask > 0, True, False)
    indexes = np.argwhere(registered_maldi_mask)
    row_start, col_start = indexes.min(axis=0)
    row_end, col_end = indexes.max(axis=0) + 1

    # Exaclty align the image sizes
    #row_end = int(row_start + maldi_image.shape[0] * scaling_factor[0])
    #col_end = int(col_start + maldi_image.shape[1] * scaling_factor[1])

    aligned_cropped_image = he_image[row_start:row_end, col_start:col_end]

    #plt.figure()
    #plt.imshow(aligned_cropped_image)

    resulting_image = downscale_local_mean(aligned_cropped_image, scaling_factor)
    resulting_image = np.clip(resulting_image, 0, 255).astype(np.uint8)

    plt.figure()
    plt.imshow(resulting_image)

    np.save(f"{path}/{sample}/h&e/cofocal_registered.npy", resulting_image)
    
    #np.save(f"{path}/{sample}/maldi/maldi_coordinates.npy", maldi_coordinates_he_space)
    
    #return maldi_coordinates_he_space
    return resulting_image
    
    