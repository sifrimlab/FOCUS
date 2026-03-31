import os, json, argparse

import utils
from focus.constants import ConfigParameters, ModalityParameters, MODALITY_PREPROCESSING
from focus.preprocessing import preprocess_modality
from focus.preprocessing._utils import discover_sample_ids
from focus.alignment.alignment import DirectMappingAligner

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

    # Get the anchor modality that will be used as a reference for the alignment
    anchor_modality = config[ConfigParameters.ANCHOR_MODALITY]
    anchor_settings = None

    # Store preprocessed file paths per modality: {modality_name: {sample_id: file_path}}
    modality_files = {}

    print("STEP 1: Preprocessing of the input modalities")
    if config[ConfigParameters.PERFORM_PREPROCESSING] == True:
        # Apply preprocessing to the input modalities
        for modality in config[ConfigParameters.MODALITIES]:
            modality_name = modality[ModalityParameters.MODALITY_NAME]
            print(f"Preprocessing {modality_name}")

            modality_files[modality_name] = preprocess_modality(
                path = data_source_path,
                modality_name = modality_name,
                modality_type = modality[ModalityParameters.MODALITY_TYPE],
                preprocessing_settings = modality[ModalityParameters.PREPROCESSING_SETTINGS]
            )

            # Save the anchor settings for the alignment step
            if anchor_settings is None and modality_name == anchor_modality:
                anchor_settings = modality
    else:
        print("Skipping preprocessing step as per config.")

        # Reconstruct preprocessed file paths from disk
        sample_ids = discover_sample_ids(data_source_path)
        for modality in config[ConfigParameters.MODALITIES]:
            modality_name = modality[ModalityParameters.MODALITY_NAME]
            modality_type = modality[ModalityParameters.MODALITY_TYPE]

            # Determine file extension based on modality type
            file_type = "ome.tiff" if modality_type in ["microscopy_image", "raman"] else "h5ad"

            modality_files[modality_name] = {
                sid: MODALITY_PREPROCESSING(data_source_path, sid, modality_name, file_type)
                for sid in sample_ids
            }

            if anchor_settings is None and modality_name == anchor_modality:
                anchor_settings = modality

    print(f"STEP 2: Alignment of the input modalities")

    # Align each modality to the anchor modality
    for modality in config[ConfigParameters.MODALITIES]:
        modality_name = modality[ModalityParameters.MODALITY_NAME]
        if modality_name == anchor_modality:
            continue

        print(f"Aligning {modality_name} to {anchor_modality}")

        alignment_engine = DirectMappingAligner(
            path = data_source_path,
            reference_modality = modality_files[anchor_modality],
            target_modality = modality_files[modality_name],
            reference_modality_name = anchor_modality,
            target_modality_name = modality_name,
            reference_modality_type = anchor_settings[ModalityParameters.MODALITY_TYPE],
            target_modality_type = modality[ModalityParameters.MODALITY_TYPE]
        )

        aligned_files = alignment_engine.align_dataset()
        print(f"Alignment complete for {modality_name}: {len(aligned_files)} files produced")
