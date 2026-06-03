import os
import re
import logging

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


def discover_sample_ids(path: str, ignore_samples: list[str] | None = None) -> list[str]:
	"""Discover sample IDs from subdirectories under the given path.

	Returns a sorted list of directory names, excluding standard FOCUS output directories
	and any sample IDs listed in ignore_samples.
	"""
	excluded = set(FocusOutputDirectories.list()) | set(ignore_samples or [])
	sample_ids = sorted(
		d for d in os.listdir(path)
		if os.path.isdir(os.path.join(path, d)) and d not in excluded
	)
	return sample_ids


def _parse_step_label(desc: str) -> tuple[int, int]:
	"""Parse '3/9 - ...' → (3, 9). Returns (0, 0) if the pattern is not found."""
	m = re.match(r'^(\d+)/(\d+)', desc)
	if m:
		return int(m.group(1)), int(m.group(2))
	return 0, 0


class StepReporter:
	"""Single, unified reporting interface for the whole pipeline.

	Every textual line is fanned out to all *available* sinks through one call:
	  - console + the focus.log file, via the shared ``focus`` logger;
	  - the web GUI, via the optional ``callback`` (absent in headless runs).

	Sinks degrade gracefully and independently: the file handler only exists once
	a dataset path is known (``setup_logging``), the GUI callback only when a GUI is
	attached, and if no logging handlers are configured at all we fall back to
	``print`` so the console is never silent. Prefer ``reporter.message(...)`` over
	bare ``print()`` / ``logging`` so output reaches every interface at once.
	"""

	def __init__(self, callback=None):
		self._callback = callback
		self._logger = logging.getLogger("focus")

	def _emit(self, msg: str, level: int = logging.INFO) -> None:
		"""Fan a line out to the console + log file (via the 'focus' logger).

		Falls back to ``print`` when the logger has no handlers (e.g. a bare
		StepReporter() in a script or test that never called ``setup_logging``),
		so the console is never silent regardless of how FOCUS is launched.
		"""
		self._logger.log(level, msg)
		if not self._logger.hasHandlers():
			print(msg)

	def step(self, desc: str, current: int = 0, total: int = 0) -> None:
		"""Announce a named step on every interface (console, log file, GUI)."""
		self._emit(desc)
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

	def message(self, msg: str, level: int = logging.INFO) -> None:
		"""Report a status line to every available interface at once.

		Writes to the console and the focus.log file (via the 'focus' logger) and,
		when a GUI is attached, to the web GUI message log (callback). This is the
		single unified reporting call — use it instead of print()/logging so a line
		reaches all sinks, degrading gracefully when one (e.g. the GUI in a headless
		run, or the log file before setup_logging) is unavailable. Pass ``level`` to
		emit at a different logging level (e.g. logging.WARNING)."""
		self._emit(msg, level)
		if self._callback:
			self._callback({"message": msg})

	def set_sample(self, sample_id: str, index: int, total: int) -> None:
		"""Set the current sample context and reset sub-step fields. Reports on every interface."""
		self._emit(f"[{index}/{total}] Processing sample: {sample_id}")
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
		os.path.join(path, FocusOutputDirectories.MERGED, FocusOutputDirectories.PREPROCESSING),
		exist_ok=True,
	)
