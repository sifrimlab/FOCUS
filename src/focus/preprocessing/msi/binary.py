"""Validate IBD spectrum ranges and identify non-finite binary payload rows."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
import mmap
import os
import tqdm
import concurrent.futures

import focus.runtime.resources as resources
from focus.config.keys import MsiMetadata


class BinaryValidator:
    """Check IBD ranges and finite spectrum values before downstream sample processing."""

    def __init__(self, sample: MsiSample) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def _validate_binary_data(self, mode: str) -> set[int]:
        """Validate the IBD binary file for *mode* before any processing begins.

        Performs two checks:

        1. **File-size check** – raises ``ValueError`` if the IBD file is smaller
           than the byte range implied by all (offset + count × itemsize) pairs in
           the imzML.  A truncated file is unrecoverable.

        2. **Full NaN / Inf scan** – reads every spectrum via memory-mapped I/O and
           flags those containing non-finite m/z or intensity values.  Corrupted
           spectra are returned as a set of indices so the caller can filter them
           out rather than aborting.

        Returns
        -------
        set[int]
            Indices of spectra that contain NaN/Inf values.  Empty when all
            spectra are clean.
        """
        ibd_path = self.sample._binary_files[mode]
        meta = self.sample._metadata[mode]
        mz_meta = meta[MsiMetadata.MZ_BINARY_METADATA]  # shape (n_spectra, 3)
        int_meta = meta[MsiMetadata.INTENSITIES_BINARY_METADATA]  # shape (n_spectra, 3)
        # columns: [0] = n_values (count), [1] = encoded_length (bytes), [2] = byte offset
        mz_dtype = meta[MsiMetadata.MZ_DTYPE]
        int_dtype = meta[MsiMetadata.INTENSITIES_DTYPE]
        n_spectra: int = mz_meta.shape[0]
        mz_itemsize: int = np.dtype(mz_dtype).itemsize
        int_itemsize: int = np.dtype(int_dtype).itemsize

        # ── 1. File-size check ─────────────────────────────────────────────────
        actual_size = os.path.getsize(ibd_path)

        # The minimum required file size is the highest byte address that any
        # spectrum occupies: max over all spectra of (offset + count × itemsize).
        last_mz_byte = int(np.max(mz_meta[:, 2] + mz_meta[:, 0] * mz_itemsize))
        last_int_byte = int(np.max(int_meta[:, 2] + int_meta[:, 0] * int_itemsize))
        required_size = max(last_mz_byte, last_int_byte)

        if actual_size < required_size:
            missing = required_size - actual_size
            raise ValueError(
                f"[Sample '{self.sample.sample_id}' – {str(mode).upper()} mode] "
                f"IBD file is truncated: {actual_size:,} bytes on disk, "
                f"{required_size:,} bytes required for {n_spectra:,} spectra "
                f"({missing:,} bytes missing, {missing / required_size * 100:.1f}% absent).\n"
                f"  File: {ibd_path}"
            )

        # ── 2. Full NaN / Inf scan ─────────────────────────────────────────────
        # Read every spectrum via memory-mapped I/O and validate with np.isfinite.
        # Each worker thread opens its own mmap so file reads are truly parallel.
        # np.frombuffer creates zero-copy views — no extra allocation per spectrum.

        n_workers = resources.available_cpus() or 1
        chunk_size = max(1, -(-n_spectra // n_workers))  # ceiling division
        chunks = [
            range(i, min(i + chunk_size, n_spectra))
            for i in range(0, n_spectra, chunk_size)
        ]

        def _check_chunk(spec_range: range) -> tuple[dict[int, int], dict[int, int]]:
            """Scan the assigned IBD spectrum ranges and return non-finite value counts."""
            chunk_bad_mz: dict[int, int] = {}
            chunk_bad_int: dict[int, int] = {}
            with open(ibd_path, "rb") as fh:
                mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
                try:
                    for idx in spec_range:
                        count_mz = int(mz_meta[idx, 0])
                        if count_mz > 0:
                            arr = np.frombuffer(
                                mm,
                                dtype=mz_dtype,
                                count=count_mz,
                                offset=int(mz_meta[idx, 2]),
                            )
                            n_bad = int(np.sum(~np.isfinite(arr)))
                            del arr  # release mmap reference before next read
                            if n_bad:
                                chunk_bad_mz[idx] = n_bad

                        count_int = int(int_meta[idx, 0])
                        if count_int > 0:
                            arr = np.frombuffer(
                                mm,
                                dtype=int_dtype,
                                count=count_int,
                                offset=int(int_meta[idx, 2]),
                            )
                            n_bad = int(np.sum(~np.isfinite(arr)))
                            del arr  # release mmap reference before next read
                            if n_bad:
                                chunk_bad_int[idx] = n_bad
                finally:
                    mm.close()
            return chunk_bad_mz, chunk_bad_int

        bad_mz: dict[int, int] = {}
        bad_int: dict[int, int] = {}

        with tqdm.tqdm(
            total=n_spectra,
            desc=f"Validating IBD ({str(mode).upper()}, {self.sample.sample_id})",
            unit="spec",
        ) as pbar:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=n_workers
            ) as executor:
                futures = {
                    executor.submit(_check_chunk, chunk): chunk for chunk in chunks
                }
                for future in concurrent.futures.as_completed(futures):
                    bm, bi = future.result()
                    bad_mz.update(bm)
                    bad_int.update(bi)
                    pbar.update(len(futures[future]))

        return set(bad_mz.keys()) | set(bad_int.keys())
