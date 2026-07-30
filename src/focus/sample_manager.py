import os

from focus.constants import FocusOutputDirectories, ModalityType, MsiIonMode


class SampleManager:
	"""Creates and scaffolds sample folder structures for a FOCUS dataset.

	Can be used standalone as a Python package utility:

	    from focus.sample_manager import SampleManager
	    sm = SampleManager("/path/to/dataset")
	    sm.create_sample("sample_01", modalities=[{"name": "lipidomics", "type": "msi"}])
	"""

	def __init__(self, dataset_path: str) -> None:
		self.dataset_path = dataset_path

	def create_sample(self, sample_id: str, modalities: list[dict]) -> None:
		"""Create a new sample folder and its modality subfolders.

		Raises ValueError if the sample folder already exists or the name is invalid.
		modalities should be a list of dicts with 'name' and 'type' keys (same shape as
		the FOCUS config modalities list).
		"""
		_validate_sample_id(sample_id)
		sample_path = os.path.join(self.dataset_path, sample_id)
		if os.path.exists(sample_path):
			raise ValueError(f"Sample '{sample_id}' already exists at: {sample_path}")
		os.makedirs(sample_path)
		for modality in modalities:
			self._ensure_modality_folder(sample_id, modality["name"], modality["type"])

	def ensure_modality_folders(
		self, sample_ids: list[str], modality_name: str, modality_type: str
	) -> None:
		"""Ensure a modality subfolder exists inside each sample directory.

		Idempotent — existing folders are left untouched. For MSI modalities the
		pos/ and neg/ ion-mode subfolders are also created if missing.

		Both ion-mode subfolders are always created, since which polarities a sample
		has cannot be known here. That is safe: preprocessing decides a sample's ion
		modes from the presence of a complete .imzML + .ibd pair (see
		preprocessing._utils.find_imzml_pair), so an empty one is simply ignored and
		the user does not need to delete it.
		"""
		for sample_id in sample_ids:
			self._ensure_modality_folder(sample_id, modality_name, modality_type)

	def _ensure_modality_folder(
		self, sample_id: str, modality_name: str, modality_type: str
	) -> None:
		modality_path = os.path.join(self.dataset_path, sample_id, modality_name)
		os.makedirs(modality_path, exist_ok=True)
		if modality_type == ModalityType.MSI:
			os.makedirs(os.path.join(modality_path, MsiIonMode.POSITIVE), exist_ok=True)
			os.makedirs(os.path.join(modality_path, MsiIonMode.NEGATIVE), exist_ok=True)


def _validate_sample_id(sample_id: str) -> None:
	"""Raise ValueError if sample_id is not a valid folder name."""
	if not sample_id or not sample_id.strip():
		raise ValueError("Sample ID must not be empty.")
	if sample_id != sample_id.strip():
		raise ValueError("Sample ID must not have leading or trailing whitespace.")
	if os.sep in sample_id or (os.altsep and os.altsep in sample_id):
		raise ValueError(f"Sample ID must not contain path separators: '{sample_id}'")
	if sample_id in FocusOutputDirectories.list():
		raise ValueError(
			f"'{sample_id}' is a reserved FOCUS output directory name and cannot be used as a sample ID."
		)
