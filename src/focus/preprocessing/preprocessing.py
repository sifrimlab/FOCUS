from focus.constants import ModalityType, MsiIntensityNormalization
from focus.preprocessing._utils import validate_path_readable, discover_sample_ids, create_output_directories
from focus.preprocessing._registry import _MODALITY_REGISTRY

# Import modality modules to trigger their register_modality() calls
import focus.preprocessing.microscopy_image
import focus.preprocessing.lipidomics
import focus.preprocessing.raman
import focus.preprocessing.transcriptomic


def preprocess_modality(path: str, modality_name: str, modality_type: str, preprocessing_settings: dict, step_reporter=None, ignore_samples: list[str] | None = None) -> dict[str, str]:
	'''
	Apply preprocessing steps to a given modality based on its type and settings.
	This method is an entry point for the preprocessing pipeline.
	All the samples in a given modality will be processed, producing output for each one and a combined output if applicable.

	Parameters
	----------
		path: str
			The path to the directory where the source data are stored.
		modality_name: str
			The name of the modality being processed.
		modality_type: str
			The type of the modality (e.g., 'microscopy_image', 'msi', 'raman', 'st').
		preprocessing_settings: dict
			A dictionary containing the preprocessing settings for the modality.

	Returns
	-------
		dict[str, str]
			A dictionary with keys as sample identifiers and values as paths to the preprocessed data.
	'''

	# Validate inputs
	if modality_type not in ModalityType.list():
		raise ValueError(f"Unsupported modality type: {modality_type}")

	validate_path_readable(path)

	# Discover samples and create output directories
	sample_ids = discover_sample_ids(path, ignore_samples=ignore_samples)
	create_output_directories(path, sample_ids, modality_name)

	# Dispatch to the registered modality handler
	handler = _MODALITY_REGISTRY[modality_type]
	samples = handler.create_samples(path, sample_ids, modality_name, preprocessing_settings)
	dataset = handler.create_dataset(path, samples, preprocessing_settings)
	settings = handler.extract_settings(preprocessing_settings)

	return dataset.process_dataset(step_reporter=step_reporter, **settings)
