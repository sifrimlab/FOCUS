"""
Pure-NumPy helpers for microscopy feature extraction.

These functions are intentionally torch-free so the pixel, coordinate and background
semantics can be unit-tested on CPU without a GPU: where windows and grid patches sit, how
they are cut from the resident image, and which patches or encoder tokens count as background.
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
) -> tuple[np.ndarray, np.ndarray]:
    """
    Top-left and centre coordinates of a non-overlapping patch grid, without touching pixels.

    Only ``(M, 2)`` coordinate arrays are produced (a few MB even at 1M patches); the pixel
    data is cut lazily, batch by batch, during encoding.

    Parameters
    ----------
    img_shape : tuple
        Shape of the (HWC) image, ``(H, W, C)`` or ``(H, W)``.
    patch_size : int
        Side length of each square patch.

    Returns
    -------
    top_left : np.ndarray
        ``(M, 2)`` int32 array of ``(x0, y0)`` top-left pixel positions, row-major (x fastest).
    center : np.ndarray
        ``(M, 2)`` float32 array of the patch centres ``(x0 + patch_size // 2, y0 + patch_size // 2)``.
    """
    h, w = int(img_shape[0]), int(img_shape[1])
    half = patch_size // 2
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


def cut_window_batch(
    img: np.ndarray,
    top_left_batch: np.ndarray,
    height: int,
    width: int,
) -> np.ndarray:
    """
    Cut a batch of ``height x width`` windows from the resident image, zero-padding short borders.

    Parameters
    ----------
    img : np.ndarray
        HWC image (3 channels) the windows are cut from.
    top_left_batch : np.ndarray
        ``(B, 2)`` array of ``(x0, y0)`` top-left positions.
    height, width : int
        Window size in pixels.

    Returns
    -------
    np.ndarray
        ``(B, height, width, 3)`` float32 batch. Windows running off the image are zero-padded.
    """
    b = top_left_batch.shape[0]
    out = np.zeros((b, height, width, 3), dtype=np.float32)
    for i in range(b):
        x0 = int(top_left_batch[i, 0])
        y0 = int(top_left_batch[i, 1])
        window = img[y0:y0 + height, x0:x0 + width, :]
        out[i, :window.shape[0], :window.shape[1], :] = window
    return out


def cut_patch_batch(
    img: np.ndarray,
    top_left_batch: np.ndarray,
    patch_size: int = 224,
) -> np.ndarray:
    """Square ``patch_size`` windows (see :func:`cut_window_batch`)."""
    return cut_window_batch(img, top_left_batch, patch_size, patch_size)


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


def token_background_mask(
    windows: np.ndarray,
    patch: int,
    bg_color: np.ndarray,
) -> np.ndarray:
    """
    Flag the encoder tokens of each window that are (almost) entirely background.

    A token is the ``patch x patch`` pixel square the encoder embeds as one input token;
    tokens are numbered row-major (``row * n_cols + col``), the order in which a ViT
    flattens its patch grid. The test is :func:`background_mask` applied to every token.

    Parameters
    ----------
    windows : np.ndarray
        ``(B, H, W, 3)`` raw windows, ``H`` and ``W`` multiples of ``patch``.
    patch : int
        Token side in pixels.
    bg_color : np.ndarray
        ``(3,)`` background color.

    Returns
    -------
    np.ndarray
        ``(B, (H // patch) * (W // patch))`` boolean array, True for background tokens.
    """
    b, h, w, c = windows.shape
    ny, nx = h // patch, w // patch
    tokens = windows.reshape(b, ny, patch, nx, patch, c).transpose(0, 1, 3, 2, 4, 5)
    return background_mask(tokens.reshape(b * ny * nx, patch, patch, c), bg_color).reshape(b, ny * nx)
