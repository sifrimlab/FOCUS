"""Names of existing configuration, preprocessing and imzML metadata fields."""

from typing import Final

from focus.config.choices import _AbstractEnum


class ImzMLFileParser(_AbstractEnum):
    """Serialized keys for imz mlfile parser."""

    SCAN_SETTINGS_LIST: Final = "{http://psi.hupo.org/ms/mzml}scanSettingsList"
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

    COLOR_ENHANCEMENT: Final = "color_enhancement"
    REMOVE_BACKGROUND: Final = "remove_background"
    CROP_TO_TISSUE: Final = "crop_to_tissue"
    BACKGROUND_COLOR: Final = "background_color"
    MIN_OBJECT_COVERAGE: Final = "min_object_coverage"
    FORCE_RECOMPUTING: Final = "force_recomputing"
    CLIP_PERCENTILE: Final = "clip_percentile"
    CROP_MARGIN: Final = "crop_margin"
    GAMMA: Final = "gamma"
    CONTRAST_SATURATION: Final = "contrast_saturation"


class MsiPreprocessingParams(_AbstractEnum):
    """Serialized keys for msi preprocessing params."""

    LIPID_ANNOTATION_DB: Final = "lipid_annotation_db"
    MASS_TOLERANCE: Final = "mass_tolerance"
    FREQUENCY_THRESHOLD: Final = "frequency_threshold"
    INTENSITY_NORMALIZATION: Final = "intensity_normalization"
    RECALIBRATION_REFERENCE: Final = "recalibration_reference"
    INTERNAL_STANDARD_MZ: Final = "internal_standard_mz"
    DETECT_BACKGROUND: Final = "detect_background"
    SAMPLE_TYPE: Final = "sample_type"
    RESOLUTION: Final = "resolution"
    FORCE_RECOMPUTING: Final = "force_recomputing"


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

    RESOLUTION: Final = "resolution"
    MIN_COUNT_PER_SPOT: Final = "min_count_per_spot"
    MAX_COUNT_PER_SPOT: Final = "max_count_per_spot"
    MIN_GENES_PER_SPOT: Final = "min_genes_per_spot"
    MAX_GENES_PER_SPOT: Final = "max_genes_per_spot"
    MIN_SPOTS_PER_GENE: Final = "min_spots_per_gene"
    MIN_COUNT_SPOTS_RATIO_PER_GENE: Final = "min_count_spots_ratio_per_gene"
    REMOVE_MITOCHONDRIAL_GENES: Final = "remove_mitochondrial_genes"
    TOTAL_COUNTS_NORMALIZE: Final = "total_counts_normalize"
    LOG1P_TRANSFORM: Final = "log1p_transform"
    FORCE_RECOMPUTING: Final = "force_recomputing"
