"""Detect microscopy tissue on a bounded detection canvas with original threshold rules."""

from __future__ import annotations
from typing import cast
from typing import TYPE_CHECKING
import focus.preprocessing.microscopy.settings as _settings

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.sample import MicroscopyImage

import numpy as np
import cv2
import skimage.morphology as morphology
from scipy.ndimage import binary_fill_holes

from focus.runtime.reporter_context import get_reporter


class TissueMask:
    """Compute and refine the tissue mask on the bounded microscopy detection canvas."""

    def __init__(self, sample: MicroscopyImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    @staticmethod
    def _as_detection_rgb(image_uint8: np.ndarray) -> np.ndarray:
        """Coerce a uint8 (H, W, C) array to 3 channels so cv2's RGB->gray conversion accepts it.

        Only the tissue mask is computed from the result, so replicating/padding channels is
        harmless. Follows the same convention as the alignment GUI's display payload
        (``alignment._image_to_rgb_uint8``) so the mask is derived from the same composite the
        user sees when aligning: 1 channel is replicated to RGB, 2 channels gain a zero third
        channel. A 3-channel array is returned unchanged (same object, no copy).
        """
        c = image_uint8.shape[2]
        if c == 1:
            return np.repeat(image_uint8, 3, axis=2)
        if c == 2:
            pad = np.zeros((*image_uint8.shape[:2], 1), dtype=image_uint8.dtype)
            return np.concatenate([image_uint8, pad], axis=2)
        return image_uint8

    @staticmethod
    def _has_dark_background(gray: np.ndarray) -> bool:
        """True when `gray`'s background is darker than its content (fluorescence-like).

        The border frame of a slide scan is background, so its median is compared against the
        median of the whole image. Ties (a scan filled edge to edge with tissue) report False,
        which keeps the bright-background handling below.
        """
        h, w = gray.shape[:2]
        k = max(1, round(0.05 * min(h, w)))
        border = np.concatenate(
            [
                gray[:k, :].ravel(),
                gray[-k:, :].ravel(),
                gray[:, :k].ravel(),
                gray[:, -k:].ravel(),
            ]
        )
        return float(np.median(border)) < float(np.median(gray))

    def _detect_tissue_mask(
        self,
        image: np.ndarray,
        min_object_coverage: float = 0.01,
        clip_percentile: int = 99,
    ) -> np.ndarray:
        """
        Segment tissue vs. background (Otsu-based) on `image` at its current resolution,
        preserving tissue areas larger than image_area * min_object_coverage.

        Blur kernel size and speck-removal size are fixed relative to the detection canvas
        (_DETECTION_BLUR_KERNEL_SIZE / _DETECTION_MAX_OBJECT_SIZE) rather than configurable,
        since `image` here is always already at or below the ~3000px detection canvas size.

        Everything from the percentile clip onwards expects tissue to be the *bright* class.
        Brightfield input reaches that state by inversion; an image with a dark background
        (only detected for 1-/2-channel input, see below) is already in it.

        Parameters
        ----------
        image : np.ndarray
                Input float32 image of shape (H, W, C) in [0, 1]. C may be 1, 2 or 3; fewer than
                3 channels are promoted to RGB for the grayscale conversion only.
        min_object_coverage : float
                Minimum tissue area relative to image area.
        clip_percentile : int
                Percentile for intensity clipping before thresholding.

        Returns
        -------
        np.ndarray
                Boolean tissue mask, same (H, W) as `image`.
        """
        # Convert to uint8 for mask computation (avoids float intermediaries)
        image_uint8 = (image * 255).astype(np.uint8)

        # Promote to 3 channels for cv2's RGB->gray conversion. Done before the black-pixel guard
        # below, which is equivalent to doing it after: replicated/padded channels are zero exactly
        # where the source channels are, so `np.all(... == 0)` selects the same pixels either way.
        promoted = image_uint8.shape[2] != 3
        image_uint8 = self._as_detection_rgb(image_uint8)

        # Polarity: brightfield scans have a bright background and are inverted below so tissue
        # becomes the bright class. A promoted (1-/2-channel) image may instead be fluorescence-like,
        # with tissue already bright on a dark background - inverting that would segment the
        # background instead. Probed only for promoted input, so 3-channel behaviour (and its single
        # grayscale conversion) is unchanged; when the background is dark this grayscale is final and
        # is reused as is.
        gray = cv2.cvtColor(image_uint8, cv2.COLOR_RGB2GRAY) if promoted else None
        dark_background = promoted and self._has_dark_background(cast(np.ndarray, gray))

        if not dark_background:
            # Replace black pixels with white to avoid thresholding artifacts
            black_pixels = np.all(image_uint8 == 0, axis=-1)
            image_uint8[black_pixels] = 255

            # Grayscale → invert (white bg becomes black)
            gray = cv2.cvtColor(image_uint8, cv2.COLOR_RGB2GRAY)
            gray = cv2.bitwise_not(gray)
        del image_uint8  # Free the uint8 RGB copy

        # Clip at percentile to reduce oversaturation impact
        clip_value = np.percentile(cast(np.ndarray, gray), clip_percentile)
        blurred = cv2.GaussianBlur(
            np.clip(cast(np.ndarray, gray), None, clip_value).astype(np.uint8),
            (
                _settings._DETECTION_BLUR_KERNEL_SIZE,
                _settings._DETECTION_BLUR_KERNEL_SIZE,
            ),
            0,
        )

        # Otsu threshold on blurred, apply to original grayscale
        otsu_thresh, _ = cv2.threshold(
            blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        del blurred
        _, thresh = cv2.threshold(
            cast(np.ndarray, gray), int(otsu_thresh), 255, cv2.THRESH_BINARY
        )
        del gray

        # Morphological cleanup
        mask_clean = morphology.remove_small_objects(
            thresh.astype(bool), max_size=_settings._DETECTION_MAX_OBJECT_SIZE
        )
        del thresh
        segmentation_mask = binary_fill_holes(mask_clean)
        del mask_clean

        # Refine with contour-based area filtering
        seg_uint8 = segmentation_mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(
            seg_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        if contours:
            image_area = seg_uint8.shape[0] * seg_uint8.shape[1]
            area_threshold = min_object_coverage * image_area
            large_contours = [
                c for c in contours if cv2.contourArea(c) >= area_threshold
            ]

            tissue_mask = np.zeros_like(seg_uint8)
            cv2.drawContours(
                tissue_mask,
                large_contours,
                contourIdx=-1,
                color=255,
                thickness=cv2.FILLED,
            )
            segmentation_mask = tissue_mask.astype(bool)
        else:
            get_reporter().warning(
                "No contours found: the background mask is not refined"
            )
        del seg_uint8

        return segmentation_mask

    def _compute_tissue_mask(
        self,
        image: np.ndarray,
        min_object_coverage: float = 0.01,
        clip_percentile: int = 99,
    ) -> tuple[np.ndarray, float]:
        """
        Detect the tissue mask on a downsampled proxy for speed on very large images.

        Segmentation (Gaussian blur, Otsu threshold, morphological cleanup) is run on a
        proxy capped at _DETECTION_MAX_PIXELS rather than the full-resolution image, since
        a tissue/background boundary is a smooth, low-frequency shape that doesn't need
        full-resolution input to locate accurately.

        Returns
        -------
        tuple of (mask, scale)
                mask : boolean tissue mask at `scale` resolution relative to `image`.
                scale : proxy_size / full_size, in (0, 1]. 1.0 means no downsampling occurred.
        """
        import math

        H, W = image.shape[:2]
        scale = min(1.0, math.sqrt(_settings._DETECTION_MAX_PIXELS / (H * W)))
        if scale < 1.0:
            proxy = cv2.resize(
                image,
                (max(1, round(W * scale)), max(1, round(H * scale))),
                interpolation=cv2.INTER_AREA,
            )
            if proxy.ndim == 2:
                proxy = proxy[..., np.newaxis]
        else:
            proxy = image
        mask = self._detect_tissue_mask(proxy, min_object_coverage, clip_percentile)
        return mask, scale
