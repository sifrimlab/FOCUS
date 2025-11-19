import os, sys
import preprocessing.microscopy_image as mi
import preprocessing.lipidomics as lipidomics
import preprocessing.raman as raman
import preprocessing.transcriptomic as transcriptomic

from constants import MicroscopyImageProcessingParams, ModalityType, FocusOutputDirectories, SegmentationBackgroundColor
from constants import MsiPreprocessingParams, MsiIntensityNormalization, MsiIonMode, RamanPreprocessingParams
from constants import STPreprocessingParams

def preprocess_modality(path: str, modality_name: str, modality_type: str, preprocessing_settings: dict) -> dict[str, str]:
    '''
    Apply preprocessing steps to a given modality based on its type and settings.
    This method is an entry point for the preprocessing pipeline.
    All the samples in a given modality will be processed, producing output for each one and a combined output if applicable.

    Parameters:
    ----------
        path: str
            The path to the directory where the source data are stored.
        modality_name: str
            The name of the modality being processed.
        modality_type: str 
            The type of the modality (e.g., 'microscopy_image', 'msi', 'raman').
        preprocessing_settings: dict 
            A dictionary containing the preprocessing settings for the modality.
        hf_token: str
            The Hugging Face token for accessing private models or datasets if needed.

    Returns:
    -------
        dict[str, str]
            A dictionary with keys as sample identifiers and values as paths to the preprocessed data.
    '''

    # Check if the modality type is supported
    if modality_type not in ModalityType.list():
        raise ValueError(f"Unsupported modality type: {modality_type}")
    
    # Check if the input path exists and is accessible
    if not os.path.exists(path):
        raise FileNotFoundError(f"The specified path does not exist: {path}")
    if not os.access(path, os.R_OK):
        raise PermissionError(f"The specified path is not accessible: {path}")
    
    # Get a list of sample IDs from the specified path (assuming each subdirectory is a sample)
    sample_ids = [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    sample_ids.sort()

    # Remove from the sample IDs the standard output directories if they exists
    for output_dir in FocusOutputDirectories.list():
        if output_dir in sample_ids:
            sample_ids.remove(output_dir)

    # For each sample_id, create the folders required for preprocessing outputs
    for sample_id in sample_ids:
        os.makedirs(os.path.join(path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name), exist_ok=True)

    # Create the directory for the merged dataset if needed
    os.makedirs(os.path.join(path, FocusOutputDirectories.MERGED, FocusOutputDirectories.PREPROCESSING, modality_name), exist_ok=True)

    # Create a dataset using the appropriate modality type
    if modality_type == ModalityType.MICROSCOPY_IMAGE:
        color_enhancement = preprocessing_settings.get(MicroscopyImageProcessingParams.COLOR_ENHANCEMENT, True)
        remove_background = preprocessing_settings.get(MicroscopyImageProcessingParams.REMOVE_BACKGROUND, True)
        crop_to_tissue = preprocessing_settings.get(MicroscopyImageProcessingParams.CROP_TO_TISSUE, True)
        background_color = preprocessing_settings.get(MicroscopyImageProcessingParams.BACKGROUND_COLOR, SegmentationBackgroundColor.WHITE)
        pyramid_levels = preprocessing_settings.get(MicroscopyImageProcessingParams.PYRAMID_LEVELS, 4)
        min_object_coverage = preprocessing_settings.get(MicroscopyImageProcessingParams.MIN_OBJECT_COVERAGE, 0.01)
        force_recomputing = preprocessing_settings.get(MicroscopyImageProcessingParams.FORCE_RECOMPUTING, False)

        # Define the sample list
        samples: list[mi.MicroscopyImage] = [
            mi.MicroscopyImage(
                source_path=path,
                sample_id=sample_id,
                modality_name=modality_name
            ) for sample_id in sample_ids
        ]

        # Define the dataset object
        dataset = mi.MicroscopyImageDataset(path=path, samples=samples)

        # Process the dataset
        processed_samples = dataset.process_dataset(
            color_enhancement=color_enhancement,
            remove_background=remove_background,
            crop_to_tissue=crop_to_tissue,
            background_color=background_color,
            pyramid_levels=pyramid_levels,
            min_object_coverage=min_object_coverage,
            force_recomputing=force_recomputing
        )

    elif modality_type == ModalityType.MSI:
        lipid_annotation_db = preprocessing_settings.get(MsiPreprocessingParams.LIPID_ANNOTATION_DB, None)
        mass_tolerance = preprocessing_settings.get(MsiPreprocessingParams.MASS_TOLERANCE, 10)
        frequency_threshold = preprocessing_settings.get(MsiPreprocessingParams.FREQUENCY_THRESHOLD, 0.01)
        intensity_normalization = preprocessing_settings.get(MsiPreprocessingParams.INTENSITY_NORMALIZATION, MsiIntensityNormalization.TIC)
        batch_size = preprocessing_settings.get(MsiPreprocessingParams.BATCH_SIZE, 10000)
        force_recomputing = preprocessing_settings.get(MsiPreprocessingParams.FORCE_RECOMPUTING, False)

        # Define the sample list
        samples: list[lipidomics.MsiSample] = []
        for sample_id in sample_ids:
            subdir = os.listdir(os.path.join(path, sample_id, modality_name))
            ion_modes = 0

            for mode in MsiIonMode.list():
                if mode in subdir:
                    ion_modes += 1

            if ion_modes == 0:
                raise ValueError(f"No ion mode subdirectories found for sample {sample_id}. Expected at least one of: {MsiIonMode.list()}")
            elif ion_modes == 1:
                samples.append(
                    lipidomics.MsiSample(
                        source_path=path,
                        sample_id=sample_id,
                        modality_name=modality_name,
                        double_ion_mode=False,
                        ion_mode=MsiIonMode.POSITIVE if MsiIonMode.POSITIVE in subdir else MsiIonMode.NEGATIVE
                    )
                )
            else:
                samples.append(
                    lipidomics.MsiSample(
                        source_path=path,
                        sample_id=sample_id,
                        modality_name=modality_name,
                        double_ion_mode=True,
                    )
                )

        # Define the dataset object
        dataset = lipidomics.MsiDataset(path=path, samples=samples, lipid_annotation_db=lipid_annotation_db)

        # Process the dataset
        processed_samples = dataset.process_dataset(
            mass_tolerance=mass_tolerance,
            frequency_threshold=frequency_threshold,
            intensity_normalization=intensity_normalization,
            batch_size=batch_size,
            force_recomputing=force_recomputing
        )

    elif modality_type == ModalityType.RAMAN:
        force_recomputing = preprocessing_settings.get(RamanPreprocessingParams.FORCE_RECOMPUTING, False)

        # Define the sample list
        samples: list[raman.RamanImage] = [
            raman.RamanImage(
                source_path=path,
                sample_id=sample_id,
                modality_name=modality_name
            ) for sample_id in sample_ids
        ]

        # Define the dataset object
        dataset = raman.RamanDataset(path=path, samples=samples)

        # Process the dataset
        processed_samples = dataset.process_dataset(
            force_recomputing=force_recomputing
        )
    
    elif  modality_type == ModalityType.ST:
        min_count_per_spot = preprocessing_settings.get(STPreprocessingParams.MIN_COUNT_PER_SPOT, 1000)
        max_count_per_spot = preprocessing_settings.get(STPreprocessingParams.MAX_COUNT_PER_SPOT, 40000)
        min_spots_per_gene = preprocessing_settings.get(STPreprocessingParams.MIN_SPOTS_PER_GENE, 0.02)
        total_counts_normalize = preprocessing_settings.get(STPreprocessingParams.TOTAL_COUNTS_NORMALIZE, True)
        log1p_transform = preprocessing_settings.get(STPreprocessingParams.LOG1P_TRANSFORM, True)
        force_recomputing = preprocessing_settings.get(STPreprocessingParams.FORCE_RECOMPUTING, False)

        # Define the sample list
        samples: list[transcriptomic.SpatialTranscriptomic] = [
            transcriptomic.SpatialTranscriptomic(
                source_path=path,
                sample_id=sample_id,
                modality_name=modality_name
            ) for sample_id in sample_ids
        ]

        # Define the dataset object
        dataset = transcriptomic.SpatialTranscriptomicDataset(path=path, samples=samples)

        # Process the dataset
        processed_samples = dataset.process_dataset(
            min_count_per_spot=min_count_per_spot,
            max_count_per_spot=max_count_per_spot,
            min_spots_per_gene=min_spots_per_gene,
            total_counts_normalize=total_counts_normalize,
            log1p_transform=log1p_transform,
            force_recomputing=force_recomputing
        )

    else:
        raise ValueError(f"Unsupported modality type: {modality_type}")
    
    return processed_samples