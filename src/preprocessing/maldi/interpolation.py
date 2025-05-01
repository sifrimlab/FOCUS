import numpy as np
import cupy as cp
from scipy.signal import find_peaks
from cuml.neighbors import KernelDensity
from tqdm import trange


def kde_consensus(all_mz: np.ndarray, x_grid: np.ndarray, n_iter: int = 20, subsample_size: int = 50000, bandwidth: float = 0.05) -> np.ndarray:
    '''
    Compute the consensus m/z vector using Kernel Density Estimation (KDE). The ensamble method is used to reduce GPU memory usage
    without introducing statistical bias.

    Parameters
    ----------
    all_mz : np.ndarray
        The m/z values from all spectra.
    x_grid : np.ndarray
        The grid of m/z values for density estimation.
    n_iter : int
        The number of iterations for the ensemble method.
    subsample_size : int
        The size of the subsample for each iteration.
    bandwidth : float
        The bandwidth for the KDE.
    
    Returns
    -------
    np.ndarray
        The average density across all iterations.
    '''
    
    
    total_density = cp.zeros(x_grid.shape[0])

    # Iterate the KDE process
    for _ in trange(n_iter, desc="KDE Consensus Progress"):

        # Randomly sample m/z values from the input array
        idx = cp.random.choice(all_mz.shape[0], size=subsample_size, replace=False)
        sample = all_mz[idx.get()].reshape(-1, 1)

        # Fit the KDE model (use CUDA with RAPIDS)
        kde = KernelDensity(kernel='gaussian', bandwidth=bandwidth)
        kde.fit(sample)

        # Compute the log density for the grid and convert it to linear
        density = kde.score_samples(x_grid)
        total_density += cp.exp(density)

        # Free up memory
        del kde, sample, density, idx
        cp.get_default_memory_pool().free_all_blocks()

    avg_density = total_density / n_iter

    # Free up memory
    del total_density
    cp.get_default_memory_pool().free_all_blocks()
    cp.cuda.Device().synchronize()

    return cp.asnumpy(avg_density)

def maldi_windowed_mapping(original_mz, original_intensity, reference_mz, ppm_tolerance=20):
    """
    GPU-accelerated MALDI intensity mapping using variable-size windowing (vectorized with CuPy).

    Parameters
    ----------
    original_mz : np.ndarray
        Original m/z values.
    original_intensity : np.ndarray
        Corresponding intensity values.
    reference_mz : np.ndarray
        Target reference m/z values.
    ppm_tolerance : float
        Tolerance window in parts per million (ppm).

    Returns
    -------
    np.ndarray
        Intensities mapped to reference_mz.
    """

    # Move to GPU
    mz = cp.asarray(original_mz)
    intensity = cp.asarray(original_intensity)
    ref_mz = cp.asarray(reference_mz)

    # Compute PPM window bounds
    window = mz * ppm_tolerance / 1e4
    lower = mz - window
    upper = mz + window

    # Expand dimensions for broadcasting
    ref_mz_exp = ref_mz[None, :]        # (1, M)
    mz_exp = mz[:, None]                # (N, 1)
    lower_exp = lower[:, None]          # (N, 1)
    upper_exp = upper[:, None]          # (N, 1)

    # Boolean mask for matching windows
    in_window = (ref_mz_exp >= lower_exp) & (ref_mz_exp <= upper_exp)

    # Distance from each mz to each reference mz (masked)
    distances = cp.where(in_window, cp.abs(ref_mz_exp - mz_exp), cp.inf)

    # Find index of closest ref_mz within window
    nearest_idx = cp.argmin(distances, axis=1)
    valid = cp.any(in_window, axis=1)  # mz values that found a match

    # Only keep valid mappings
    valid_idx = nearest_idx[valid]
    valid_intensity = intensity[valid]

    # Accumulate using bincount
    result = cp.zeros(ref_mz.shape, dtype=original_intensity.dtype)
    bincount = cp.bincount(valid_idx, weights=valid_intensity, minlength=ref_mz.shape[0])
    result[:bincount.shape[0]] = bincount

    return cp.asnumpy(result)


def compute_reference_mz(spectra_list, prominence = 0.01, tolerance = 0.1):
    """
    Computes consensus reference m/z vector using mean spectrum alignment
    and peak prominence analysis
    
    Parameters:
    spectra_list : list of tuples - [(mz_array, intensity_array)]
    prominence : minimum peak prominence (relative to max intensity)
    tolerance : m/z merging tolerance (Da)
    
    Returns:
    reference_mz : np.array - Consensus m/z values
    """
    # Create high-resolution grid for density estimation
    all_mz = np.concatenate([s[0] for s in spectra_list])
    x_grid = cp.linspace(all_mz.min(), all_mz.max(), 10000).reshape(-1, 1)
    log_dens = kde_consensus(all_mz, x_grid)
    
    x_grid = cp.asnumpy(x_grid).flatten()
    log_dens = cp.asnumpy(log_dens)
    
    # Find density peaks as candidate reference points. Use a relative threshold
    peaks, _ = find_peaks(np.exp(log_dens), prominence = prominence * np.max(log_dens))
    candidate_mzs = x_grid[peaks]

    return np.array(candidate_mzs)