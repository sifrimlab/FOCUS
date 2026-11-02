"""Convert modality image previews into display RGB without changing native data."""

import numpy as np
from sklearn.decomposition import NMF


from focus.spot_colors import cluster_colors


def _first(pair: tuple[str, object]) -> str:
    """Sample id of a (sample_id, value) loop item."""
    return pair[0]


def _generate_cluster_colors(labels: np.ndarray) -> dict[str, str]:
    """Generate a color map for cluster labels using a perceptually distinct palette."""
    return cluster_colors(labels)


def _image_to_rgb_uint8(image_data: np.ndarray, axes: str | None) -> np.ndarray:
    """
    Convert loaded image data to an RGB uint8 (H, W, 3) array for GUI display.

    The channel / Y / X axes are located from the OME-TIFF ``axes`` metadata rather than
    guessed positionally, so multi-channel channel-first images (e.g. Raman 'CYX') and
    channel-last RGB images (e.g. 'YXC') are both handled correctly.

    Handles:
    - collapsing extra axes (T, Z, …) by taking their first index
    - dtype conversion to uint8
    - moving the channel axis last (HWC)
    - 1 channel (grayscale) → RGB by triplication
    - 2 channels → pad with zeros to 3
    - 3 channels → keep as-is
    - 4+ channels → NMF reduction to 3 components
    """
    # The colour axis is 'C' (separate channels, e.g. Raman) or 'S' (samples-per-pixel of an
    # interleaved RGB, how tifffile labels microscopy). Use the axes string only when it lines
    # up with the array; otherwise fall back to the positional heuristic further below.
    _COLOUR = ("C", "S")
    axes = (axes or "").upper()
    use_axes = len(axes) == image_data.ndim and "Y" in axes and "X" in axes

    if use_axes:
        # Collapse any non-spatial, non-colour axes (T, Z, …) by selecting the first index,
        # until the array is at most 3-D (Y, X[, colour]).
        while image_data.ndim > 3:
            drop = next(
                (i for i, ax in enumerate(axes) if ax not in ("Y", "X", *_COLOUR)), 0
            )
            image_data = np.take(image_data, 0, axis=drop)
            axes = axes[:drop] + axes[drop + 1 :]
    else:
        # No usable axes metadata: squeeze leading singleton dims to get to ≤ 3-D.
        while image_data.ndim > 3 and image_data.shape[0] == 1:
            image_data = image_data[0]

    # Convert to uint8 if needed.
    # Always use min-max normalisation: never assume a specific input range such as
    # [0, 1] for float32, because tifffile may return SubIFD data in any numeric
    # range (uint8 0-255, uint16 0-65535, float32 0-1, etc.).
    if image_data.dtype != np.uint8:
        arr = image_data.astype(np.float32)
        dmin, dmax = float(arr.min()), float(arr.max())
        if dmax > dmin:
            image_data = ((arr - dmin) / (dmax - dmin) * 255.0).astype(np.uint8)
        else:
            image_data = np.zeros_like(arr, dtype=np.uint8)
    else:
        # If uint8 but very low range (e.g. [0, 1]), re-scale for GUI visibility
        dmin, dmax = int(image_data.min()), int(image_data.max())
        if 0 < dmax < 10:  # Arbitrary threshold for "very low dynamic range"
            arr = image_data.astype(np.float32)
            image_data = ((arr - dmin) / (dmax - dmin) * 255.0).astype(np.uint8)

    # Ensure (H, W, colour): move the colour axis last.
    if image_data.ndim == 2:
        # Grayscale → (H, W, 1)
        image_data = image_data[:, :, np.newaxis]
    elif use_axes:
        colour = [i for i, ax in enumerate(axes) if ax in _COLOUR]
        c = colour[0] if colour else int(np.argmin(image_data.shape))
        if c != image_data.ndim - 1:
            image_data = np.moveaxis(image_data, c, -1)
    elif image_data.ndim == 3 and int(np.argmin(image_data.shape)) == 0:
        # No usable axes metadata: fall back to the channels-first positional heuristic.
        image_data = np.transpose(image_data, (1, 2, 0))

    n_channels = image_data.shape[-1]

    if n_channels == 1:
        # Grayscale → RGB
        image_data = np.repeat(image_data, 3, axis=-1)
    elif n_channels == 2:
        # 2-channel → pad with zeros to RGB
        pad = np.zeros((*image_data.shape[:2], 1), dtype=np.uint8)
        image_data = np.concatenate([image_data, pad], axis=-1)
    elif n_channels == 3:
        pass  # Already RGB
    else:
        # 4+ channels → NMF reduction to 3 components
        h, w = image_data.shape[:2]
        reshaped = image_data.reshape(-1, n_channels).astype(np.float32)
        nmf_model = NMF(n_components=3, init="nndsvda", random_state=42, max_iter=300)
        W = nmf_model.fit_transform(reshaped)
        wmax = W.max()
        if wmax > 0:
            W = (W / wmax * 255).astype(np.uint8)
        else:
            W = np.zeros_like(W, dtype=np.uint8)
        image_data = W.reshape(h, w, 3)

    return image_data
