"""Run history for the main GUI progress screen.

The orchestrator and ``StepReporter`` report only the *current* position of a
run (stage, modality, sample, step) as partial status updates that the GUI
merges into one dict. ``ProgressTracker`` observes that merged status after
every update and derives what the snapshot alone cannot provide:

- timing: when the run, each stage and each modality started and ended;
- the samples seen in each (stage, modality), or in the stage itself when it
  has no modality level (annotation transfer), in order;
- a bounded history of the activity lines built by ``focus.reporting``.

It is reporting-only: it never changes the status the pipeline reports.
Times are UNIX timestamps in seconds; ``server_now`` lets the browser correct
for clock skew when the GUI is served from another machine.
"""

from __future__ import annotations

import threading
import time
from collections import deque

_MAX_ACTIVITY = 500
# A 'patch' (e.g. a line marked cached after it was sent) targets one of the latest lines.
_PATCH_WINDOW = 20


class ProgressTracker:
	"""Accumulates the timeline and message history of one pipeline run."""

	def __init__(self) -> None:
		self._lock = threading.Lock()
		self._run_started_at: float | None = None
		self._timeline: list[dict] = []
		self._activity: deque[dict] = deque(maxlen=_MAX_ACTIVITY)

	def reset(self) -> None:
		"""Start tracking a new run."""
		with self._lock:
			self._run_started_at = time.time()
			self._timeline = []
			self._activity.clear()

	def observe(self, status: dict) -> None:
		"""Record what changed in the merged status since the previous call."""
		with self._lock:
			if self._run_started_at is None:
				return
			now = time.time()
			state = status.get("state")
			stage = status.get("stage")

			if state in ("completed", "error"):
				self._close_open(now, failed=state == "error")
				return

			current = self._timeline[-1] if self._timeline else None
			if stage and (current is None or current["stage"] != stage):
				self._close_open(now)
				current = {
					"stage": stage, "started_at": now, "ended_at": None, "failed": False,
					"modalities": [], "samples": [],
				}
				self._timeline.append(current)
			if current is None:
				return

			modality = status.get("current_modality")
			mods = current["modalities"]
			if modality and (not mods or mods[-1]["name"] != modality):
				if mods and mods[-1]["ended_at"] is None:
					mods[-1]["ended_at"] = now
				mods.append({"name": modality, "started_at": now, "ended_at": None, "samples": []})

			sample = status.get("current_sample")
			if sample:
				open_mod = mods[-1] if mods and mods[-1]["ended_at"] is None else None
				seen = open_mod["samples"] if open_mod else current["samples"]
				if sample not in seen:
					seen.append(sample)

	def record(self, ops: list[dict] | None) -> None:
		"""Apply activity operations from the reporter: append 'add' lines, update 'patch' targets."""
		if not ops:
			return
		with self._lock:
			if self._run_started_at is None:
				return
			for op in ops:
				fields = {k: v for k, v in op.items() if k != "op"}
				if op["op"] == "add":
					self._activity.append(fields)
				elif op["op"] == "patch":
					for i in range(len(self._activity) - 1, max(-1, len(self._activity) - 1 - _PATCH_WINDOW), -1):
						if self._activity[i]["id"] == fields["id"]:
							self._activity[i].update(fields)
							break

	def snapshot(self) -> dict:
		"""History fields merged into every status response."""
		with self._lock:
			return {
				"run_started_at": self._run_started_at,
				"server_now": time.time(),
				"timeline": [
					{
						**stage,
						"samples": list(stage["samples"]),
						"modalities": [{**m, "samples": list(m["samples"])} for m in stage["modalities"]],
					}
					for stage in self._timeline
				],
				"activity": [dict(e) for e in self._activity],
			}

	# ── internals (lock held) ─────────────────────────────────────────────

	def _close_open(self, now: float, failed: bool = False) -> None:
		"""End the open stage and modality; on error, mark the open stage as failed."""
		if not self._timeline:
			return
		stage = self._timeline[-1]
		if stage["ended_at"] is None:
			stage["ended_at"] = now
			stage["failed"] = failed
		mods = stage["modalities"]
		if mods and mods[-1]["ended_at"] is None:
			mods[-1]["ended_at"] = now
