import os
from abc import ABC, abstractmethod

from focus.constants import FocusOutputDirectories
from focus.preprocessing._utils import validate_path_readable


class BaseSample(ABC):
	"""Abstract base class for all modality-specific sample processors.

	Provides common initialization: path validation, attribute assignment,
	and output directory creation.
	"""

	def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
		validate_path_readable(source_path)
		self.source_path = source_path
		self.sample_id = sample_id
		self.modality_name = modality_name
		self.output_path = os.path.join(
			source_path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name
		)
		os.makedirs(self.output_path, exist_ok=True)


class BaseDataset(ABC):
	"""Abstract base class for all modality-specific dataset processors.

	Provides common initialization and enforces the process_dataset interface.
	"""

	def __init__(self, path: str, samples: list) -> None:
		self.dataset_source_path = path
		self.samples = samples

	@abstractmethod
	def process_dataset(self, **kwargs) -> dict[str, str]:
		...

	@staticmethod
	def _check_cache(output_path: str, force_recomputing: bool) -> bool:
		"""Returns True if a cached result exists and force_recomputing is False."""
		return not force_recomputing and os.path.exists(output_path)
