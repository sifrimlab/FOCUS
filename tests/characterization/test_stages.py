"""Ordered pipeline stages compared with the preserved implementation."""

import json
from pathlib import Path

import focus.pipeline.stages as stages
from tests.characterization.stage_contract import collect_stage_contract


def test_stage_contract() -> None:
    """Retain outputs, force flags, releases, and progress in eight stage combinations."""
    expected = json.loads(Path(__file__).with_name("stage_contract.json").read_text())
    assert collect_stage_contract(stages) == expected
