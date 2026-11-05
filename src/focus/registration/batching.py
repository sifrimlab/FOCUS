"""Probe available GPU memory and choose the encoder batch size."""

from numpy.typing import NDArray

from collections.abc import Callable
from typing import TypeAlias, cast
import numpy as np
import torch

_PROBE_BATCH = 8
_MAX_BATCH = 512
_MAX_BATCH_PIXELS = 512 * 224 * 224
_SAFETY_FRACTION = 0.8
_CPU_BATCH = 32
_OOMError = getattr(torch.cuda, "OutOfMemoryError", RuntimeError)

# Per-window encoder outputs: arrays whose first axis is the window.
Encoded: TypeAlias = tuple[NDArray[np.float32], ...]
Encoder: TypeAlias = Callable[[NDArray[np.float32]], Encoded]


def estimate_batch_size(
    device: torch.device, windows: NDArray[np.float32], encode: Encoder
) -> int:
    """
    Conservatively choose a batch size for windows shaped like ``windows[0]``.

    Runs one forward pass on copies of a representative window (through the exact path
    real batches use) to measure the per-window activation cost, then sizes the batch to
    ``_SAFETY_FRACTION`` of the memory actually available (free device memory plus torch's
    reusable cache). The batch is also capped by ``_MAX_BATCH_PIXELS`` so host memory
    stays flat for large windows.
    """
    h, w = windows.shape[1:3]
    pixel_cap = max(1, _MAX_BATCH_PIXELS // (h * w))
    if device.type != "cuda":
        return cast(int, max(1, min(_CPU_BATCH, pixel_cap)))

    torch.cuda.empty_cache()
    probe = min(_PROBE_BATCH, pixel_cap)
    while True:
        torch.cuda.reset_peak_memory_stats(device)
        base = torch.cuda.memory_allocated(device)  # resident model weights
        trial_w = np.repeat(windows[:1], probe, axis=0)
        try:
            encode(trial_w)
            break
        except _OOMError:
            torch.cuda.empty_cache()
            if probe == 1:
                raise
            probe = max(1, probe // 2)
    peak = torch.cuda.max_memory_allocated(device)

    per_window = max(1.0, (peak - base) / probe)
    free, _ = torch.cuda.mem_get_info(device)
    reusable = torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device)
    batch = int(_SAFETY_FRACTION * (free + reusable) / per_window)
    batch = max(1, min(_MAX_BATCH, pixel_cap, batch))
    if batch >= 16:
        batch -= batch % 8  # tensor-core friendly
    torch.cuda.empty_cache()
    return cast(int, batch)


def forward_with_retry(
    device: torch.device,
    encode: Encoder,
    windows: NDArray[np.float32],
    batch_size: int,
) -> tuple[Encoded, int]:
    """Encode a batch, halving its working size on CUDA OOM and retaining output order.

    Parameters
    ----------
    device : torch.device
        Device whose cached allocations are released after an out-of-memory error.
    encode : callable
        Forward operation for float32 (B, H, W, 3) windows; returns a tuple of float32 CPU
        arrays whose first axis is the window.
    windows : np.ndarray
        Host windows, sliced without mutation during retry.
    batch_size : int
        Current working batch limit, retained or reduced for the caller's next batch.

    Returns
    -------
    tuple[Encoded, int]
        Every output concatenated in window order, and the final working batch size.
    """
    try:
        return encode(windows), batch_size
    except _OOMError:
        if device.type == "cuda":
            torch.cuda.empty_cache()
        if len(windows) <= 1:
            # Can't shrink any further: re-raise rather than spin forever.
            raise
        new_bs = max(1, min(batch_size, len(windows)) // 2)
        parts: list[Encoded] = []
        start = 0
        while start < len(windows):
            # A slice that fails again lowers new_bs; the next slice starts where this one ended.
            part = windows[start : start + new_bs]
            out, new_bs = forward_with_retry(device, encode, part, new_bs)
            parts.append(out)
            start += len(part)
        return tuple(np.concatenate(p, axis=0) for p in zip(*parts)), new_bs
