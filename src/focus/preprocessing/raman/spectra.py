"""Clean Raman tile spectra with the original RamanSPy pipeline and zero-variance handling."""

import warnings
import numpy as np
import ramanspy as rp


def _zero_variance_spectra(spectra_array: np.ndarray) -> np.ndarray:
    """Identify zero-variance spectra (produce numerical errors downstream)."""
    forward_differences = np.diff(spectra_array, axis=-1)
    mad = np.median(
        np.abs(
            forward_differences - np.median(forward_differences, axis=-1, keepdims=True)
        ),
        axis=-1,
    )
    return np.array(mad == 0, dtype=np.bool_).squeeze()


def _process_tile_parallel(
    tile: np.ndarray,
    wavenumbers: np.ndarray,
    tile_index: int,
    slice_index: int,
    savgol_window: int,
    savgol_polyorder: int,
) -> tuple[np.ndarray, int, int]:
    """Process a single tile with the RamanSPy spectral cleaning pipeline."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)

        pipeline = rp.preprocessing.Pipeline(
            [
                rp.preprocessing.despike.WhitakerHayes(),
                rp.preprocessing.denoise.SavGol(
                    window_length=savgol_window, polyorder=savgol_polyorder
                ),
                rp.preprocessing.baseline.IASLS(),
                rp.preprocessing.normalise.MinMax(),
            ]
        )

        C, X, Y = tile.shape
        y_indices, x_indices = np.meshgrid(np.arange(X), np.arange(Y))
        coordinates = np.stack((x_indices, y_indices), axis=-1)

        reshaped_tile = tile.reshape(C, -1).T  # (Y*X, C)
        reshaped_coordinates = coordinates.reshape(-1, 2)

        zero_variance_mask = _zero_variance_spectra(reshaped_tile)
        reshaped_tile = reshaped_tile[~zero_variance_mask]
        reshaped_coordinates = reshaped_coordinates[~zero_variance_mask]

        if reshaped_tile.shape[0] == 0:
            return np.zeros_like(tile), tile_index, slice_index

        spectral_image = rp.SpectralImage(reshaped_tile, wavenumbers)
        processed_tile = pipeline.apply(spectral_image).spectral_data

        restored_tile = np.zeros((C, X, Y), dtype=np.float32)
        for i in range(processed_tile.shape[0]):
            x = int(reshaped_coordinates[i, 0])
            y = int(reshaped_coordinates[i, 1])
            restored_tile[:, x, y] = processed_tile[i, :]

        return restored_tile, tile_index, slice_index
