import os
from preprocessing import preprocessing
from alignment import alignment

from constants import MsiPreprocessingParams, MicroscopyImageProcessingParams, ModalityType
from constants import SegmentationBackgroundColor

if __name__ == "__main__":
    PATH = "/staging/leuven/stg_00077/projects/Lorenzo/FOCUS/p_PDA/d_C1"
    PLOT_PATH = os.path.join(PATH, "plots", "preprocessing", "MSI")

    # Define the MSI preprocessing settings
    msi_modality_name = "MSI"
    msi_preprocessing_settings = {
        MsiPreprocessingParams.FORCE_RECOMPUTING: False,
        MsiPreprocessingParams.FREQUENCY_THRESHOLD: 0.01,
        MsiPreprocessingParams.INTENSITY_NORMALIZATION: preprocessing.MsiIntensityNormalization.TIC,
        MsiPreprocessingParams.LIPID_ANNOTATION_DB: None,
        MsiPreprocessingParams.MASS_TOLERANCE: 10,
        MsiPreprocessingParams.BATCH_SIZE: 10000,
    }

    # Define the Microscopy preprocessing settings
    microscopy_modality_name = "HE"
    microscopy_preprocessing_settings = {
        MicroscopyImageProcessingParams.FORCE_RECOMPUTING: False,
        MicroscopyImageProcessingParams.BACKGROUND_COLOR: SegmentationBackgroundColor.WHITE,
        MicroscopyImageProcessingParams.CROP_TO_TISSUE: True,
        MicroscopyImageProcessingParams.MIN_OBJECT_COVERAGE: 0.0025,
        MicroscopyImageProcessingParams.PYRAMID_LEVELS: 4,
        MicroscopyImageProcessingParams.REMOVE_BACKGROUND: True,
        MicroscopyImageProcessingParams.COLOR_ENHANCEMENT: False,
    }

    # Preprocess MSI Dataset
    processed_msi = preprocessing.preprocess_modality(
        path=PATH,
        modality_type=ModalityType.MSI,
        modality_name=msi_modality_name,
        preprocessing_settings=msi_preprocessing_settings,
    )

    # Preprocess Microscopy Dataset
    processed_microscopy = preprocessing.preprocess_modality(
        path=PATH,
        modality_type=ModalityType.MICROSCOPY_IMAGE,
        modality_name=microscopy_modality_name,
        preprocessing_settings=microscopy_preprocessing_settings,
    )

    # Perform Direct Mapping Alignment
    aligner = alignment.DirectMappingAligner(
        path=PATH,
        reference_modality=processed_microscopy,
        target_modality=processed_msi,
        force_recompute=False,
        target_modality_name='MSI',
        reference_modality_name='HE',
    )

    aligned_samples = aligner.align_dataset()



