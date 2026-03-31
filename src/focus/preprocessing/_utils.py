import os

from focus.constants import FocusOutputDirectories


def validate_path_readable(path: str) -> None:
	"""Validate that a path exists and is readable.

	Raises FileNotFoundError if the path does not exist,
	PermissionError if the path is not readable.
	"""
	if not os.path.exists(path):
		raise FileNotFoundError(f"The specified path does not exist: {path}")
	if not os.access(path, os.R_OK):
		raise PermissionError(f"The specified path is not readable: {path}")


def discover_sample_ids(path: str) -> list[str]:
	"""Discover sample IDs from subdirectories under the given path.

	Returns a sorted list of directory names, excluding standard FOCUS output directories.
	"""
	sample_ids = sorted(
		d for d in os.listdir(path)
		if os.path.isdir(os.path.join(path, d)) and d not in FocusOutputDirectories.list()
	)
	return sample_ids


def create_output_directories(path: str, sample_ids: list[str], modality_name: str) -> None:
	"""Create per-sample and merged preprocessing output directories."""
	for sample_id in sample_ids:
		os.makedirs(
			os.path.join(path, sample_id, FocusOutputDirectories.PREPROCESSING, modality_name),
			exist_ok=True,
		)
	os.makedirs(
		os.path.join(path, FocusOutputDirectories.MERGED, FocusOutputDirectories.PREPROCESSING, modality_name),
		exist_ok=True,
	)
