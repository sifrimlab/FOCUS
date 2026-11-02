"""Bind one microscopy source to its preparation options, cache key and output writer."""

from focus.preprocessing.microscopy.parameters import ImageSettings
from focus.preprocessing.microscopy.workflow import process_image

from typing import cast

from functools import cached_property

from focus.preprocessing.microscopy.reader import ImageReader
from focus.preprocessing.microscopy.pyramid import ImagePyramid
from focus.preprocessing.microscopy.tissue import TissueMask
from focus.preprocessing.microscopy.crop import ImageCrop
import focus.preprocessing.microscopy.settings as _settings
import os
import numpy as np
import cv2
import tifffile
import scipy
import skimage

from focus.runtime.reporter_context import get_reporter
from focus.config.options import SegmentationBackgroundColor
from focus.io.paths import preprocessed_file
from focus.preprocessing.base import SampleSource
from focus.io.provenance import (
    cached_fingerprint,
    file_signature,
    fingerprint,
)
from focus.preprocessing.microscopy.metadata import (
    METHOD_VERSION,
    _SUPPORTED_EXTENSIONS,
)


class MicroscopyImage(SampleSource):
    """
    Process a microscopy image to uniform the format, enhance colors,
    and prepare it for alignment/registration.

    Input: TIFF (.tiff, .tif, .ome.tiff, .ome.tif), qpTIFF (.qptiff), CZI (.czi), or ND2 (.nd2)
    Output: multi-resolution OME-TIFF, stored in the source file's dtype
    (uint8/uint16 passthrough, float sources stay float32), zlib-compressed with predictor.
    """

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Create sample output directories, then resolve a supported microscopy source.

        Parameters
        ----------
        source_path : str
            Existing readable dataset root.
        sample_id : str
            Sample directory identifier.
        modality_name : str
            Microscopy input subdirectory and processed-output name.

        Raises
        ------
        FileNotFoundError
            If the sample modality directory contains no supported microscopy image."""
        super().__init__(source_path, sample_id, modality_name)

        input_dir = os.path.join(source_path, sample_id, modality_name)
        filename = self.reader._find_image_file(input_dir)
        self.filename: str = cast(str, filename)
        if filename is None:
            raise FileNotFoundError(
                f"No supported image file ({', '.join(_SUPPORTED_EXTENSIONS)}) found in {input_dir}"
            )

    def preview_image(self) -> np.ndarray:
        """Load and return a preview of the microscopy image as float32 (H, W, C)."""
        image, _ = self.reader._load_image(self.filename)
        return image

    def process_image(
        self,
        color_enhancement: bool = True,
        remove_background: bool = True,
        crop_to_tissue: bool = True,
        background_color: str = SegmentationBackgroundColor.WHITE,
        min_object_coverage: float = 0.01,
        force_recomputing: bool = False,
        clip_percentile: int = _settings._CLIP_PERCENTILE,
        crop_margin: int = _settings._CROP_MARGIN_PX,
        gamma: float = _settings._GAMMA,
        contrast_saturation: float = _settings._CONTRAST_SATURATION,
    ) -> str:
        """
        Preprocess a microscopy image: load, enhance, remove background, crop, save as OME-TIFF.

        Parameters
        ----------
        color_enhancement : bool
                Whether to enhance colors using gamma correction and contrast stretching.
        remove_background : bool
                Whether to remove the background using Otsu thresholding.
        crop_to_tissue : bool
                Whether to crop the image to the tissue bounding box.
        background_color : SegmentationBackgroundColor
                Color to fill the background with after removal.
        min_object_coverage : float
                Minimum tissue area fraction to keep during background removal (0-1).
        force_recomputing : bool
                Whether to force recomputation even if the output's fingerprint matches.
        clip_percentile : int
                Percentile for intensity clipping before thresholding.
        crop_margin : int
                Pixel margin around the tissue bounding box when cropping.
        gamma : float
                Gamma value for gamma correction (< 1 brightens, > 1 darkens).
        contrast_saturation : float
                Percentage of pixels to saturate during contrast stretching.

        Returns
        -------
        str
                Path to the output OME-TIFF file.
        """

        output_ome_tiff = preprocessed_file(
            self.source_path, self.sample_id, self.modality_name, "ome.tiff"
        )

        reporter = getattr(self, "_step_reporter", None) or get_reporter()

        settings = ImageSettings(
            color_enhancement,
            remove_background,
            crop_to_tissue,
            background_color,
            min_object_coverage,
            clip_percentile,
            crop_margin,
            gamma,
            contrast_saturation,
        )
        key = self._fingerprint(settings.fingerprint_settings())
        if not force_recomputing and cached_fingerprint(output_ome_tiff) == key:
            reporter.cached()
            return output_ome_tiff
        previous = self.pyramid._previous_output(output_ome_tiff)

        process_image(self, settings, reporter, output_ome_tiff, key, previous)
        return output_ome_tiff

    def _fingerprint(self, settings: dict[str, object]) -> str:
        """Preprocessing fingerprint: method, source file identity, sample, settings, constants and software."""
        return fingerprint(
            {
                "method": METHOD_VERSION,
                "input": file_signature(self.filename),
                "sample_id": self.sample_id,
                "modality": self.modality_name,
                "settings": settings,
                "constants": {
                    "max_pyramid_pixels": _settings._MAX_PYRAMID_PIXELS,
                    "detection_max_pixels": _settings._DETECTION_MAX_PIXELS,
                    "detection_blur_kernel_size": _settings._DETECTION_BLUR_KERNEL_SIZE,
                    "detection_max_object_size": _settings._DETECTION_MAX_OBJECT_SIZE,
                },
                "software": {
                    "numpy": np.__version__,
                    "opencv": getattr(cv2, "__version__"),
                    "scikit-image": getattr(skimage, "__version__"),
                    "scipy": getattr(scipy, "__version__"),
                    "tifffile": tifffile.__version__,
                },
            }
        )

    @cached_property
    def reader(self) -> ImageReader:
        """Return the ImageReader component bound to this sample."""
        return ImageReader(self)

    @cached_property
    def pyramid(self) -> ImagePyramid:
        """Return the ImagePyramid component bound to this sample."""
        return ImagePyramid(self)

    @cached_property
    def tissue(self) -> TissueMask:
        """Return the TissueMask component bound to this sample."""
        return TissueMask(self)

    @cached_property
    def crop(self) -> ImageCrop:
        """Return the ImageCrop component bound to this sample."""
        return ImageCrop(self)
