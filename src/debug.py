import os
from preprocessing import preprocessing
from alignment import alignment
from registration import registration

from constants import MsiPreprocessingParams, MicroscopyImageProcessingParams, ModalityType
from constants import SegmentationBackgroundColor, RamanPreprocessingParams

if __name__ == "__main__":
    PATH = "/staging/leuven/stg_00077/projects/jelle/lipogrid/pilot/MALDI_MSI/d_lipogrid"
    HF_TOKEN = "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"

    PROCESSED_MODALITIES = [ModalityType.MSI]
    ALIGN = False
    REGISTER = False


    ############# PREPROCESSING #############

    # Define the MSI preprocessing settings
    if ModalityType.MSI in PROCESSED_MODALITIES:
        msi_modality_name = "MSI"
        msi_preprocessing_settings = {
            MsiPreprocessingParams.FORCE_RECOMPUTING: True,
            MsiPreprocessingParams.FREQUENCY_THRESHOLD: 0.01,
            MsiPreprocessingParams.INTENSITY_NORMALIZATION: preprocessing.MsiIntensityNormalization.TIC,
            MsiPreprocessingParams.LIPID_ANNOTATION_DB: os.path.join(PATH, "resources", "MSI_database_POS_NEG_combined.json"),
            MsiPreprocessingParams.MASS_TOLERANCE: 10,
            MsiPreprocessingParams.BATCH_SIZE: 10000,
        }

        # Preprocess MSI Dataset
        processed_msi = preprocessing.preprocess_modality(
            path=PATH,
            modality_type=ModalityType.MSI,
            modality_name=msi_modality_name,
            preprocessing_settings=msi_preprocessing_settings,
        )

    # Define the Microscopy preprocessing settings
    if ModalityType.MICROSCOPY_IMAGE in PROCESSED_MODALITIES:
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

        # Preprocess Microscopy Dataset
        processed_microscopy = preprocessing.preprocess_modality(
            path=PATH,
            modality_type=ModalityType.MICROSCOPY_IMAGE,
            modality_name=microscopy_modality_name,
            preprocessing_settings=microscopy_preprocessing_settings,
        )
    
    if ModalityType.RAMAN in PROCESSED_MODALITIES:
        raman_modality_name = "Raman"
        raman_processing_settings = {
            RamanPreprocessingParams.FORCE_RECOMPUTING: False,
        }

        # Preprocess Raman Dataset
        processed_raman = preprocessing.preprocess_modality(
            path=PATH,
            modality_type=ModalityType.RAMAN,
            modality_name=raman_modality_name,
            preprocessing_settings=raman_processing_settings,
        )

    ############# ALIGNMENT  #############

    if ALIGN:
        # Perform Direct Mapping Alignment
        aligner = alignment.DirectMappingAligner(
            path=PATH,
            reference_modality=processed_microscopy,
            target_modality=processed_msi,
            target_modality_name=msi_modality_name,
            reference_modality_name=microscopy_modality_name,
        )

        aligned_samples = aligner.align_dataset()

    ############# REGISTRATION #############
    
    if REGISTER:
        # Perform Registration
        registrar = registration.FeatureExtractorRegistration(
            path=PATH,
            hf_token=HF_TOKEN
        )

        reference_modality = {
            microscopy_modality_name: processed_microscopy
        }

        reference_modality_type = {
            microscopy_modality_name: ModalityType.MICROSCOPY_IMAGE
        }

        registered_samples = registrar.register_dataset(
            reference_modality=reference_modality,
            target_modality=aligned_samples,
            reference_modality_type=reference_modality_type,
            target_modality_name=msi_modality_name,
            force_recomputing=False
        )




