import constants as constants
import preprocessing.microscopy_image as microscopy_image
import preprocessing.lipidomics as lipidomics

def preprocess_modality(path: str, sample_id: str, modality_name: str, modality_type: str, preprocessing_settings: dict) -> tuple[float, float]:
    '''
    Apply preprocessing steps to a given modality based on its type and settings.
    This method is an entry point for the preprocessing pipeline and will
    save the preprocessed data to a file. Returns the physical pixel coverage in in µm.

    Parameters:
    ----------
        path: str
            The path to the directory where the source data are stored.
        sample_id: str
            The ID of the sample being processed.
        modality_name: str
            The name of the modality being processed.
        modality_type: str 
            The type of the modality (e.g., 'microscopy_image', 'msi', 'raman').
        preprocessing_settings: dict 
            A dictionary containing the preprocessing settings for the modality.

    Returns:
    ----------
        tuple[float, float]
            A tuple containing the physical pixel coverage in µm for the x and y dimensions.
    '''

    if modality_type == constants.ModalityType.MICROSCOPY_IMAGE:
        crop = preprocessing_settings.get(constants.ImagingPreprocessing.CROP, False)
        filter_strength = preprocessing_settings.get(constants.ImagingPreprocessing.FILTER_STRENGTH, constants.ImagingFilterStrength.SOFT)
        smoothing = preprocessing_settings.get(constants.ImagingPreprocessing.SMOOTHING, False)
        color_enhancement = preprocessing_settings.get(constants.ImagingPreprocessing.COLOR_ENHANCEMENT, False)

        physical_pixel_coverage = microscopy_image.preprocess_microscopy_image(
            path = path,
            sample_id = sample_id,
            modality_name = modality_name,
            crop = crop,
            filter_strength = filter_strength,
            smoothing = smoothing,
            color_enhancement = color_enhancement
        )
    elif modality_type == constants.ModalityType.MSI:
        peak_picking = preprocessing_settings.get(constants.LipidomicsPreprocessing.PEAK_PICKING, True)
        peak_prominence_threshold = preprocessing_settings.get(constants.LipidomicsPreprocessing.PEAK_PROMINENCE_THRESHOLD, 0.01)
        peak_window_tolerance_ppm = preprocessing_settings.get(constants.LipidomicsPreprocessing.PEAK_WINDOW_TOLERANCE_PPM, 20)
        dynamic_peak_window = preprocessing_settings.get(constants.LipidomicsPreprocessing.DYNAMIC_PEAK_WINDOW, True)
        dynamic_peak_window_factor = preprocessing_settings.get(constants.LipidomicsPreprocessing.DYNAMIC_PEAK_WINDOW_FACTOR, 1e6)
        
        physical_pixel_coverage = lipidomics.preprocess_lipidomics(
            path = path,
            sample_id = sample_id,
            modality_name = modality_name,
            peak_picking = peak_picking,
            prominence = peak_prominence_threshold,
            window_tolerance = peak_window_tolerance_ppm,
            dynamic_window = dynamic_peak_window,
            dynamic_window_factor = dynamic_peak_window_factor
        )
    elif modality_type == constants.ModalityType.RAMAN:
        # Apply preprocessing for Raman spectroscopy
        pass
    else:
        raise ValueError(f"Unsupported modality type: {modality_type}")
    
    return physical_pixel_coverage