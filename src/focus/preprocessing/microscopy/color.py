"""Apply the original gamma and intensity-contrast operations to microscopy pixels."""

import numpy as np


# --- Image utilities ---


def enhance_contrast(
    channel: np.ndarray,
    saturated_pixels: float = 0.35,
    max_stat_pixels: int | None = None,
) -> np.ndarray:
    """
    Enhance the contrast of a single channel image by stretching the histogram.
    Add a small amount of saturated pixels to improve the contrast.

    Parameters
    ----------
    channel : np.ndarray[np.uint8]
            The channel to enhance.
    saturated_pixels : float
            The amount of saturated pixels to add. Default is 0.35%.
    max_stat_pixels : int, optional
            If set and the number of nonzero pixels exceeds this, the saturation percentiles are
            estimated from a strided subsample instead of the full array (percentiles are stable
            under subsampling, so this is a fast, negligible-error approximation for huge images).
            The stretch itself is still applied exactly to every pixel of `channel`. Default (None)
            always uses the exact full-population percentile, unchanged from prior behavior.
    """

    if channel.dtype != np.float32:
        channel = channel.astype(np.float32)

    mask = channel > 0

    if np.any(mask):
        values = channel[mask]
        if max_stat_pixels is not None and values.size > max_stat_pixels:
            stride = -(-values.size // max_stat_pixels)  # ceil division
            values = values[::stride]

        # Compute the pixels to saturate
        p_low, p_high = np.percentile(
            values, (saturated_pixels, 100 - saturated_pixels)
        )

        if p_high > p_low:
            # Stretch the histogram in-place
            np.clip(channel, p_low, p_high, out=channel)
            channel -= p_low
            channel /= p_high - p_low

    return channel


def gamma_correction(channel: np.ndarray, gamma: float = 0.45) -> np.ndarray:
    """
    Apply gamma correction to a single channel image.

    Parameters
    ----------
    image : np.ndarray[np.uint8]
            The image to correct.
    gamma : float
            The gamma value to use. Default is 0.45.
    """

    if channel.dtype != np.float32:
        channel = channel.astype(np.float32)
    np.power(channel, gamma, out=channel)
    return channel
