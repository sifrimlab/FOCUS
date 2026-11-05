"""Characterize recursive encoder backoff without allocating GPU memory."""

import json
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

torch = pytest.importorskip("torch")

from focus.registration.batching import _OOMError, forward_with_retry  # noqa: E402


@pytest.mark.parametrize("outputs", [1, 2])
def test_backoff_matches_baseline(outputs: int) -> None:
    """Retain original slice order for every output and the reduced batch limit."""
    calls: list[list[float]] = []

    def encode(windows: NDArray[np.float32]) -> tuple[NDArray[np.float32], ...]:
        """Record input slices and simulate failure for batches larger than two."""
        calls.append(windows.ravel().tolist())
        if len(windows) > 2:
            raise _OOMError("controlled allocation failure")
        ids = windows.reshape(-1, 1)
        return (ids,) if outputs == 1 else (ids, -ids)

    windows = np.arange(5, dtype=np.float32).reshape(5, 1, 1, 1)
    result, batch = forward_with_retry(torch.device("cpu"), encode, windows, 8)
    contract = json.loads(Path(__file__).with_name("oom_contract.json").read_text())[
        outputs - 1
    ]
    assert {
        "outputs": outputs,
        "calls": calls,
        "result": [r.tolist() for r in result],
        "batch_size": batch,
        "dtype": [str(r.dtype) for r in result],
    } == contract


def test_nested_failures_keep_every_window() -> None:
    """A slice that fails again shrinks the batch without skipping the windows after it."""

    def encode(windows: NDArray[np.float32]) -> tuple[NDArray[np.float32], ...]:
        """Fail on more than two windows, and on more than one once window 2 is involved."""
        ids = windows.ravel()
        if len(ids) > 2 or (len(ids) > 1 and 2 in ids):
            raise _OOMError("controlled allocation failure")
        return (windows.reshape(-1, 1),)

    windows = np.arange(6, dtype=np.float32).reshape(6, 1, 1, 1)
    (result,), batch = forward_with_retry(torch.device("cpu"), encode, windows, 8)
    assert result.ravel().tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert batch == 1


def test_single_window_failure_propagates() -> None:
    """Stop retrying and propagate the allocation error when one window cannot fit."""

    def fail(windows: NDArray[np.float32]) -> tuple[NDArray[np.float32], ...]:
        """Simulate an allocation error regardless of the requested batch size."""
        raise _OOMError("one window does not fit")

    with pytest.raises(_OOMError, match="one window does not fit"):
        forward_with_retry(
            torch.device("cpu"), fail, np.ones((1, 1, 1, 1), dtype=np.float32), 1
        )
