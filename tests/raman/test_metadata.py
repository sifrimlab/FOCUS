"""Metadata defaults and validation recorded before the refactor."""

import json
from pathlib import Path
from typing import cast

import numpy as np

from focus.preprocessing.raman.metadata import RamanMetadata


def test_metadata_contract() -> None:
    """Preserve unset fields, bool handling, float32 copies, and exact errors."""
    reference = json.loads(
        Path(__file__).with_name("metadata_contract.json").read_text()
    )
    cases = cast(dict[str, list[list[object]]], reference["cases"])
    assert [getattr(RamanMetadata(), name) for name in cases] == reference["defaults"]
    values: tuple[object, ...] = (
        None,
        False,
        True,
        -1,
        0,
        1,
        1.5,
        float("nan"),
        "",
        "laser",
        [],
        np.zeros(2),
        np.zeros((2, 2)),
        np.zeros((2, 3)),
    )
    for name, expected in cases.items():
        rows: list[list[object]] = []
        for value in values:
            metadata = RamanMetadata()
            try:
                setattr(metadata, name, value)
                actual = getattr(metadata, name)
                saved: object = (
                    [actual.dtype.str, list(actual.shape), actual.tolist()]
                    if isinstance(actual, np.ndarray)
                    else repr(actual)
                )
                rows.append(
                    ["ok", type(actual).__name__, saved, list(metadata.__dict__)]
                )
            except Exception as error:
                rows.append([type(error).__name__, str(error)])
        assert rows == expected
