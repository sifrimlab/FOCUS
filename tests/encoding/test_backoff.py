"""Characterize recursive encoder backoff without allocating GPU memory."""

import json
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

torch = pytest.importorskip("torch")

from focus.registration.batching import _OOMError, forward_with_retry  # noqa: E402


@pytest.mark.parametrize("masked", [False, True])
def test_backoff_matches_baseline(masked: bool) -> None:
    """Retain original slice order, matching memberships and the reduced batch limit."""
    calls: list[dict[str, object]] = []

    def encode(
        windows: NDArray[np.float32], member: NDArray[np.bool_] | None
    ) -> NDArray[np.float32]:
        """Record aligned input slices and simulate failure for batches larger than two."""
        calls.append(
            {
                "ids": windows.ravel().tolist(),
                "member": None if member is None else member.ravel().tolist(),
            }
        )
        if len(windows) > 2:
            raise _OOMError("controlled allocation failure")
        return windows.reshape(-1, 1)

    windows = np.arange(5, dtype=np.float32).reshape(5, 1, 1, 1)
    member = np.arange(5).reshape(5, 1, 1).astype(bool) if masked else None
    result, batch = forward_with_retry(torch.device("cpu"), encode, windows, member, 8)
    contract = json.loads(Path(__file__).with_name("oom_contract.json").read_text())[
        int(masked)
    ]
    assert {
        "masked": masked,
        "calls": calls,
        "result": result.tolist(),
        "batch_size": batch,
        "dtype": str(result.dtype),
    } == contract


def test_single_window_failure_propagates() -> None:
    """Stop retrying and propagate the allocation error when one window cannot fit."""

    def fail(
        windows: NDArray[np.float32], member: NDArray[np.bool_] | None
    ) -> NDArray[np.float32]:
        """Simulate an allocation error regardless of the requested batch size."""
        raise _OOMError("one window does not fit")

    with pytest.raises(_OOMError, match="one window does not fit"):
        forward_with_retry(
            torch.device("cpu"), fail, np.ones((1, 1, 1, 1), dtype=np.float32), None, 1
        )
