import constants as constants

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