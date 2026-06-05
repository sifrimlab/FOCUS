"""
Pure-NumPy helpers for microscopy patch feature extraction.

These functions are intentionally torch-free so the exact pixel/coordinate/
background semantics can be unit-tested on CPU without a GPU. They are the
single source of truth for *where* patches sit, *how* they are cut from the
resident image, and *which* patches count as background — the parts that must
stay bit-identical to the original in-memory implementation, because a silent
misalignment would corrupt the downstream MuData join with no error.
"""

import numpy as np

from focus.constants import SegmentationBackgroundColor


def ensure_hwc3(img: np.ndarray) -> np.ndarray:
    """
    Coerce an image to HWC layout with exactly 3 channels.

    Mirrors the channel handling previously inlined in the patch extractor:
    2-D images gain a channel axis, single-channel images are repeated to RGB,
    and a 4th (alpha) channel is dropped. Other channel counts are returned
    unchanged (the caller's downstream code assumes 3 channels).
    """
    if img.ndim == 2:
        img = img[..., None]
    c = img.shape[2]
    if c == 1:
        img = np.repeat(img, 3, axis=2)
    elif c == 4:
        img = img[..., :3]
    return img


def compute_patch_coordinates(
    img_shape: tuple,
    patch_size: int = 224,
    patch_centers: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Compute patch top-left and centre coordinates *without touching pixels*.

    This is the cheap, vectorized replacement for the coordinate bookkeeping in
    the old ``_extract_patches``. Only ``(M, 2)`` integer/float coordinate arrays
    are produced (a few MB even at 1M patches); the pixel data is cut lazily,
    batch by batch, during encoding.

    Parameters
    ----------
    img_shape : tuple
        Shape of the (HWC) image, ``(H, W, C)`` or ``(H, W)``.
    patch_size : int
        Side length of each square patch.
    patch_centers : np.ndarray, optional
        ``(N, 2)`` array of requested ``(x, y)`` centres. If ``None``,
        non-overlapping grid patches are generated across the image.

    Returns
    -------
    top_left : np.ndarray
        ``(M, 2)`` int32 array of clamped ``(x0, y0)`` top-left pixel positions.
    center : np.ndarray
        ``(M, 2)`` float32 array of the actual ``(x, y)`` patch centres (after
        any clamping to the image bounds).
    """
    h, w = int(img_shape[0]), int(img_shape[1])
    half = patch_size // 2

    if patch_centers is not None:
        centers = np.asarray(patch_centers, dtype=np.float32)
        if centers.shape[0] == 0:
            return (np.zeros((0, 2), dtype=np.int32), np.zeros((0, 2), dtype=np.float32))

        # int() truncates toward zero; .astype(np.int32) does the same for the
        # values seen here, so the result matches the original per-patch loop.
        x0 = (centers[:, 0] - half).astype(np.int32)
        y0 = (centers[:, 1] - half).astype(np.int32)

        # Reproduce the original max(0, min(x0, w - patch_size)) nesting exactly
        # (np.clip would differ when the image is smaller than a patch).
        x0 = np.maximum(0, np.minimum(x0, w - patch_size))
        y0 = np.maximum(0, np.minimum(y0, h - patch_size))

        top_left = np.stack([x0, y0], axis=1).astype(np.int32)
        center = (top_left + half).astype(np.float32)
        return top_left, center

    # Non-overlapping grid across the image foreground.
    n_y = h // patch_size
    n_x = w // patch_size
    if n_y == 0 or n_x == 0:
        return (np.zeros((0, 2), dtype=np.int32), np.zeros((0, 2), dtype=np.float32))

    x_coords = np.arange(n_x) * patch_size
    y_coords = np.arange(n_y) * patch_size
    xx, yy = np.meshgrid(x_coords, y_coords)  # row-major ravel => x fastest
    top_left = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(np.int32)
    center = (top_left + half).astype(np.float32)
    return top_left, center


def cut_patch_batch(
    img: np.ndarray,
    top_left_batch: np.ndarray,
    patch_size: int = 224,
) -> np.ndarray:
    """
    Cut a batch of patches from the resident image, zero-padding short borders.

    Equivalent to the per-patch slice + pad in the old ``_extract_patches``
    (interior patches are full; patches running off the edge are zero-padded into
    a ``patch_size`` buffer). ``top_left_batch`` is expected to be pre-clamped by
    :func:`compute_patch_coordinates`, so padding only occurs when the image is
    smaller than a patch.

    Parameters
    ----------
    img : np.ndarray
        HWC image (3 channels) the patches are cut from.
    top_left_batch : np.ndarray
        ``(B, 2)`` array of ``(x0, y0)`` top-left positions.
    patch_size : int
        Side length of each square patch.

    Returns
    -------
    np.ndarray
        ``(B, patch_size, patch_size, 3)`` float32 batch.
    """
    b = top_left_batch.shape[0]
    out = np.zeros((b, patch_size, patch_size, 3), dtype=np.float32)
    for i in range(b):
        x0 = int(top_left_batch[i, 0])
        y0 = int(top_left_batch[i, 1])
        patch = img[y0:y0 + patch_size, x0:x0 + patch_size, :]
        ph, pw = patch.shape[0], patch.shape[1]
        out[i, :ph, :pw, :] = patch
    return out


def resolve_bg_color(background_color: SegmentationBackgroundColor) -> np.ndarray:
    """Map the background-color enum to an RGB array in the image's [0, 1] range."""
    if background_color == SegmentationBackgroundColor.WHITE:
        return np.array([1.0, 1.0, 1.0], dtype=np.float32)
    if background_color == SegmentationBackgroundColor.BLACK:
        return np.array([0.0, 0.0, 0.0], dtype=np.float32)
    raise ValueError(f"Unsupported background color: {background_color}")


def background_mask(
    patches: np.ndarray,
    bg_color: np.ndarray,
    atol: float = 1e-3,
    frac: float = 0.99,
) -> np.ndarray:
    """
    Flag patches that are (almost) entirely background.

    A patch is background when at least ``frac`` of its pixels match ``bg_color``.
    This is the complement of the old foreground test
    (``bg_pixel_counts < patch_area * 0.99``) and is computed on the *raw* patch,
    before any normalization, exactly as before.

    Parameters
    ----------
    patches : np.ndarray
        ``(B, H, W, 3)`` raw patch batch in the image's value range.
    bg_color : np.ndarray
        ``(3,)`` background color.
    atol : float
        Absolute tolerance for the per-channel color match.
    frac : float
        Background-pixel fraction at or above which a patch is background.

    Returns
    -------
    np.ndarray
        ``(B,)`` boolean array — True where the patch is background.
    """
    bg = np.all(np.isclose(patches, bg_color, atol=atol), axis=-1)  # (B, H, W)
    bg_counts = np.sum(bg, axis=(1, 2))                             # (B,)
    area = patches.shape[1] * patches.shape[2]
    return bg_counts >= frac * area
