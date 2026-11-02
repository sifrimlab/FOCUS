"""Resolve usable CPUs and reclaim completed-stage CPU/GPU allocations."""

from collections.abc import Callable
from typing import cast

import os
import gc
import sys
import ctypes
import multiprocessing


def available_cpus() -> int:
    """Return the usable CPU count, preserving affinity and platform fallbacks."""
    try:
        # Linux: respects affinity (Slurm, cpuset cgroups, taskset)
        return len(cast(Callable[[int], set[int]], getattr(os, "sched_getaffinity"))(0))
    except AttributeError:
        # Non-Linux fallback
        return multiprocessing.cpu_count()


def release_memory(*, gpu: bool = True) -> None:
    """
    Reclaim memory left over from a finished pipeline stage.

    The pipeline already isolates stages (only file paths cross stage boundaries),
    so a stage's large objects are unreferenced by the time this is called — but
    nothing forces their collection or returns the freed pages to the OS. This
    helper does both, and is the single place that knowledge lives.

    Steps (all best-effort):
    1. ``gc.collect()`` — AnnData / MuData / pandas / torch routinely build
       reference cycles that frame-exit refcounting cannot free; only the cyclic
       collector reclaims them, and an idle process may not run it for a long time.
    2. ``torch.cuda.empty_cache()`` — only when torch is *already* imported (probed
       via ``sys.modules`` so a torch-free run never pays the import / CUDA-init
       cost) and a CUDA device is present. Returns the caching allocator's blocks.
    3. ``malloc_trim(0)`` — Linux/glibc only. This is the step that actually makes
       RSS fall in tools like ``htop``; without it the C allocator keeps freed
       pages. ``libc.so.6`` / ``malloc_trim`` do not exist on macOS or musl.

    Parameters
    ----------
    gpu : bool, keyword-only
            When False, skip the torch/CUDA probe (use at non-GPU stage boundaries).
    """
    gc.collect()

    if gpu:
        torch = sys.modules.get("torch")
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    if sys.platform.startswith("linux"):
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass
