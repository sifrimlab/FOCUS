import os

MODALITY_PREPROCESSING = lambda base_path, sample_id, modality_name, file_type: os.path.join(
	base_path, 
	sample_id,
	FocusOutputDirectories.PREPROCESSING,
	modality_name,
	f"{modality_name}_{sample_id}_processed.{file_type}"
)

MODALITY_PREPROCESSING_MERGED = lambda base_path, modality_name, file_type: os.path.join(
	base_path,
	FocusOutputDirectories.MERGED,
	FocusOutputDirectories.PREPROCESSING,
	f"{modality_name}_merged_processed.{file_type}"
)

MODALITY_ALIGNMENT = lambda base_path, sample_id, modality_name, file_type: os.path.join(
	base_path,
	sample_id,
	FocusOutputDirectories.ALIGNMENT,
	f"{modality_name}_{sample_id}_processed_aligned.{file_type}"
)

MODALITY_ALIGNMENT_MERGED = lambda base_path, modality_name, file_type: os.path.join(
	base_path,
	FocusOutputDirectories.MERGED,
	FocusOutputDirectories.ALIGNMENT,
	f"{modality_name}_merged_processed_aligned.{file_type}"
)

MODALITY_REGISTRATION = lambda base_path, sample_id, modality_name, file_type: os.path.join(
	base_path,
	sample_id,
	FocusOutputDirectories.REGISTRATION,
	f"{modality_name}_{sample_id}_processed_aligned_registered.{file_type}"
)

MODALITY_REGISTRATION_MERGED = lambda base_path, modality_name, file_type: os.path.join(
	base_path,
	FocusOutputDirectories.MERGED,
	FocusOutputDirectories.REGISTRATION,
	f"{modality_name}_merged_processed_aligned_registered.{file_type}"
)

MULTIMODAL_DATASET = lambda base_path, file_type: os.path.join(
	base_path,
	FocusOutputDirectories.MERGED,
	f"multimodal_dataset.{file_type}"
)

MODALITY_ANNOTATION = lambda base_path, sample_id, modality_name, file_type: os.path.join(
	base_path,
	sample_id,
	FocusOutputDirectories.ANNOTATIONS,
	f"{modality_name}_{sample_id}_annotated.{file_type}"
)

MODALITY_ANNOTATION_MERGED = lambda base_path, modality_name, file_type: os.path.join(
	base_path,
	FocusOutputDirectories.MERGED,
	FocusOutputDirectories.ANNOTATIONS,
	f"{modality_name}_merged_annotated.{file_type}"
)


class _AbstractEnum():
	def __init__(self) -> None:
		raise Exception("Enum Classes can't be concrete!")

	@classmethod
	def list(cls):
		varList = [attr for attr in vars(cls) if not callable(getattr(cls, attr)) and not attr.startswith("__")]
		return [vars(cls)[elem] for elem in varList]
	
class FocusOutputDirectories(_AbstractEnum):
	PREPROCESSING = "preprocessing"
	ALIGNMENT = "alignment"
	REGISTRATION = "registration"
	ANNOTATIONS = "annotations"
	PLOTS = "plots"
	RESOURCES = "resources"
	MERGED = "merged"

class ImzMLFileParser(_AbstractEnum):

	SCAN_SETTINGS_LIST = "{http://psi.hupo.org/ms/mzml}scanSettingsList"
	REFERENCEABLE_PARAM_GROUP_LIST = "{http://psi.hupo.org/ms/mzml}referenceableParamGroupList"
	REFERENCEABLE_PARAM_GROUP_REF = "{http://psi.hupo.org/ms/mzml}referenceableParamGroupRef"
	RUN_KEY = "{http://psi.hupo.org/ms/mzml}run"
	SPECTRUM_LIST_KEY = "{http://psi.hupo.org/ms/mzml}spectrumList"
	SPECTRUM_KEY = "{http://psi.hupo.org/ms/mzml}spectrum"

	SCAN_LIST = "{http://psi.hupo.org/ms/mzml}scanList"
	SCAN = "{http://psi.hupo.org/ms/mzml}scan"
	
	BINARY_DATA_ARRAY_LIST = "{http://psi.hupo.org/ms/mzml}binaryDataArrayList"
	BINARY_DATA_ARRAY = "{http://psi.hupo.org/ms/mzml}binaryDataArray"

	CV_PARAM = "{http://psi.hupo.org/ms/mzml}cvParam"
	USER_PARAM = "{http://psi.hupo.org/ms/mzml}userParam"

class SegmentationBackgroundColor(_AbstractEnum):
	WHITE = "white"
	BLACK = "black"
	
class MsiMetadata(_AbstractEnum):
	INTENSITIES_DTYPE = "intensities_dtype"
	MZ_DTYPE = "mz_dtype"
	RASTER_SIZE = "raster_size"

	PIXEL_COORDINATES = "pixel_coordinates"
	PHYSICAL_COORDINATES = "physical_coordinates"
	RASTER_COORDINATES = "raster_coordinates"
	MZ_BINARY_METADATA = "mz_binary_metadata"
	INTENSITIES_BINARY_METADATA = "intensities_binary_metadata"

class MsiIonMode(_AbstractEnum):
	POSITIVE = "pos"
	NEGATIVE = "neg"

class ConfigParameters(_AbstractEnum):
	DATASET_PATH = "dataset_path"
	MODALITIES = "modalities"
	REFERENCE_MODALITY = "reference_modality"
	PERFORM_ALIGNMENT = "perform_alignment"
	PERFORM_REGISTRATION = "perform_registration"
	HUGGINGFACE_TOKEN = "huggingface_token"
	SPATIAL_ANNOTATIONS = "spatial_annotations"
	IGNORE_SAMPLES = "ignore_samples"
	SAMPLES = "samples"
	LAST_EDIT = "last_edit"

class AnnotationsParameters(_AbstractEnum):
	MODALITY_NAME = "modality_name"
	FILE_TYPE = "file_type"

class AnnotationFileType(_AbstractEnum):
	GEOJSON = "geojson"

class ModalityParameters(_AbstractEnum):
	NAME = "name"
	TYPE = "type"
	PROCESSING_SETTINGS = "processing_settings"
	REGISTRATION_TYPE = "registration_type"
	REGISTRATION_SETTINGS = "registration_settings"
	ALIGNMENT_STRATEGY = "alignment_strategy"
	ALIGNMENT_FORCE_RECOMPUTING = "alignment_force_recomputing"

class RegistrationType(_AbstractEnum):
	NONE = "none"
	FEATURE_EXTRACTION = "feature_extraction"
	SPOT_INTERPOLATION = "spot_interpolation"
	SPOT_AGGREGATION = "spot_aggregation"
	RAMAN_PIXEL_INTERPOLATION = "raman_pixel_interpolation"

class AlignmentStrategy(_AbstractEnum):
	MANUAL = "manual"
	PRE_ALIGNED = "pre_aligned"


class MicroscopyImageProcessingParams(_AbstractEnum):

	COLOR_ENHANCEMENT = "color_enhancement"
	REMOVE_BACKGROUND = "remove_background"
	CROP_TO_TISSUE = "crop_to_tissue"
	BACKGROUND_COLOR = "background_color"
	MIN_OBJECT_COVERAGE = "min_object_coverage"
	FORCE_RECOMPUTING = "force_recomputing"
	GAUSSIAN_BLUR_KERNEL_SIZE = "gaussian_blur_kernel_size"
	MIN_OBJECT_SIZE = "min_object_size"
	CLIP_PERCENTILE = "clip_percentile"
	CROP_MARGIN = "crop_margin"
	GAMMA = "gamma"
	CONTRAST_SATURATION = "contrast_saturation"

class MsiPreprocessingParams(_AbstractEnum):

	LIPID_ANNOTATION_DB = "lipid_annotation_db"
	MASS_TOLERANCE = "mass_tolerance"
	FREQUENCY_THRESHOLD = "frequency_threshold"
	INTENSITY_NORMALIZATION = "intensity_normalization"
	RECALIBRATION_REFERENCE = "recalibration_reference"
	MIN_INTENSITY_THRESHOLD = "min_intensity_threshold"
	DETECT_BACKGROUND = "detect_background"
	SAMPLE_TYPE = "sample_type"
	FORCE_RECOMPUTING = "force_recomputing"

class RamanPreprocessingParams(_AbstractEnum):
	FORCE_RECOMPUTING = "force_recomputing"
	MAX_WORKERS = "max_workers"
	SAVGOL_WINDOW = "savgol_window"
	SAVGOL_POLYORDER = "savgol_polyorder"
	BG_MIN_AREA_FRACTION = "bg_min_area_fraction"
	OTSU_THRESHOLD_FACTOR = "otsu_threshold_factor"
	MIN_OBJECT_SIZE = "min_object_size"

class STPreprocessingParams(_AbstractEnum):
	MIN_COUNT_PER_SPOT = "min_count_per_spot"
	MAX_COUNT_PER_SPOT = "max_count_per_spot"
	MIN_GENES_PER_SPOT = "min_genes_per_spot"
	MAX_GENES_PER_SPOT = "max_genes_per_spot"
	MIN_SPOTS_PER_GENE = "min_spots_per_gene"
	MIN_COUNT_SPOTS_RATIO_PER_GENE = "min_count_spots_ratio_per_gene"
	REMOVE_MITOCHONDRIAL_GENES = "remove_mitochondrial_genes"
	TOTAL_COUNTS_NORMALIZE = "total_counts_normalize"
	LOG1P_TRANSFORM = "log1p_transform"
	FORCE_RECOMPUTING = "force_recomputing"

class ModalityType(_AbstractEnum):
	MICROSCOPY_IMAGE = "microscopy_image"
	MSI = "msi"
	RAMAN = "raman"
	ST = "st"

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

class TransformationType(_AbstractEnum):
	TRANSLATION = "translation"
	RIGID = "rigid"
	AFFINE = "affine"
	BSPLINE = "bspline"

class MsiSampleType(_AbstractEnum):
	TISSUE   = "tissue"
	MICROGRID = "microgrid"

class MsiIntensityNormalization(_AbstractEnum):
	TIC = "tic"
	LOG = "log"
	CLR = "clr"
	GLOBAL_SCALING = "global_scaling"
	NONE = "none"

class DecompositionMethod(_AbstractEnum):
	PCA = "pca"
	NMF = "nmf"


# Human-readable labels for all string constants shown in the GUI.
# The GUI uses the original literals in the config file; these labels are display-only.
DISPLAY_NAMES: dict[str, str] = {
	# Modality types
	ModalityType.MICROSCOPY_IMAGE: "Microscopy Image",
	ModalityType.MSI:              "MSI",
	ModalityType.RAMAN:            "Raman",
	ModalityType.ST:               "Spatial Transcriptomics",
	# Registration types
	RegistrationType.NONE:                        "None",
	RegistrationType.FEATURE_EXTRACTION:          "Feature Extraction",
	RegistrationType.SPOT_INTERPOLATION:          "Spot Interpolation",
	RegistrationType.SPOT_AGGREGATION:            "Spot Aggregation",
	RegistrationType.RAMAN_PIXEL_INTERPOLATION:   "Raman Pixel Interpolation",
	# Alignment strategies
	AlignmentStrategy.MANUAL:      "Manual",
	AlignmentStrategy.PRE_ALIGNED: "Pre-Aligned",
	# MSI intensity normalisation
	MsiIntensityNormalization.TIC:  "TIC",
	MsiIntensityNormalization.LOG:  "Log",
	MsiIntensityNormalization.CLR:  "CLR",
	MsiIntensityNormalization.GLOBAL_SCALING: "Global Scaling",
	MsiIntensityNormalization.NONE: "None",
	# Background colour
	SegmentationBackgroundColor.WHITE: "White",
	SegmentationBackgroundColor.BLACK: "Black",
	# MSI sample type
	MsiSampleType.TISSUE:    "Tissue",
	MsiSampleType.MICROGRID: "Microgrid",
	# Annotation file types
	AnnotationFileType.GEOJSON: "GeoJSON",
}