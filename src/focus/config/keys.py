"""Names of existing configuration, preprocessing and imzML metadata fields."""

from typing import Final

from focus.config.choices import _AbstractEnum


class ImzMLFileParser(_AbstractEnum):
    """Serialized keys for imz mlfile parser."""

    SCAN_SETTINGS_LIST: Final = "{http://psi.hupo.org/ms/mzml}scanSettingsList"
    FILE_DESCRIPTION: Final = "{http://psi.hupo.org/ms/mzml}fileDescription"
    REFERENCEABLE_PARAM_GROUP_LIST: Final = (
        "{http://psi.hupo.org/ms/mzml}referenceableParamGroupList"
    )
    REFERENCEABLE_PARAM_GROUP_REF: Final = (
        "{http://psi.hupo.org/ms/mzml}referenceableParamGroupRef"
    )
    RUN_KEY: Final = "{http://psi.hupo.org/ms/mzml}run"
    SPECTRUM_LIST_KEY: Final = "{http://psi.hupo.org/ms/mzml}spectrumList"
    SPECTRUM_KEY: Final = "{http://psi.hupo.org/ms/mzml}spectrum"

    SCAN_LIST: Final = "{http://psi.hupo.org/ms/mzml}scanList"
    SCAN: Final = "{http://psi.hupo.org/ms/mzml}scan"

    BINARY_DATA_ARRAY_LIST: Final = "{http://psi.hupo.org/ms/mzml}binaryDataArrayList"
    BINARY_DATA_ARRAY: Final = "{http://psi.hupo.org/ms/mzml}binaryDataArray"

    CV_PARAM: Final = "{http://psi.hupo.org/ms/mzml}cvParam"
    USER_PARAM: Final = "{http://psi.hupo.org/ms/mzml}userParam"


class MsiMetadata(_AbstractEnum):
    """Serialized keys for msi metadata."""

    INTENSITIES_DTYPE: Final = "intensities_dtype"
    MZ_DTYPE: Final = "mz_dtype"
    RASTER_SIZE: Final = "raster_size"

    PIXEL_COORDINATES: Final = "pixel_coordinates"
    PHYSICAL_COORDINATES: Final = "physical_coordinates"
    RASTER_COORDINATES: Final = "raster_coordinates"
    MZ_BINARY_METADATA: Final = "mz_binary_metadata"
    INTENSITIES_BINARY_METADATA: Final = "intensities_binary_metadata"
    LINE_AXIS: Final = "line_axis"  # PIXEL_COORDINATES column indexing the scan lines (0: horizontal, 1: vertical)
    STAGE_COORDINATES: Final = "stage_coordinates"  # True when every spectrum carries its stage position (3DPositionX/Y)
    PAIRING_BANDS: Final = "pairing_bands"  # per-spectrum m/z band sums, used to register the two polarities
    STAGE_POSITIONS: Final = "stage_positions"  # recorded stage positions before the scan-line rotation correction


class ConfigParameters(_AbstractEnum):
    """Serialized keys for config parameters."""

    DATASET_PATH: Final = "dataset_path"
    MODALITIES: Final = "modalities"
    REFERENCE_MODALITY: Final = "reference_modality"
    PERFORM_ALIGNMENT: Final = "perform_alignment"
    PERFORM_REGISTRATION: Final = "perform_registration"
    HUGGINGFACE_TOKEN: Final = "huggingface_token"
    SPATIAL_ANNOTATIONS: Final = "spatial_annotations"
    IGNORE_SAMPLES: Final = "ignore_samples"
    SAMPLES: Final = "samples"
    LAST_EDIT: Final = "last_edit"


class AnnotationsParameters(_AbstractEnum):
    """Serialized keys for annotations parameters."""

    MODALITY_NAME: Final = "modality_name"
    FILE_TYPE: Final = "file_type"


class ModalityParameters(_AbstractEnum):
    """Serialized keys for modality parameters."""

    NAME: Final = "name"
    TYPE: Final = "type"
    PROCESSING_SETTINGS: Final = "processing_settings"
    REGISTRATION_TYPE: Final = "registration_type"
    REGISTRATION_SETTINGS: Final = "registration_settings"
    ALIGNMENT_STRATEGY: Final = "alignment_strategy"
    ALIGNMENT_FORCE_RECOMPUTING: Final = "alignment_force_recomputing"


class MicroscopyImageProcessingParams(_AbstractEnum):
    """Serialized keys for microscopy image processing params."""

    IMAGE_TYPE: Final = "image_type"
    Z_PROJECTION: Final = "z_projection"
    CROP_TO_TISSUE: Final = "crop_to_tissue"
    REMOVE_BACKGROUND: Final = "remove_background"
    PIXEL_SIZE: Final = "pixel_size"
    FORCE_RECOMPUTING: Final = "force_recomputing"


# Microscopy settings removed in microscopy-3, with the reason (config validation rejects them).
MICROSCOPY_REMOVED_SETTINGS: dict[str, str] = {
    "color_enhancement": "contrast is display-only and automatic",
    "gamma": "contrast is display-only and automatic",
    "contrast_saturation": "contrast is display-only and automatic",
    "background_color": "the fill follows 'image_type'",
    "min_object_coverage": "tissue detection is calibrated in µm",
    "clip_percentile": "tissue detection is calibrated in µm",
    "crop_margin": "the crop margin is a fixed 100 µm",
}


class MsiPreprocessingParams(_AbstractEnum):
    """Serialized keys for msi preprocessing params."""

    LIPID_ANNOTATION_DB: Final = "lipid_annotation_db"
    MASS_TOLERANCE: Final = "mass_tolerance"
    INTENSITY_NORMALIZATION: Final = "intensity_normalization"
    RECALIBRATION_REFERENCE: Final = "recalibration_reference"
    INTERNAL_STANDARD_MZ: Final = "internal_standard_mz"
    SAMPLE_TYPE: Final = "sample_type"
    RESOLUTION: Final = "resolution"
    FORCE_RECOMPUTING: Final = "force_recomputing"


# MSI settings removed in msi-13, with the reason (config validation rejects them).
MSI_REMOVED_SETTINGS: dict[str, str] = {
    "detect_background": "foreground detection always runs; 'sample_type' calibrates it",
    "frequency_threshold": "fixed; noise is removed by the recurrence test and the S/N filter",
    "min_intensity_threshold": "replaced by the automatic S/N filter",
}

# MSI normalizations removed in msi-13, with the value that replaces each one.
MSI_REMOVED_NORMALIZATIONS: dict[str, str] = {
    "tic_mean_scaled": "tic",
    "log": "none",
    "clr": "none",
}


class RamanPreprocessingParams(_AbstractEnum):
    """Serialized keys for raman preprocessing params."""

    FORCE_RECOMPUTING: Final = "force_recomputing"
    MAX_WORKERS: Final = "max_workers"
    SAVGOL_WINDOW: Final = "savgol_window"
    SAVGOL_POLYORDER: Final = "savgol_polyorder"
    BG_MIN_AREA_FRACTION: Final = "bg_min_area_fraction"
    OTSU_THRESHOLD_FACTOR: Final = "otsu_threshold_factor"
    MIN_OBJECT_SIZE: Final = "min_object_size"


class STPreprocessingParams(_AbstractEnum):
    """Serialized keys for stpreprocessing params."""

    CAPTURE: Final = "capture"
    UNIT: Final = "unit"
    NORMALIZATION: Final = "normalization"
    QUALITY_CONTROL: Final = "quality_control"
    REMOVE_MITOCHONDRIAL_GENES: Final = "remove_mitochondrial_genes"
    FORCE_RECOMPUTING: Final = "force_recomputing"


# ST settings removed in st-5, with the setting that replaces each one (config validation
# rejects them and names the replacement).
ST_REMOVED_SETTINGS: dict[str, str] = {
    "resolution": "unit",
    "min_count_per_spot": "quality_control",
    "max_count_per_spot": "quality_control",
    "min_genes_per_spot": "quality_control",
    "max_genes_per_spot": "quality_control",
    "min_spots_per_gene": "capture",
    "min_count_spots_ratio_per_gene": "capture",
    "total_counts_normalize": "normalization",
    "log1p_transform": "normalization",
}
