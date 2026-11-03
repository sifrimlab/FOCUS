"""Configuration defaults and supported modality/strategy combinations."""

from focus.config.options import (
    AlignmentStrategy,
    AnnotationFileType,
    ModalityType,
    MsiIntensityNormalization,
    MsiSampleType,
    RegistrationType,
    SegmentationBackgroundColor,
    SpotResolution,
    StCapture,
    StNormalization,
    StUnit,
)


# Modality types grouped by the shape of their data: image modalities carry a pixel grid,
# spot modalities a set of discrete positions. Alignment, registration and config validation
# all branch on this distinction — import these rather than redeclaring the lists.
IMAGE_MODALITY_TYPES = (ModalityType.MICROSCOPY_IMAGE, ModalityType.RAMAN)

SPOT_MODALITY_TYPES = (ModalityType.MSI, ModalityType.ST)


# Maps modality type to the file extension used for preprocessed/aligned output
MODALITY_FILE_EXTENSION = {
    ModalityType.MICROSCOPY_IMAGE: "ome.tiff",
    ModalityType.RAMAN: "ome.tiff",
    ModalityType.MSI: "h5ad",
    ModalityType.ST: "h5ad",
}


# Maps registration type to compatible modality types (None = all types)
REGISTRATION_COMPATIBILITY = {
    RegistrationType.FEATURE_EXTRACTION: [ModalityType.MICROSCOPY_IMAGE],
    RegistrationType.SPOT_INTERPOLATION: [ModalityType.MSI, ModalityType.ST],
    RegistrationType.SPOT_AGGREGATION: [ModalityType.MSI, ModalityType.ST],
    RegistrationType.RAMAN_PIXEL_INTERPOLATION: [ModalityType.RAMAN],
    RegistrationType.NONE: None,
}


# Maps alignment strategy to the compatible *reference* modality types for that strategy
# (None = all types). PRE_ALIGNED requires a spot-based reference, since the reference's
# own coordinates must already be expressible in the target's frame; the target may be any
# type. Validated against the reference modality in utils.py.
ALIGNMENT_STRATEGY_COMPATIBILITY = {
    AlignmentStrategy.MANUAL: None,
    AlignmentStrategy.PRE_ALIGNED: [ModalityType.MSI, ModalityType.ST],
}


# Human-readable labels for all string constants shown in the GUI.
# The GUI uses the original literals in the config file; these labels are display-only.
DISPLAY_NAMES: dict[str, str] = {
    # Modality types
    ModalityType.MICROSCOPY_IMAGE: "Microscopy Image",
    ModalityType.MSI: "MSI",
    ModalityType.RAMAN: "Raman",
    ModalityType.ST: "Spatial Transcriptomics",
    # Registration types
    RegistrationType.NONE: "None",
    RegistrationType.FEATURE_EXTRACTION: "Feature Extraction",
    RegistrationType.SPOT_INTERPOLATION: "Spot Interpolation",
    RegistrationType.SPOT_AGGREGATION: "Spot Aggregation",
    RegistrationType.RAMAN_PIXEL_INTERPOLATION: "Raman Pixel Interpolation",
    # Alignment strategies
    AlignmentStrategy.MANUAL: "Manual",
    AlignmentStrategy.PRE_ALIGNED: "Pre-Aligned",
    # MSI intensity normalisation
    MsiIntensityNormalization.TIC: "TIC",
    MsiIntensityNormalization.LOG: "Log",
    MsiIntensityNormalization.CLR: "CLR",
    MsiIntensityNormalization.TIC_MEAN_SCALED: "TIC Mean Scaled",
    MsiIntensityNormalization.MEDIAN: "Median",
    MsiIntensityNormalization.INTERNAL_STANDARD: "Internal Standard",
    MsiIntensityNormalization.NONE: "None",
    # Background colour
    SegmentationBackgroundColor.WHITE: "White",
    SegmentationBackgroundColor.BLACK: "Black",
    # MSI sample type
    MsiSampleType.TISSUE: "Tissue",
    MsiSampleType.MICROGRID: "Microgrid",
    # Spot resolution
    SpotResolution.STANDARD: "Standard",
    SpotResolution.SUB_CELLULAR: "Sub-cellular",
    # ST technology and normalization
    StCapture.SEQUENCING: "Sequencing (Visium, Visium HD, Stereo-seq, Slide-seq)",
    StCapture.IMAGING: "Imaging (Xenium, MERFISH, CosMx)",
    StUnit.SPOT: "Multicellular spots",
    StUnit.BIN: "Sub-cellular bins",
    StUnit.CELL: "Segmented cells",
    StNormalization.AUTO: "Automatic (technology default)",
    StNormalization.LOG_NORMALIZE: "Log-normalize (median depth)",
    StNormalization.CP10K_LOG: "Log-normalize (10,000 counts)",
    StNormalization.SIZE_FACTORS: "Size factors (median depth, linear)",
    StNormalization.AREA_LOG_NORMALIZE: "Log-normalize (area)",
    # Annotation file types
    AnnotationFileType.GEOJSON: "GeoJSON",
}
