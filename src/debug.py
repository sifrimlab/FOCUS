import os
from preprocessing import preprocessing
from alignment import alignment
from registration import registration

from constants import MsiPreprocessingParams, MicroscopyImageProcessingParams, ModalityType
from constants import SegmentationBackgroundColor, RamanPreprocessingParams, STPreprocessingParams

if __name__ == "__main__":
    PATH = "/staging/leuven/stg_00077/projects/jelle/lipogrid/pilot/MALDI_MSI/d_lipogrid"
    HF_TOKEN = "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"

    PROCESSED_MODALITIES = [ModalityType.MSI, ModalityType.ST]
    ALIGN = True
    REGISTER = True

    REGISTRATION_MODE = "SpotInterpolation"


    ############# PREPROCESSING #############

    # Define the MSI preprocessing settings
    msi_modality_name = "MSI"
    msi_modality_type = ModalityType.MSI
    msi_preprocessing_settings = {
        MsiPreprocessingParams.FORCE_RECOMPUTING: False,
        MsiPreprocessingParams.FREQUENCY_THRESHOLD: 0.01,
        MsiPreprocessingParams.INTENSITY_NORMALIZATION: preprocessing.MsiIntensityNormalization.TIC,
        MsiPreprocessingParams.LIPID_ANNOTATION_DB: os.path.join(PATH, "resources", "MSI_database_POS_NEG_combined.json"),
        MsiPreprocessingParams.MASS_TOLERANCE: 10,
        MsiPreprocessingParams.RECALIBRATION_REFERENCE: None,
        MsiPreprocessingParams.MIN_INTENSITY_THRESHOLD: 1e4
    }

    # Preprocess MSI Dataset
    processed_msi = preprocessing.preprocess_modality(
        path=PATH,
        modality_type=ModalityType.MSI,
        modality_name=msi_modality_name,
        preprocessing_settings=msi_preprocessing_settings,
    )

    # Define ST preprocessing settings
    st_modality_name = "Xenium"
    st_modality_type = ModalityType.ST
    preprocessing_settings = {
        STPreprocessingParams.MIN_COUNT_PER_SPOT: None,
        STPreprocessingParams.MAX_COUNT_PER_SPOT: None,
        STPreprocessingParams.MIN_GENES_PER_SPOT: None,
        STPreprocessingParams.MAX_GENES_PER_SPOT: None,
        STPreprocessingParams.MIN_SPOTS_PER_GENE: None,
        STPreprocessingParams.MIN_COUNT_SPOTS_RATIO_PER_GENE: None,
        STPreprocessingParams.TOTAL_COUNTS_NORMALIZE: True,
        STPreprocessingParams.LOG1P_TRANSFORM: True,
        STPreprocessingParams.FORCE_RECOMPUTING: False 
    }

    # Preprocess ST Dataset
    processed_st = preprocessing.preprocess_modality(
        path=PATH,
        modality_type=st_modality_type,
        modality_name=st_modality_name,
        preprocessing_settings=preprocessing_settings,
    )

    ############# ALIGNMENT  #############

    if ALIGN:
        # Perform Direct Mapping Alignment
        aligner = alignment.DirectMappingAligner(
            path=PATH,
            reference_modality=processed_msi,
            target_modality=processed_st,
            reference_modality_name=msi_modality_name,
            target_modality_name=st_modality_name,
            reference_modality_type=msi_modality_type,
            target_modality_type=st_modality_type,
        )

        aligned_samples = aligner.align_dataset(force_recomputing=False)

    ############# REGISTRATION #############
    
    if REGISTER:
        if REGISTRATION_MODE == "FeatureExtractor":
            # Perform Registration
            registrar = registration.FeatureExtractorRegistration(
                path=PATH,
                hf_token=HF_TOKEN
            )

            reference_modality = {
                st_modality_name: processed_st
            }

            reference_modality_type = {
                st_modality_name: ModalityType.ST
            }

            registered_samples = registrar.register_dataset(
                reference_modality=reference_modality,
                target_modality=aligned_samples,
                reference_modality_type=reference_modality_type,
                target_modality_name=msi_modality_name,
                force_recomputing=False
            )
        elif REGISTRATION_MODE == "SpotInterpolation":
            # Perform Registration
            registrar = registration.SpotInterpolationRegistration(
                path=PATH,
                nearest_neighbors=4,
                max_distance=None
            )

            reference_modality = {
                msi_modality_name: processed_msi
            }

            reference_modality_type = {
                msi_modality_name: ModalityType.MSI
            }

            registered_samples = registrar.register_dataset(
                reference_modality=reference_modality,
                target_modality=aligned_samples,
                reference_modality_type=reference_modality_type,
                target_modality_name=st_modality_name,
                force_recomputing=False
            )


