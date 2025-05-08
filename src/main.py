import os, json, argparse

import utils
from constants import ConfigParameters, ModalityParameters
import preprocessing.preprocessing as preprocessing
from alignment.alignment import Aligner

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='FOCUS: Flexible Multiomics data preprocessing and alignment pipeline.')
    parser.add_argument('-c', '--config', type = str, help='Absolute path of the config file', required = True)
    
    args = parser.parse_args()
    config_path = args.config
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"The config file {config_path} does not exist. Please check the input values.")
    
    # Load the config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Error decoding JSON: {e}")
    except Exception as e:
        raise Exception(f"An error occurred while loading the config file: {e}")
    
    print(f"Loaded config file: {config_path}")
    
    # Check the config
    utils.parse_config(config)

    data_source_path = config[ConfigParameters.DATA_SOURCE_PATH]
    sample_id =  config[ConfigParameters.SAMPLE_NAME]
    sample_folder = os.path.join(data_source_path, sample_id)

    # Get the anchor modality that will be used as a reference for the alignment
    anchor_modality = config[ConfigParameters.ANCHOR_MODALITY]
    anchor_settings = None

    # Store the spatial resoluotion for each modality to perform alignment
    spatial_resolution = {}

    if config[ConfigParameters.PERFORM_PREPROCESSING] == True:
        print("STEP 1: Preprocessing of the input modalities")

        # Apply preprocessing to the input modalities
        for modality in config[ConfigParameters.MODALITIES]:
            print(f"Preprocessing {modality[ModalityParameters.MODALITY_NAME]}")
            
            inferred_physical_size = preprocessing.preprocess_modality(
                path = data_source_path,
                sample_id = sample_id,
                modality_name = modality[ModalityParameters.MODALITY_NAME],
                modality_type = modality[ModalityParameters.MODALITY_TYPE],
                preprocessing_settings = modality[ModalityParameters.PREPROCESSING_SETTINGS]
            )

            # If the config file provides the physical pixel coverage, use it
            if ModalityParameters.PHYSICAL_PIXEL_COVERAGE in modality[ModalityParameters.PREPROCESSING_SETTINGS]:
                spatial_resolution[modality[ModalityParameters.MODALITY_NAME]] = tuple(modality[ModalityParameters.PREPROCESSING_SETTINGS][ModalityParameters.PHYSICAL_PIXEL_COVERAGE])
            else:
                spatial_resolution[modality[ModalityParameters.MODALITY_NAME]] = inferred_physical_size

            # Save the anchor settings for the alignment step
            if anchor_settings is None and modality[ModalityParameters.MODALITY_NAME] == anchor_modality:
                anchor_settings = modality
    else:
        print("Skipping preprocessing step as per config.")
        
        # Load the spatial resolution from the config file
        for modality in config[ConfigParameters.MODALITIES]:
            # If the config file provides the physical pixel coverage, use it
            if ModalityParameters.PHYSICAL_PIXEL_COVERAGE in modality[ModalityParameters.PREPROCESSING_SETTINGS]:
                spatial_resolution[modality[ModalityParameters.MODALITY_NAME]] = tuple(modality[ModalityParameters.PREPROCESSING_SETTINGS][ModalityParameters.PHYSICAL_PIXEL_COVERAGE])
            else:
                raise ValueError(f"The physical pixel coverage for {modality[ModalityParameters.MODALITY_NAME]} is not defined in the config file, but preprocessing is skipped.")
            
            if anchor_settings is None and modality[ModalityParameters.MODALITY_NAME] == anchor_modality:
                anchor_settings = modality

    print(f"STEP 2: Alignment of the input modalities")

    # Align each modality to the anchor modality
    for modality in config[ConfigParameters.MODALITIES]:
        if modality[ModalityParameters.MODALITY_NAME] == anchor_modality:
            continue
        
        print(f"Aligning {modality[ModalityParameters.MODALITY_NAME]} to {anchor_modality}")

        alignment_engine = Aligner(
            path = sample_folder,
            load_landmarks = False,
            load_alignment_transformation = False
        )
        
        alignment_engine.align_modality_to_anchor(
            target_modality = modality,
            anchor_modality = anchor_settings,
            target_spacing = spatial_resolution[modality[ModalityParameters.MODALITY_NAME]],
            anchor_spacing = spatial_resolution[anchor_modality]
        )