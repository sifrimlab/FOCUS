"""Unified progress reporting for the whole pipeline.

Every progress change (stage, modality, sample, step) becomes one *activity
line* with the same structure everywhere::

	[step] [sample] [modality] [stage]

The same line is written to the console and focus.log (``format_activity``)
and, when a GUI is attached, sent to it through the callback together with the
full current context, so the GUI's progress tracks and its activity log never
disagree.

Line kinds:
- ``step``: a step started, or the context (sample, modality, stage) of the
  running step changed;
- ``done``: a step ended, with its duration (never for cached steps);
- ``detail`` / ``warning`` / ``error``: free text attached to the current context.

Context changes are silent: they update the status at once but only produce a
line with the next step, detail or cached mark, so a sample that starts with a
step is announced by that step alone. Item progress inside a step (tiles,
patches, channels) never produces lines.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

import tqdm as _tqdm_lib

STAGE_LABELS = {
	"preprocessing": "Preprocessing",
	"alignment": "Alignment",
	"annotation_transfer": "Annotation transfer",
	"registration": "Registration",
	"compiling": "Compiling",
}

_LEVELS = {"step": logging.INFO, "done": logging.INFO, "detail": logging.INFO,
	"warning": logging.WARNING, "error": logging.ERROR}


def format_step_label(index: int, total: int, name: str) -> str:
	"""'3/8 - Name', the single place where step labels are built."""
	return f"{index}/{total} - {name}" if total else name


def format_activity(entry: dict) -> str:
	"""Console / log rendering of an activity line (ASCII, empty segments omitted)."""
	kind = entry["kind"]
	if kind == "detail":
		return f"    - {entry['text']}"
	if kind == "warning":
		return f"    ! {entry['text']}"
	segments = []
	if entry.get("step"):
		segments.append(format_step_label(entry.get("step_index", 0), entry.get("step_total", 0), entry["step"]))
	for key in ("sample", "modality"):
		if entry.get(key):
			segments.append(entry[key])
	if entry.get("stage"):
		segments.append(STAGE_LABELS.get(entry["stage"], entry["stage"]))
	line = " ".join(f"[{s}]" for s in segments)
	if kind == "done":
		return f"{line} done in {entry.get('seconds', 0):.1f}s"
	if kind == "error":
		return f"{line} failed: {entry.get('text', '')}".strip()
	return f"{line} (cached)" if entry.get("cached") else line


class StepReporter:
	"""Single reporting interface: console + focus.log via the ``focus`` logger, GUI via ``callback``.

	The orchestrator opens the context with ``stage()`` and ``modality()``;
	modules report ``step()`` / ``tqdm()`` / ``samples()`` and attach
	``detail()`` / ``warning()`` lines. Calls are serialized by a re-entrant
	lock (alignment reports from its own thread) and the callback runs under it,
	so lines reach the GUI in order.
	"""

	def __init__(self, callback=None):
		self._callback = callback
		self._logger = logging.getLogger("focus")
		self._lock = threading.RLock()
		self._next_id = 1
		self._stage: str | None = None
		self._stage_index = 0
		self._total_stages = 0
		self._modality: str | None = None
		self._modality_index = 0
		self._total_modalities = 0
		self._sample: str | None = None
		self._sample_index = 0
		self._total_samples = 0
		# Open step: name, index, total, unit, items_total, progress, cached, opened_at, announced.
		self._step: dict | None = None
		# The context changed and no line names it yet.
		self._dirty = False
		self._last_line_id: int | None = None
		self._message = ""
		# Activity operations waiting for the next callback.
		self._ops: list[dict] = []

	# ── context ───────────────────────────────────────────────────────────

	@contextmanager
	def stage(self, stage_id: str, index: int, total: int):
		"""Run a pipeline stage; the open step closes when the stage ends normally."""
		with self._lock:
			self._close_step()
			self._stage, self._stage_index, self._total_stages = stage_id, index, total
			self._set_modality(None, 0, 0)
			self._set_sample(None, 0, 0)
			self._dirty = True
			self._send(extra={"state": "running"})
		yield self
		with self._lock:
			self._close_step()
			self._send()

	@contextmanager
	def modality(self, name: str, index: int, total: int):
		"""Process one modality of the current stage."""
		with self._lock:
			self._close_step()
			self._set_modality(name, index, total)
			self._set_sample(None, 0, 0)
			self._dirty = True
			self._send()
		yield self
		with self._lock:
			self._close_step()
			self._set_modality(None, 0, 0)
			self._send()

	def samples(self, items, id_of=None):
		"""Iterate samples whose steps are reported inside the loop body.

		Each item becomes the current sample (its open step closes first); the
		sample is cleared when the loop ends, also on error or ``break``.
		"""
		items = list(items)
		n = len(items)
		try:
			for i, item in enumerate(items, 1):
				with self._lock:
					self._close_step()
					self._set_sample(_sample_id(item, id_of), i, n)
					self._dirty = True
					self._send()
				yield item
			with self._lock:
				self._close_step()
		finally:
			with self._lock:
				self._set_sample(None, 0, 0)
				self._send()

	def set_state(self, state: str, **extra) -> None:
		"""Change the run state (e.g. ``alignment_waiting``) without a line."""
		with self._lock:
			self._send(extra={"state": state, **extra})

	# ── steps ─────────────────────────────────────────────────────────────

	def step(self, index: int, total: int, name: str, *, unit: str | None = None,
			items_total: int = 0, cached: bool = False) -> None:
		"""Start step ``index/total``; repeated calls with the same step only update progress."""
		with self._lock:
			s = self._step
			if s and (s["index"], s["total"], s["name"]) == (index, total, name) and not self._dirty:
				s.update(unit=unit, items_total=items_total)
				if cached:
					self.cached()
				else:
					self._send()
				return
			self._open_step(index, total, name, unit, items_total, cached)
			self._add_line("step")
			self._send()

	def tqdm(self, iterable, step_index: int, step_total: int, name: str, *, unit: str | None = None,
			id_of=None, **tqdm_kwargs):
		"""Iterate with a console progress bar while reporting step ``step_index/step_total``.

		With ``unit="sample"`` each item becomes the current sample and gets its
		own line under the same step; the sample is cleared after the loop. Other
		units (tile, channel, patch) only report item progress. ``total=`` keeps
		its tqdm meaning: the number of items, for iterables without a length.
		"""
		n = tqdm_kwargs.pop("total", None)
		if n is None and hasattr(iterable, "__len__"):
			n = len(iterable)
		n = n or 0
		per_sample = unit == "sample"
		with self._lock:
			self._open_step(step_index, step_total, name, unit, n, False)
			if not per_sample:
				self._add_line("step")
			self._send()
		label = format_step_label(step_index, step_total, name)
		try:
			for i, item in enumerate(_tqdm_lib.tqdm(iterable, desc=label, total=n or None, unit=unit or "it", **tqdm_kwargs)):
				with self._lock:
					if per_sample:
						self._set_sample(_sample_id(item, id_of), i + 1, n)
						self._step["progress"] = i
						self._add_line("step")
					self._send()
				yield item
				with self._lock:
					if self._step:
						self._step["progress"] = i + 1
					self._send()
		finally:
			if per_sample:
				with self._lock:
					# Back to the step's own context, already announced: no new line needed.
					self._set_sample(None, 0, 0)
					self._send()

	def update(self, current: int, total: int | None = None) -> None:
		"""Item progress of the open step (no line)."""
		with self._lock:
			if self._step:
				self._step["progress"] = current
				if total is not None:
					self._step["items_total"] = total
			self._send()

	def cached(self) -> None:
		"""Mark the current context as served from cache (no 'done' line follows)."""
		with self._lock:
			s = self._step
			# A step spanning samples is not cached as a whole when one sample is.
			if s and s["unit"] != "sample":
				s["cached"] = True
			if self._dirty or self._last_line_id is None:
				self._add_line("step", cached=True)
			else:
				self._send_ops([{"op": "patch", "id": self._last_line_id, "cached": True}])
				self._log("detail", "cached result reused")
				return
			self._send()

	# ── free text ─────────────────────────────────────────────────────────

	def detail(self, msg: str) -> None:
		"""Attach an informative line to the current context."""
		self._text("detail", msg)

	def warning(self, msg: str) -> None:
		"""Attach a warning line to the current context."""
		self._text("warning", msg)

	def finish(self) -> None:
		"""Close the open step at the end of the run."""
		with self._lock:
			self._close_step()
			self._send()

	def fail(self, exc: BaseException) -> None:
		"""Report the error in the current context; the failed step gets no 'done' line."""
		with self._lock:
			self._step = None
			self._add_line("error", text=f"{type(exc).__name__}: {exc}")
			self._send()

	# ── internals (lock held) ─────────────────────────────────────────────

	def _text(self, kind: str, msg: str) -> None:
		with self._lock:
			if self._dirty:
				self._add_line("step")
			self._add_line(kind, text=msg)
			self._send()

	def _set_modality(self, name, index, total):
		self._modality, self._modality_index, self._total_modalities = name, index, total

	def _set_sample(self, sample, index, total):
		self._sample, self._sample_index, self._total_samples = sample, index, total

	def _open_step(self, index, total, name, unit, items_total, cached):
		self._close_step()
		self._step = {
			"index": index, "total": total, "name": name, "unit": unit, "items_total": items_total,
			"progress": 0, "cached": cached, "opened_at": time.time(), "announced": False,
		}

	def _close_step(self):
		s = self._step
		self._step = None
		if s and s["announced"] and not s["cached"]:
			# A step spanning samples ends at the level of its modality, not of its last sample.
			sample = None if s["unit"] == "sample" else s["sample"]
			self._add_line("done", step=s, sample=sample, seconds=time.time() - s["opened_at"])

	def _add_line(self, kind: str, *, step: dict | None = None, sample: str | None = None, **fields):
		"""Create a line in the current context (``step``/``sample`` override it for 'done')."""
		s = step if step is not None else self._step
		entry = {
			"id": self._next_id,
			"t": time.time(),
			"kind": kind,
			"stage": self._stage,
			"modality": self._modality,
			"sample": sample if step is not None else self._sample,
			"step": s["name"] if s else None,
			"step_index": s["index"] if s else 0,
			"step_total": s["total"] if s else 0,
			"cached": bool(fields.pop("cached", False) or (s and s["cached"] and kind == "step")),
			**fields,
		}
		self._next_id += 1
		if kind == "step":
			self._dirty = False
			self._last_line_id = entry["id"]
			if s:
				s["announced"] = True
				s["sample"] = self._sample
		self._message = entry.get("text") or entry["step"] or self._message
		self._log(kind, entry=entry)
		self._ops.append({"op": "add", **entry})

	def _log(self, kind: str, text: str | None = None, entry: dict | None = None) -> None:
		line = format_activity(entry) if entry else format_activity({"kind": kind, "text": text})
		self._logger.log(_LEVELS[kind], line)
		if not self._logger.hasHandlers():
			print(line)

	def _send_ops(self, ops: list) -> None:
		self._ops.extend(ops)
		self._send()

	def _send(self, extra: dict | None = None) -> None:
		"""Send the full context plus any pending activity operations to the GUI."""
		ops, self._ops = self._ops, []
		if not self._callback:
			return
		s = self._step
		status = {
			"stage": self._stage,
			"stage_index": self._stage_index,
			"total_stages": self._total_stages,
			"current_modality": self._modality,
			"current_modality_index": self._modality_index,
			"total_modalities": self._total_modalities,
			"current_sample": self._sample,
			"current_sample_index": self._sample_index,
			"total_samples": self._total_samples,
			"sub_step": format_step_label(s["index"], s["total"], s["name"]) if s else None,
			"sub_step_index": s["index"] if s else 0,
			"sub_step_total": s["total"] if s else 0,
			"sub_step_progress": s["progress"] if s else 0,
			"sub_step_items_total": s["items_total"] if s else 0,
			"sub_step_unit": s["unit"] if s else None,
			"message": self._message,
			**(extra or {}),
		}
		if ops:
			status["activity"] = ops
		self._callback(status)


def _sample_id(item, id_of=None) -> str | None:
	"""Sample id of a loop item: ``id_of(item)``, the item itself if a str, or its ``sample_id``."""
	if id_of is not None:
		return id_of(item)
	if isinstance(item, str):
		return item
	sid = getattr(item, "sample_id", None)
	return sid if isinstance(sid, str) else None


# ── active reporter (for code without a reporter handle) ──────────────────

_active_lock = threading.Lock()
_active: StepReporter | None = None


def use_reporter(reporter: StepReporter | None) -> None:
	"""Make ``reporter`` the one returned by ``get_reporter()`` (None resets it)."""
	global _active
	with _active_lock:
		_active = reporter


def get_reporter() -> StepReporter:
	"""The reporter of the running pipeline, or a console-only one outside a run."""
	with _active_lock:
		return _active if _active is not None else StepReporter()
