import numpy as np
import numba as nb
from cuml.neighbors import KernelDensity
from scipy.signal import find_peaks
import cupy as cp

nb.core.entrypoints.init_all = lambda: None

import numpy as np

def maldi_nearest_neighbor(original_mz, original_intensity, reference_mz):
    """
    Vectorized nearest-neighbor interpolation for MALDI MSI data alignment
    
    Parameters:
    original_mz : np.array - Original m/z values (1D array)
    original_intensity : np.array - Corresponding intensity values
    reference_mz : np.array - Target m/z vector for standardization
    
    Returns:
    np.array - Interpolated intensities aligned to reference_mz
    """
    # Find nearest indices using vectorized operations
    distances = np.abs(original_mz[:, None] - reference_mz[None, :])
    nearest_indices = np.argmin(distances, axis=0)
    
    # Handle out-of-range values by setting to zero
    lower_bound = original_mz[0]
    upper_bound = original_mz[-1]
    out_of_range = (reference_mz < lower_bound) | (reference_mz > upper_bound)
    
    # Create output array
    interpolated = original_intensity[nearest_indices]
    interpolated[out_of_range] = 0
    
    return interpolated

def compute_reference_mz(spectra_list, prominence=0.1, tolerance=0.1):
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
    kde = KernelDensity(kernel = "gaussian", bandwidth=0.05).fit(all_mz.reshape(-1, 1))
    x_grid = np.linspace(all_mz.min(), all_mz.max(), 10000).reshape(-1, 1)
    log_dens = kde.score_samples(x_grid)
    x_grid = cp.asnumpy(x_grid).flatten()
    log_dens = cp.asnumpy(log_dens)
    
    # Find density peaks as candidate reference points
    peaks, _ = find_peaks(np.exp(log_dens), prominence=0.01)
    candidate_mz = x_grid[peaks]
    
    # Calculate mean spectrum
    mean_intensity = np.zeros_like(x_grid)
    for mz, intensity in spectra_list:
        # KDE-based intensity alignment
        kde_ind = np.abs(x_grid[:, None] - mz).argmin(axis=0)
        np.add.at(mean_intensity, kde_ind, intensity)
    mean_intensity /= len(spectra_list)
    
    # Refine peaks using mean spectrum characteristics
    refined_peaks, props = find_peaks(mean_intensity, 
                                    prominence=prominence*np.max(mean_intensity),
                                    width=2)
    
    # Merge peaks within tolerance window
    final_mz = []
    current_peak = x_grid[refined_peaks[0]]
    for peak in x_grid[refined_peaks[1:]]:
        if peak - current_peak <= tolerance:
            current_peak = (current_peak + peak)/2  # Weighted average
        else:
            final_mz.append(current_peak)
            current_peak = peak
    final_mz.append(current_peak)
    
    return np.array(final_mz)


@nb.njit
def interpolate(x_correct, x_wrong, values):
    
    index_min = np.argmin(np.abs(x_wrong - x_correct[0]))
    if x_wrong[index_min] > x_correct[0]:
        index_min -= 1
        index_min = index_min if index_min > 0 else 0
    index_max = np.argmin(np.abs(x_wrong - x_correct[-1]))
    if x_wrong[index_max] < x_correct[-1]:
        index_max += 1
        index_max = index_max if index_max < x_wrong.shape[0] else x_wrong.shape[0]
        
    result = np.zeros_like(x_correct, dtype=values.dtype)
    
    values = values[index_min: index_max + 1]
    x_wrong = x_wrong[index_min: index_max + 1]
    
    indices = np.searchsorted(x_correct, x_wrong, side='right') - 1
    
    idxs = indices < 0

    if idxs.sum() > 0:
        result[0] = np.dot(values[idxs], x_wrong[idxs] - x_correct[0]) / (x_correct[0] - x_correct[1])
    
    idxs = np.logical_and(0 <= indices, indices < x_correct.shape[0] - 1)
    if idxs.sum() > 0:
        temp = indices[idxs]
        shifted = temp + 1
        factor = np.divide(x_wrong[idxs] - x_correct[temp], x_correct[shifted] - x_correct[temp])
        vals = values[idxs]
        for i, index in enumerate(temp):
            result[index] += vals[i] * (1 - factor[i])
            result[index + 1] += vals[i] * factor[i]
    
    idxs = indices >= x_correct.shape[0] - 1
    if idxs.sum() > 0:
        temp = indices[idxs]
        result[-1] += np.dot(values[idxs], np.divide(x_wrong[idxs] - x_correct[temp], x_correct[temp] - x_correct[temp - 1]))
                
    return result


def interpolate_matrix(x_correct, x_wrong, values, axis=1):
    
    index_min = np.argmin(np.abs(x_wrong - x_correct[0]))
    if x_wrong[index_min] > x_correct[0]:
        index_min -= 1
        index_min = np.max([index_min, 0])
    index_max = np.argmin(np.abs(x_wrong - x_correct[-1]))
    if x_wrong[index_min] < x_correct[-1]:
        index_max += 1
        index_max = np.min([index_max, x_wrong.shape[0]])
        
    result = np.zeros((values.shape[0], x_correct.shape[0]), dtype=values.dtype)
    
    values = values[:, index_min: index_max + 1]
    x_wrong = x_wrong[index_min: index_max + 1]
    
    indices = np.searchsorted(x_correct, x_wrong, side='right') - 1
    
    idxs = indices < 0
    if idxs.sum() > 0:
        result[:, 0] = np.dot(values[:, idxs], x_wrong[idxs] - x_correct[0]) / (x_correct[0] - x_correct[1])
    
    idxs = np.logical_and(0 <= indices, indices < x_correct.shape[0] - 1)
    if idxs.sum() > 0:
        temp = indices[idxs]
        shifted = temp + 1
        factor = np.divide(x_wrong[idxs] - x_correct[temp], x_correct[shifted] - x_correct[temp])
        vals = values[:, idxs]
        for i, index in enumerate(temp):
            result[:, index] += vals[:, i] * (1 - factor[i])
            result[:, index + 1] += vals[:, i] * factor[i]
    
    idxs = indices >= x_correct.shape[0] - 1
    if idxs.sum() > 0:
        temp = indices[idxs]
        result[:, -1] += np.dot(values[:, idxs], np.divide(x_wrong[idxs] - x_correct[temp], x_correct[temp] - x_correct[temp - 1]))
        
    return result

