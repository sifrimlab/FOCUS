import os
import re

import tqdm as _tqdm_lib

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


def _parse_step_label(desc: str) -> tuple[int, int]:
	"""Parse '3/9 - ...' → (3, 9). Returns (0, 0) if the pattern is not found."""
	m = re.match(r'^(\d+)/(\d+)', desc)
	if m:
		return int(m.group(1)), int(m.group(2))
	return 0, 0


class StepReporter:
	"""Reports sub-step progress from preprocessing methods to the GUI.

	Always prints step descriptions to stdout for CLI usage. When a callback
	is provided, also fires it with structured progress data for the GUI.
	"""

	def __init__(self, callback=None):
		self._callback = callback

	def step(self, desc: str, current: int = 0, total: int = 0) -> None:
		"""Announce a named step. Prints to stdout and notifies the GUI."""
		print(desc)
		self._send(desc, current, total)

	def _send(self, desc: str, current: int, total: int) -> None:
		if self._callback:
			idx, n = _parse_step_label(desc)
			self._callback({
				"sub_step": desc,
				"sub_step_index": idx,
				"sub_step_total": n,
				"sub_step_progress": current,
				"sub_step_items_total": total,
			})

	def _update(self, desc: str, current: int, total: int) -> None:
		"""Update progress count without printing (called during tqdm iteration)."""
		self._send(desc, current, total)

	def update(self, desc: str, current: int, total: int) -> None:
		"""Update item-level progress without printing to stdout (e.g. mid-loop updates)."""
		self._send(desc, current, total)

	def tqdm(self, iterable, desc: str, total: int | None = None, **kwargs):
		"""tqdm replacement that also reports progress to the GUI."""
		if total is None and hasattr(iterable, '__len__'):
			total = len(iterable)
		n = total or 0
		self._send(desc, 0, n)  # Report step start; tqdm handles CLI display
		for i, item in enumerate(_tqdm_lib.tqdm(iterable, desc=desc, total=total, **kwargs)):
			yield item
			self._update(desc, i + 1, n)

	def message(self, msg: str) -> None:
		"""Send a status message to the GUI message log and print to stdout."""
		print(msg)
		if self._callback:
			self._callback({"message": msg})

	def set_sample(self, sample_id: str, index: int, total: int) -> None:
		"""Set the current sample context and reset sub-step fields. Fires callback."""
		print(f"[{index}/{total}] Processing sample: {sample_id}")
		if self._callback:
			self._callback({
				"current_sample": sample_id,
				"current_sample_index": index,
				"total_samples": total,
				"sub_step": None,
				"sub_step_index": 0,
				"sub_step_total": 0,
				"sub_step_progress": 0,
				"sub_step_items_total": 0,
			})


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
