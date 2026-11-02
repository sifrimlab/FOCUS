"""Raman quick-mosaic thresholding and area-based contour refinement."""

import cv2
import numpy as np
from scipy.ndimage import binary_fill_holes
from skimage import morphology
from focus.runtime.reporter_context import get_reporter


def tissue_mask(
    mosaic: np.ndarray,
    bg_min_area_fraction: float,
    otsu_threshold_factor: float,
    min_object_size: int,
) -> np.ndarray:
    """Return a filled boolean (H, W) mask from the uint8 quick mosaic; leave pixels unchanged."""
    # Otsu-based segmentation on the quick mosaic (uint8)
    clip_value = np.percentile(mosaic, 95)
    clipped_img = np.clip(mosaic, None, clip_value).astype(np.uint8)

    otsu_thresh, _ = cv2.threshold(
        clipped_img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    adjusted_thresh = int(otsu_thresh * otsu_threshold_factor)
    _, thresh = cv2.threshold(
        mosaic,
        adjusted_thresh,
        255,
        cv2.THRESH_BINARY,
    )
    del clipped_img

    mask_clean = morphology.remove_small_objects(
        thresh.astype(bool), max_size=min_object_size
    )
    del thresh
    segmentation_mask = binary_fill_holes(mask_clean)
    del mask_clean

    return refine_mask(segmentation_mask, bg_min_area_fraction)


def refine_mask(
    segmentation_mask: np.ndarray, bg_min_area_fraction: float
) -> np.ndarray:
    """Keep external contours above the mosaic-area fraction, or warn and retain the input mask."""
    seg_mask_uint8 = segmentation_mask.astype(np.uint8) * 255
    contours, _ = cv2.findContours(
        seg_mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    if contours:
        image_area = seg_mask_uint8.shape[0] * seg_mask_uint8.shape[1]
        area_threshold = bg_min_area_fraction * image_area
        large_contours = [c for c in contours if cv2.contourArea(c) >= area_threshold]

        tissue_mask = np.zeros_like(seg_mask_uint8)
        cv2.drawContours(
            tissue_mask,
            large_contours,
            contourIdx=-1,
            color=255,
            thickness=cv2.FILLED,
        )
        segmentation_mask = tissue_mask.astype(bool)
    else:
        get_reporter().warning("No contours found: the background mask is not refined")
    del seg_mask_uint8

    return segmentation_mask
