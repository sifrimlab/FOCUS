"""Initial main-GUI run state and its existing serialized status fields."""

from focus.runtime.types import Status


# ── Helpers ───────────────────────────────────────────────────────────────


def _default_status() -> Status:
    """Create an independent status dictionary with the established defaults."""
    return {
        "state": "idle",
        "stage": None,
        "stage_index": 0,
        "total_stages": 4,
        "current_modality": None,
        "current_modality_index": 0,
        "total_modalities": 0,
        "current_sample": None,
        "current_sample_index": 0,
        "total_samples": 0,
        "message": "",
        "error": None,
        "output_files": {},
        "alignment_port": 8000,
        "sub_step": None,
        "sub_step_index": 0,
        "sub_step_total": 0,
        "sub_step_progress": 0,
        "sub_step_items_total": 0,
        "sub_step_unit": None,
    }
