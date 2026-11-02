"""Standalone encoder CLI behavior recorded from the preserved source baseline."""

import json
import logging
from pathlib import Path

import pytest

from tests.encoding.grid_contract import collect_grid_contract, load_script


def test_grid_workflow_contract(tmp_path: Path) -> None:
    """Preserve lazy loading, level caching, forced runs, invalidation, and decoded outputs."""
    pytest.importorskip("torch")
    pytest.importorskip("timm")
    import focus.registration.free_form as implementation

    script = load_script(
        Path(__file__).resolve().parents[2] / "scripts" / "feature_extraction_only.py"
    )
    expected = json.loads(Path(__file__).with_name("grid_contract.json").read_text())
    logger = logging.getLogger("focus")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    try:
        assert collect_grid_contract(script, implementation, tmp_path) == expected
    finally:
        for handler in list(logger.handlers):
            if handler not in original_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(original_level)
