"""Discover and decode microscopy images and their source physical pixel sizes."""

from __future__ import annotations
from typing import cast
from focus.preprocessing.microscopy.metadata import _SUPPORTED_EXTENSIONS
from focus.preprocessing.microscopy.metadata import _czi_pixel_size
from focus.preprocessing.microscopy.metadata import _nd2_pixel_size
from focus.preprocessing.microscopy.metadata import _tiff_page_pixel_size
from focus.preprocessing.microscopy.metadata import _valid_pixel_size
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.sample import MicroscopyImage

import os
import numpy as np
import tifffile
import czifile
import nd2

import focus.io.ome as ome_io
from focus.runtime.reporter_context import get_reporter


class ImageReader:
    """Discover and decode one microscopy sample with its physical source metadata."""

    def __init__(self, sample: MicroscopyImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    @staticmethod
    def _find_image_file(directory: str) -> str | None:
        """Find the first supported image file in a directory, by extension priority."""
        files = os.listdir(directory)
        for ext in _SUPPORTED_EXTENSIONS:
            for f in files:
                if f.lower().endswith(ext):
                    return os.path.join(directory, f)
        return None

    def _load_image(self, file: str) -> tuple[np.ndarray, np.dtype]:
        """
        Load an image from a supported file format.
        Returns a float32 array normalized to [0, 1] with shape (H, W, C),
        plus the original on-disk dtype (used to pick the output storage dtype).

        Dispatches to the appropriate loader based on file extension.
        """
        lower = file.lower()
        if lower.endswith(".czi"):
            return self._load_czi(file)
        elif lower.endswith(".nd2"):
            return self._load_nd2(file)
        elif lower.endswith(".qptiff"):
            return self._load_qptiff(file)
        else:
            return self._load_tiff(file)

    def _load_tiff(self, file: str) -> tuple[np.ndarray, np.dtype]:
        """
        Read a TIFF/OME-TIFF file and return as float32 [0,1] with channels last (H, W, C),
        plus the original on-disk dtype.
        """
        with tifffile.TiffFile(file) as f:
            image = f.asarray()

        input_dtype = image.dtype
        image = self._normalize_image(image)
        return image, input_dtype

    def _load_czi(self, file: str) -> tuple[np.ndarray, np.dtype]:
        """
        Read a CZI file and return as float32 [0,1] with channels last (H, W, C),
        plus the original on-disk dtype.
        """
        with czifile.CziFile(file) as czi:
            image = czi.asarray()

        input_dtype = image.dtype

        # Squeeze extra dimensions (CZI can have 5+ dims)
        if image.ndim > 3:
            if image.shape[0] > 1:
                get_reporter().warning(
                    "The CZI file has multiple scenes: only the first one is used"
                )
            while image.ndim > 3:
                image = image[0]

        image = self._normalize_image(image)
        return image, input_dtype

    def _load_nd2(self, file: str) -> tuple[np.ndarray, np.dtype]:
        """
        Read a Nikon ND2 file and return as float32 [0,1] with channels last (H, W, C),
        plus the original on-disk dtype.

        ND2 axes are named (T, P, Z, C, Y, X, S for RGB components), and nd2 omits
        singleton axes. Every axis other than Y, X, C and S is reduced to one plane:
        the middle plane of Z (the one most likely in focus), index 0 of any other
        (positions, time). When both C and S are present, the first channel is kept.
        Only the kept plane is read from disk.
        """
        with nd2.ND2File(file) as f:
            sizes = dict(f.sizes)
            is_rgb = "S" in sizes
            index: list[slice | int] = []
            kept_axes: list[str] = []
            reduced: list[tuple[str, int, int]] = []
            for axis, size in sizes.items():
                if axis in ("Y", "X", "S") or (axis == "C" and not is_rgb):
                    index.append(slice(None))
                    kept_axes.append(axis)
                else:
                    i = size // 2 if axis == "Z" else 0
                    index.append(i)
                    reduced.append((axis, size, i))
            image = np.asarray(f.to_dask()[tuple(index)].compute())

        if reduced:
            found = ", ".join(f"{axis}={size}" for axis, size, _ in reduced)
            used = ", ".join(f"{axis}={i}" for axis, _, i in reduced)
            get_reporter().warning(
                f"The ND2 file has extra axes ({found}): using {used}"
            )

        # Channels last (RGB components are already the last axis)
        if "C" in kept_axes:
            image = np.moveaxis(image, kept_axes.index("C"), -1)

        input_dtype = image.dtype
        image = self._normalize_image(image)
        return image, input_dtype

    def _load_qptiff(self, file: str) -> tuple[np.ndarray, np.dtype]:
        """
        Read a qpTIFF file and return as float32 [0,1] with channels last (H, W, C),
        plus the original on-disk dtype.

        qpTIFF files (e.g. Akoya Vectra/PhenoImager) embed the full-resolution image
        alongside a downsampled pyramid and small auxiliary images (thumbnail, macro,
        label). Since FOCUS recomputes its own pyramid on output, select the single
        series/level with the most pixels rather than trusting series order.
        """
        with tifffile.TiffFile(file) as f:
            level, n_levels = self._qptiff_best_level(f)
            if n_levels > 1:
                h, w = ome_io.hw_from_axes(level.shape, getattr(level, "axes", ""))
                get_reporter().detail(
                    f"qpTIFF file with {n_levels} candidate resolutions: using the highest ({h}x{w})"
                )
            image = level.asarray()

        input_dtype = image.dtype
        image = self._normalize_image(image)
        return image, input_dtype

    @staticmethod
    def _qptiff_best_level(f: tifffile.TiffFile) -> tuple[tifffile.TiffPageSeries, int]:
        """The series/level with the most pixels of an open qpTIFF, and the number of candidates."""
        levels = [level for series in f.series for level in series.levels]
        sizes = [
            ome_io.hw_from_axes(level.shape, getattr(level, "axes", ""))
            for level in levels
        ]
        best_index = max(range(len(levels)), key=lambda i: sizes[i][0] * sizes[i][1])
        return levels[best_index], len(levels)

    def _source_pixel_size_um(self, file: str) -> tuple[np.ndarray | None, str]:
        """
        Pixel size ``[x, y]`` in µm of the image that ``_load_image`` returns, and where it was read.

        Read from the source metadata: OME ``PhysicalSize``, the Aperio ``MPP``, the ImageJ unit
        with the resolution tags, the TIFF resolution tags (inch or centimetre units), the CZI
        ``Scaling`` distances or the calibrated ND2 voxel size. Values outside 0.01-100 µm are
        treated as placeholders. Never raises: an unreadable value is ``(None, "none")``.
        """
        lower = file.lower()
        try:
            if lower.endswith(".czi"):
                with czifile.CziFile(file) as czi:
                    size = _czi_pixel_size(czi.metadata(raw=True))
                return (size, "czi") if size is not None else (None, "none")
            if lower.endswith(".nd2"):
                with nd2.ND2File(file) as f:
                    size = _nd2_pixel_size(f)
                return (size, "nd2") if size is not None else (None, "none")
            if lower.endswith((".ome.tiff", ".ome.tif")):
                size = _valid_pixel_size(ome_io.read_ome_physical_size_um(file))
                if size is not None:
                    return size, "ome"
            with tifffile.TiffFile(file) as f:
                if lower.endswith(".qptiff"):
                    level, _ = self._qptiff_best_level(f)
                    page = getattr(level, "keyframe", None) or level
                else:
                    page = f.pages[0]
                size, source = _tiff_page_pixel_size(f, cast(tifffile.TiffPage, page))
            return (size, source) if size is not None else (None, "none")
        except Exception:
            return None, "none"

    @staticmethod
    def _normalize_image(image: np.ndarray) -> np.ndarray:
        """
        Common post-load normalization:
        1. Move channel axis to last if needed
        2. Convert to float32 [0, 1]
        3. Ensure (H, W, C) shape, clipping to 3 channels max
        """
        # Ensure 3D: (H, W) → (H, W, 1)
        if image.ndim == 2:
            image = image[:, :, np.newaxis]

        # Move smallest dimension to last (channel axis heuristic)
        if image.ndim == 3 and image.shape[2] > min(image.shape[0], image.shape[1]):
            channel_index = np.argmin(image.shape)
            if channel_index == 0:
                image = image.transpose(1, 2, 0)
            elif channel_index == 1:
                image = image.transpose(0, 2, 1)

        # Convert to float32 and normalize to [0, 1]
        if image.dtype == np.float32 and image.max() <= 1.0:
            pass  # Already normalized float32
        else:
            max_val = (
                np.iinfo(image.dtype).max
                if np.issubdtype(image.dtype, np.integer)
                else image.max()
            )
            image = image.astype(np.float32)
            if max_val > 0:
                image /= float(max_val)

        # Clip to max 3 channels
        if image.shape[2] > 3:
            image = image[:, :, :3]

        return image
