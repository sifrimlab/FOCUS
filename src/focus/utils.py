import numpy as np
import focus.constants as constants
import os
import multiprocessing

def available_cpus():
    try:
        # Linux: respects affinity (Slurm, cpuset cgroups, taskset)
        return len(os.sched_getaffinity(0))
    except AttributeError:
        # Non-Linux fallback
        return multiprocessing.cpu_count()

def parse_config(config: dict) -> None:
    """
    Parse the configuration dictionary and return a new dictionary with the parsed values.
    
    Args:
        config (dict): The configuration dictionary to parse.
        
    Returns:
        dict: A new dictionary with the parsed values.
    """
    
    if type(config) is not dict:
        raise TypeError("The config parameter must be a dictionary.")
    
    # Check that the config file contains the required keys
    for key in constants.ConfigParameters.list():
        if key not in config:
            raise KeyError(f"The config file is missing the required key: {key}")
        
    # Ensure that all the modalities are supported
    for modality in config[constants.ConfigParameters.MODALITIES]:
        if constants.ModalityParameters.MODALITY_NAME not in modality:
            raise KeyError(f"The modality is missing the required key: {constants.ModalityParameters.MODALITY_NAME}")
        if constants.ModalityParameters.MODALITY_TYPE not in modality:
            raise KeyError(f"The modality is missing the required key: {constants.ModalityParameters.MODALITY_TYPE}")
        if constants.ModalityParameters.PREPROCESSING_SETTINGS not in modality:
            raise KeyError(f"The modality is missing the required key: {constants.ModalityParameters.PREPROCESSING_SETTINGS}")

        if modality[constants.ModalityParameters.MODALITY_TYPE] not in constants.ModalityType.list():
            raise ValueError(f"Unsupported modality type: {modality[constants.ModalityParameters.MODALITY_TYPE]}")
        
    # Ensure that the anchor modality is defined
    anchor_modality = None
    for modality in config[constants.ConfigParameters.MODALITIES]:
        if modality[constants.ModalityParameters.MODALITY_NAME] == config[constants.ConfigParameters.ANCHOR_MODALITY]:
            anchor_modality = modality
            break
    if anchor_modality is None:
        raise ValueError(f"The anchor modality {config[constants.ConfigParameters.ANCHOR_MODALITY]} is not defined in the config file.")
    
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