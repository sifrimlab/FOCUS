class _AbstractEnum():
	def __init__(self) -> None:
		raise Exception("Enum Classes can't be concrete!")

	@classmethod
	def list(cls):
		varList = [attr for attr in vars(cls) if not callable(getattr(cls, attr)) and not attr.startswith("__")]
		return [vars(cls)[elem] for elem in varList]

class ImzMLFileParser(_AbstractEnum):

	SCAN_SETTINGS = "{http://psi.hupo.org/ms/mzml}scanSettingsList"
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

class AlignmentSettings(_AbstractEnum):
	TRANSFORMATIONS = "transformations"

class RegistrationSettings(_AbstractEnum):
	TYPE = "type"

class RegistrationType(_AbstractEnum):
	NONE = "none"
	RESOLUTION_SCALING = "resolution_scaling"

class ImagingPreprocessing(_AbstractEnum):
	
	CROP = "crop"
	FILTER_STRENGTH = "filter_strength"
	SMOOTHING = "smoothing"
	COLOR_ENHANCEMENT = "color_enhancement"

class LipidomicsPreprocessing(_AbstractEnum):
	
	PEAK_PICKING = "peak_picking"
	PEAK_PROMINENCE_THRESHOLD = "peak_prominence_threshold"
	PEAK_WINDOW_TOLERANCE_PPM = "peak_window_tolerance_ppm"
	DYNAMIC_PEAK_WINDOW = "dynamic_peak_window"
	DYNAMIC_PEAK_WINDOW_FACTOR = "dynamic_peak_window_factor"

class ModalityType(_AbstractEnum):
	
	MICROSCOPY_IMAGE = "microscopy_image"
	MSI = "msi"
	RAMAN = "raman"

class ImagingFilterStrength(_AbstractEnum):
	
	SOFT = "soft"
	MEDIUM = "medium"
	AGGRESSIVE = "aggressive"

class TransformationType(_AbstractEnum):
	
	TRANSLATION = "translation"
	RIGID = "rigid"
	AFFINE = "affine"
	BSPLINE = "bspline"
