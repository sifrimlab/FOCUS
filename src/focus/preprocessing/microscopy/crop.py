"""Apply low-resolution tissue masks and source-pixel crop offsets to microscopy images."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from focus.preprocessing.microscopy.sample import MicroscopyImage

import numpy as np
import cv2

from focus.config.options import SegmentationBackgroundColor


class ImageCrop:
    """Mask background and crop microscopy pixels using a shared detection-scale tissue mask."""

    def __init__(self, sample: MicroscopyImage) -> None:
        """Bind the component to its existing sample state."""
        self.sample = sample

    def _remove_background(
        self,
        image: np.ndarray,
        mask: np.ndarray,
        scale: float,
        background_color: str = SegmentationBackgroundColor.WHITE,
    ) -> np.ndarray:
        """
        Apply a tissue mask to a full-resolution image, filling non-tissue pixels with
        the background color.

        Parameters
        ----------
        image : np.ndarray
                Input float32 image of shape (H, W, C) in [0, 1].
        mask : np.ndarray
                Boolean tissue mask at `scale` resolution relative to `image` (see `_compute_tissue_mask`).
        scale : float
                proxy_size / full_size for `mask`, in (0, 1]. 1.0 means `mask` already matches `image`.
        background_color : SegmentationBackgroundColor
                Color to fill the background with.

        Returns
        -------
        np.ndarray
                Image with background replaced.
        """
        # Scalar fill: broadcasts over any channel count (white/black are achromatic, so every
        # channel takes the same value).
        if background_color == SegmentationBackgroundColor.WHITE:
            bg_fill = np.float32(1.0)
        elif background_color == SegmentationBackgroundColor.BLACK:
            bg_fill = np.float32(0.0)
        else:
            raise ValueError(f"Unsupported background color: {background_color}")

        if scale < 1.0:
            H, W = image.shape[:2]
            mask = cv2.resize(
                mask.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST
            ).astype(bool)

        # Apply mask: keep tissue, fill background
        output = np.empty_like(image)
        output[mask] = image[mask]
        output[~mask] = bg_fill

        return output

    def _crop_image(
        self, image: np.ndarray, mask: np.ndarray, scale: float, margin: int = 250
    ) -> tuple[np.ndarray, tuple[int, int]]:
        """
        Crop the image to the bounding box of the tissue mask plus a margin.

        Parameters
        ----------
        image : np.ndarray
                Input float32 image of shape (H, W, C).
        mask : np.ndarray
                Boolean tissue mask at `scale` resolution relative to `image` (see `_compute_tissue_mask`).
        scale : float
                proxy_size / full_size for `mask`, in (0, 1]. 1.0 means `mask` already matches `image`.
        margin : int
                Pixel margin to add around the bounding box (in full-resolution pixels).

        Returns
        -------
        tuple of (image, offset)
                image : the cropped image.
                offset : ``(x, y)`` pixel position of the crop's origin in the input image.
        """
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if not np.any(rows) or not np.any(cols):
            raise ValueError(
                "The image appears to be entirely background; cannot crop."
            )

        ymin, ymax = np.where(rows)[0][[0, -1]]
        xmin, xmax = np.where(cols)[0][[0, -1]]

        if scale < 1.0:
            ymin, ymax, xmin, xmax = (
                int(round(v / scale)) for v in (ymin, ymax, xmin, xmax)
            )

        ymin = max(0, ymin - margin)
        ymax = min(image.shape[0] - 1, ymax + margin)
        xmin = max(0, xmin - margin)
        xmax = min(image.shape[1] - 1, xmax + margin)

        return image[ymin : ymax + 1, xmin : xmax + 1, :], (int(xmin), int(ymin))
