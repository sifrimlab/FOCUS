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
	MZ_BINARY_METADATA = "mz_binary_metadata"
	INTENSITIES_BINARY_METADATA = "intensities_binary_metadata"

class MsiIonMode(_AbstractEnum):
	POSITIVE = "pos"
	NEGATIVE = "neg"

class ConfigParameters(_AbstractEnum):
	
	DATA_SOURCE_PATH = "data_source_path"
	SAMPLE_NAME = "sample_name"
	MODALITIES = "modalities"
	ANCHOR_MODALITY = "anchor_modality"
	PERFORM_PREPROCESSING = "perform_preprocessing"

class ModalityParameters(_AbstractEnum):

	MODALITY_NAME = "modality_name"
	MODALITY_TYPE = "modality_type"
	PREPROCESSING_SETTINGS = "preprocessing_settings"
	ALIGNMENT_SETTINGS = "alignment_settings"
	REGISTRATION_SETTINGS = "registration_settings"
	PHYSICAL_PIXEL_COVERAGE = "physical_pixel_coverage"


class RegistrationSettings(_AbstractEnum):
	TYPE = "type"

class RegistrationType(_AbstractEnum):
	NONE = "none"
	RESOLUTION_SCALING_TO_TARGET = "resolution_scaling_to_target"
	RESOLUTION_SCALING_TO_ANCHOR = "resolution_scaling_to_anchor"
	

class MicroscopyImageProcessingParams(_AbstractEnum):
	
	COLOR_ENHANCEMENT = "color_enhancement"
	REMOVE_BACKGROUND = "remove_background"
	CROP_TO_TISSUE = "crop_to_tissue"
	BACKGROUND_COLOR = "background_color"
	PYRAMID_LEVELS = "pyramid_levels"
	MIN_OBJECT_COVERAGE = "min_object_coverage"
	FORCE_RECOMPUTING = "force_recomputing"

class MsiPreprocessingParams(_AbstractEnum):

	LIPID_ANNOTATION_DB = "lipid_annotation_db"
	
	MASS_TOLERANCE = "mass_tolerance"
	FREQUENCY_THRESHOLD = "frequency_threshold"
	INTENSITY_NORMALIZATION = "intensity_normalization"
	BATCH_SIZE = "batch_size"
	FORCE_RECOMPUTING = "force_recomputing"

class RamanPreprocessingParams(_AbstractEnum):
	FORCE_RECOMPUTING = "force_recomputing"

class STPreprocessingParams(_AbstractEnum):
	MIN_COUNT_PER_SPOT = "min_count_per_spot"
	MAX_COUNT_PER_SPOT = "max_count_per_spot"
	MIN_SPOTS_PER_GENE = "min_spots_per_gene"
	MIN_COUNT_SPOTS_RATIO_PER_GENE = "min_count_spots_ratio_per_gene"
	TOTAL_COUNTS_NORMALIZE = "total_counts_normalize"
	LOG1P_TRANSFORM = "log1p_transform"
	FORCE_RECOMPUTING = "force_recomputing"

class ModalityType(_AbstractEnum):
	MICROSCOPY_IMAGE = "microscopy_image"
	MSI = "msi"
	RAMAN = "raman"
	ST = "st"

class TransformationType(_AbstractEnum):
	TRANSLATION = "translation"
	RIGID = "rigid"
	AFFINE = "affine"
	BSPLINE = "bspline"

class MsiIntensityNormalization(_AbstractEnum):
	TIC = "tic"
	NONE = "none"

class DecompositionMethod(_AbstractEnum):
	PCA = "pca"
	NMF = "nmf"