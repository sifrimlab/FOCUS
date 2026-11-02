"""Probe available GPU memory and choose the encoder batch size."""

from numpy.typing import NDArray

from collections.abc import Callable
from typing import cast
import numpy as np
import torch

_PROBE_BATCH = 8
_MAX_BATCH = 512
_MAX_BATCH_PIXELS = 512 * 224 * 224
_SAFETY_FRACTION = 0.8
_CPU_BATCH = 32
_OOMError = getattr(torch.cuda, "OutOfMemoryError", RuntimeError)


def estimate_batch_size(
    device: torch.device,
    windows: NDArray[np.float32],
    member: NDArray[np.bool_] | None,
    encode_np: Callable[[NDArray[np.float32]], NDArray[np.float32]],
    encode_readouts: Callable[
        [NDArray[np.float32], NDArray[np.bool_]], NDArray[np.float32]
    ],
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
            if member is None:
                encode_np(trial_w)
            else:
                encode_readouts(trial_w, np.repeat(member[:1], probe, axis=0))
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
    encode: Callable[
        [NDArray[np.float32], NDArray[np.bool_] | None], NDArray[np.float32]
    ],
    windows: NDArray[np.float32],
    member: NDArray[np.bool_] | None,
    batch_size: int,
) -> tuple[NDArray[np.float32], int]:
    """Encode a batch, halving its working size on CUDA OOM and retaining output order.

    Parameters
    ----------
    device : torch.device
        Device whose cached allocations are released after an out-of-memory error.
    encode : callable
        Forward operation for float32 (B, H, W, 3) windows and optional boolean
        (B, R, T) memberships; returns float32 CPU embeddings.
    windows : np.ndarray
        Host windows, sliced without mutation during retry.
    member : np.ndarray or None
        Readout memberships, sliced alongside windows; None selects plain CLS.
    batch_size : int
        Current working batch limit, retained or reduced for the caller's next batch.

    Returns
    -------
    tuple[NDArray[np.float32], int]
        Ordered embeddings and the final working batch size.
    """
    try:
        return (encode(windows, member)), batch_size
    except _OOMError:
        if device.type == "cuda":
            torch.cuda.empty_cache()
        new_bs = max(1, min(batch_size, len(windows)) // 2)
        if len(windows) <= 1:
            # Can't shrink any further: re-raise rather than spin forever.
            raise
        outs = []
        for s in range(0, len(windows), new_bs):
            part = None if member is None else member[s : s + new_bs]
            emb, new_bs = forward_with_retry(
                device, encode, windows[s : s + new_bs], part, new_bs
            )
            outs.append(emb)
        return np.concatenate(outs, axis=0), new_bs
