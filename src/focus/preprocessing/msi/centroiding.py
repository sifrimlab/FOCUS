"""Stream the peak picking of a profile IBD into a scratch IBD of centroids."""

from __future__ import annotations

import collections
import concurrent.futures
import mmap
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

import numpy as np
import tqdm

import focus.runtime.resources as resources
from focus.io.atomic import atomic_path
from focus.preprocessing.msi.binary import _read
from focus.preprocessing.msi.peak_stats import GridModel
from focus.preprocessing.msi.picking import centroid_spectrum
from focus.preprocessing.msi.settings import _PICK_BATCH_POINTS

# Dtypes of the scratch IBD: each spectrum is its m/z array followed by its heights.
CENTROID_MZ_DTYPE = np.float64
CENTROID_INTENSITY_DTYPE = np.float32

Picked = list[tuple[np.ndarray, np.ndarray] | None]


@dataclass(frozen=True)
class CentroidResult:
    """Ranges of the centroids in the scratch IBD and the spectra that could not be picked."""

    mz_meta: np.ndarray
    intensity_meta: np.ndarray
    corrupt: set[int]
    mean_peaks: float


def _batches(counts: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive spectrum ranges of at most ``_PICK_BATCH_POINTS`` source points (one spectrum at least)."""
    batches: list[tuple[int, int]] = []
    start = points = 0
    for i, count in enumerate(counts):
        if i > start and points + int(count) > _PICK_BATCH_POINTS:
            batches.append((start, i))
            start, points = i, 0
        points += int(count)
    if counts.size:
        batches.append((start, counts.size))
    return batches


def _shared_axis(
    path: str, mz_dtype: type[np.generic], mz_meta: np.ndarray
) -> np.ndarray | None:
    """The ascending m/z axis every spectrum shares (continuous imzML), None when they have their own."""
    if mz_meta.shape[0] == 0:
        return None
    if np.unique(mz_meta[:, 2]).size != 1 or np.unique(mz_meta[:, 0]).size != 1:
        return None
    with open(path, "rb") as stream:
        mm = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            axis = np.array(_read(mm, mz_dtype, mz_meta[0]), dtype=np.float64)
        finally:
            mm.close()
    if not np.isfinite(axis).all() or (np.diff(axis) < 0).any():
        return None
    return axis


@dataclass(frozen=True)
class _Source:
    """The profile IBD and what every worker needs to pick from it."""

    path: str
    mz_dtype: type[np.generic]
    int_dtype: type[np.generic]
    mz_meta: np.ndarray
    int_meta: np.ndarray
    grid: GridModel
    shared: np.ndarray | None


def _pick_batch(source: _Source, start: int, stop: int) -> Picked:
    """Pick spectra ``start`` to ``stop``; None marks a spectrum that cannot be picked."""
    picked: Picked = []
    with open(source.path, "rb") as stream:
        mm = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            for i in range(start, stop):
                y = _read(mm, source.int_dtype, source.int_meta[i])
                mz = source.shared
                if mz is None:
                    mz = _read(mm, source.mz_dtype, source.mz_meta[i])
                    finite = bool(np.isfinite(mz).all())
                else:
                    finite = True
                if mz.size != y.size or not finite or not np.isfinite(y).all():
                    picked.append(None)
                else:
                    picked.append(centroid_spectrum(mz, y, source.grid))
                del mz, y
        finally:
            mm.close()
    return picked


def _picked_in_order(source: _Source, workers: int) -> Iterator[Picked]:
    """Batches picked by ``workers`` threads, yielded in spectrum order through a bounded window.

    The kernel releases the GIL and each batch maps the file itself; at most ``2 * workers``
    batches are in flight, so memory does not grow with the file.
    """
    queue = iter(_batches(source.mz_meta[:, 0]))
    pending: collections.deque[concurrent.futures.Future[Picked]] = collections.deque()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)

    def submit() -> None:
        """Queue the next batch, if any."""
        batch = next(queue, None)
        if batch is not None:
            pending.append(pool.submit(_pick_batch, source, *batch))

    try:
        for _ in range(2 * workers):
            submit()
        while pending:
            picked = pending.popleft().result()
            submit()
            yield picked
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


class _ScratchWriter:
    """Append picked spectra in order to the scratch stream and record their ranges."""

    _MZ_SIZE = np.dtype(CENTROID_MZ_DTYPE).itemsize
    _INTENSITY_SIZE = np.dtype(CENTROID_INTENSITY_DTYPE).itemsize

    def __init__(self, stream: BinaryIO, n_spectra: int) -> None:
        """Start an empty file for ``n_spectra`` spectra."""
        self.stream = stream
        self.mz_meta = np.zeros((n_spectra, 3), dtype=np.int64)
        self.intensity_meta = np.zeros((n_spectra, 3), dtype=np.int64)
        self.corrupt: set[int] = set()
        self.index = 0
        self.offset = 0

    def add(self, picked: Picked) -> None:
        """Write the next spectra; a None leaves an empty spectrum and is reported as corrupt."""
        for result in picked:
            if result is None:
                self.corrupt.add(self.index)
                result = (
                    np.empty(0, dtype=CENTROID_MZ_DTYPE),
                    np.empty(0, dtype=CENTROID_INTENSITY_DTYPE),
                )
            mz, height = result
            self.mz_meta[self.index] = (mz.size, mz.size * self._MZ_SIZE, self.offset)
            self.stream.write(mz.tobytes())
            self.offset += mz.size * self._MZ_SIZE
            self.intensity_meta[self.index] = (
                height.size,
                height.size * self._INTENSITY_SIZE,
                self.offset,
            )
            self.stream.write(height.tobytes())
            self.offset += height.size * self._INTENSITY_SIZE
            self.index += 1

    def result(self) -> CentroidResult:
        """The recorded ranges, the corrupt spectra and the mean number of peaks per picked spectrum."""
        picked = self.mz_meta.shape[0] - len(self.corrupt)
        mean = float(self.mz_meta[:, 0].sum()) / max(picked, 1)
        return CentroidResult(self.mz_meta, self.intensity_meta, self.corrupt, mean)


def centroid_profile(
    path: str,
    mz_dtype: type[np.generic],
    int_dtype: type[np.generic],
    mz_meta: np.ndarray,
    int_meta: np.ndarray,
    grid: GridModel,
    scratch: str,
    description: str,
) -> CentroidResult:
    """Pick every spectrum of a profile IBD and write the centroids to the scratch IBD.

    The scratch bytes are written in spectrum order whatever the number of workers, through a
    temporary sibling renamed on success, so a killed run never leaves a file that looks complete.
    """
    n = mz_meta.shape[0]
    source = _Source(
        path,
        mz_dtype,
        int_dtype,
        mz_meta,
        int_meta,
        grid,
        _shared_axis(path, mz_dtype, mz_meta),
    )
    with atomic_path(scratch) as tmp, open(tmp, "wb") as stream:
        writer = _ScratchWriter(stream, n)
        with tqdm.tqdm(total=n, desc=description, unit="spec") as bar:
            for picked in _picked_in_order(source, resources.available_cpus() or 1):
                writer.add(picked)
                bar.update(len(picked))
    return writer.result()
