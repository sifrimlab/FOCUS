import numpy as np
import cupy as cp
from scipy.signal import find_peaks
from cuml.neighbors import KernelDensity
from tqdm import tqdm, trange
import os
import xml.etree.ElementTree as ET

from src.constants import ImzMLFileParser

def preprocess_lipidomics(path: str, peak_picking: bool, prominence: float, window_tolerance: float, dynamic_window: bool, dynamic_window_factor: float) -> tuple[float, float]:
    '''
    Read the imzML file to obtain the MSI experiment metadata. Returns the physical size
    of each detected spot in μm.

    Parameters
    ----------

    path : str
        Path to the imzML file. The first imzML file found in this directory will be used.
    peak_picking : bool
        If True, peak picking will be performed.
    prominence : float
        Minimum peak prominence (relative to max intensity) for peak picking.
    window_tolerance : float
        Tolerance window in parts per million (ppm) for peak mapping.
    dynamic_window : bool
        If True, dynamic peak windowing will be used, otherwise the fixed baseline will be used.
    dynamic_window_factor : float
        Factor for dynamic peak windowing.

    Returns
    -------
    tuple[float, float]
        Physical size of each detected spot in μm. (x, y)
    '''

    # Check input parameters types
    if type(path) != str or not isinstance(peak_picking, bool) or \
        not isinstance(prominence, float) or not isinstance(window_tolerance, int) or \
            not isinstance(dynamic_window, bool) or not isinstance(dynamic_window_factor, float):
        raise TypeError('Invalid input types. Expected str, bool, float, float, bool, float.')
    
    # Check if the path exists
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path {path} does not exist.")

    # List the files in the given directory and extract the absolute path for the first imzML file
    files = os.listdir(path)
    imzml_files = [f for f in files if f.endswith('.imzML')]
    if len(imzml_files) == 0:
        raise FileNotFoundError(f"No imzML files found in {path}.")
    imzml_file = os.path.join(path, imzml_files[0])
    
    # Obtain the IBD file using the same filename and swapping the extension
    ibd_file = imzml_file.replace('.imzML', '.ibd')
    if not os.path.exists(ibd_file):
        raise FileNotFoundError(f"IBD file {ibd_file} not found in {path}.")
    
    # Obtain the sample id
    sample_id = path.split('/')[-2]
    
    print(f"Reading imzML file: {imzml_file}, associated with IBD file {ibd_file}")

    # Parse the imzML file
    tree = ET.parse(imzml_file)
    root = tree.getroot()

    physical_size_x, physical_size_y = None, None
    mz_dtype, intensities_dtype = None, None

    # Define utility to convert string to dtype
    str_to_dtype = lambda s: np.float32 if s == "32-bit float" else np.float64

    # Load the metadata
    for scann_settings in root.find(ImzMLFileParser.SCAN_SETTINGS):
        for cv_param in scann_settings:
            if cv_param.attrib['name'].startswith('pixel size'):
                if cv_param.attrib['name'].endswith('x'):
                    physical_size_x = float(cv_param.attrib['value'])
                elif cv_param.attrib['name'].endswith('y'):
                    physical_size_y = float(cv_param.attrib['value'])

    for rpg in root.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST):
        if rpg.attrib['id'] == 'mzArray':
            for cv_param in rpg:
                if "float" in cv_param.attrib['name']:
                    mz_dtype = str_to_dtype(cv_param.attrib['name'])
        elif rpg.attrib['id'] == 'intensities':
            for cv_param in rpg:
                if "float" in cv_param.attrib['name']:
                    intensities_dtype = str_to_dtype(cv_param.attrib['name'])

    run = root.find(ImzMLFileParser.RUN_KEY)
    spectrum_list = run.find(ImzMLFileParser.SPECTRUM_LIST_KEY)
    spectra = spectrum_list.findall(ImzMLFileParser.SPECTRUM_KEY)
    
    # Decode the binary data from the imzML file
    parsed_spectra = [spectra_to_dict(spectrum) for spectrum in spectra]

    # Select the final data type based on the largest one
    final_dtype = np.promote_types(mz_dtype, intensities_dtype)

    # Read the binary IBD data file to obtain the actual M/Zs and intensities data
    read_mzs = lambda metadata: np.fromfile(ibd_file, dtype = mz_dtype, count = metadata['length'], offset = metadata['offset'])
    read_intensities = lambda metadata: np.fromfile(ibd_file, dtype = intensities_dtype, count = metadata['length'], offset = metadata['offset'])
    
    # Read the data from the IBD file
    mzs = [read_mzs(metadata["mzs"]) for metadata in parsed_spectra]
    intensities = [read_intensities(metadata["intensities"]) for metadata in parsed_spectra]

    # Obtain the lower and upper bounds for the mzs values
    low_mz, high_mz = float('inf'), float('-inf')
    for mz in mzs:
        if np.min(mz) < low_mz:
            low_mz = np.min(mz)
        if np.max(mz) > high_mz:
            high_mz = np.max(mz)

    # If False, we assume that every datapoint is alreay aligned with the same M/Z values
    if peak_picking == True:
        # Define an omogenous M/Z array that aggregates datapoints to achieve a common spectrum
        unified_mz_values = compute_reference_mz([(mzs[i], intensities[i]) for i in range(len(mzs))], prominence = prominence)

        # Define the final data matrix to store the intensities values
        merged_intensities = np.zeros((len(intensities), len(unified_mz_values)), dtype = final_dtype)

        for index in tqdm(range(0, len(mzs)), desc="Interpolating intensities"):
            # Interpolate the intensities values to the unified M/Z values
            merged_intensities[index, :] = maldi_windowed_mapping(mzs[index], intensities[index], unified_mz_values, window_tolerance, dynamic_window, dynamic_window_factor)
    else:
        mz_lenght = len(mzs[0])
        # Check if the mzs and intensities are consistent with dimensions
        for mz in mzs:
            if len(mz) != mz_lenght:
                raise ValueError(f"Mismatch in mz length, there are datapoint with different lengths. Perhaps enable peak picking.")
        
        unified_mz_values = mzs[0]
        merged_intensities = np.zeros((len(intensities), mz_lenght), dtype = final_dtype)
        for index in tqdm(range(0, len(mzs)), desc="Copying intensities"):
            # Copy the intensities values to the final data matrix
            merged_intensities[index, :] = intensities[index]

    # Parse the coordinates from the imzML file
    coordinates = [(metadata["x"], metadata['y']) for metadata in parsed_spectra]
    coordinates = np.array(coordinates, dtype = np.int32)

    # Save the processing output
    save_numpy_matrix(path = path, sample = sample_id, reference_mz = unified_mz_values, intensities = merged_intensities, coordinates = coordinates)

    return physical_size_x, physical_size_y

def spectra_to_dict(spectra: ET.Element) -> dict:
    '''
    Convert the spectra element to a dictionary.

    Parameters
    ----------
    spectra : ET.Element
        The spectra element from the imzML file.

    Returns
    -------
    dict
        A dictionary with the spectrum data.
    '''
    
    # Check input parameter type
    if not isinstance(spectra, ET.Element):
        raise TypeError('Invalid input type. Expected ET.Element.')
    
    x, y, mzs, intesities = None, None, None, None

    scan_list = spectra.find(ImzMLFileParser.SCAN_LIST)
    scan = scan_list.find(ImzMLFileParser.SCAN)

    for cv_param in scan.iter(ImzMLFileParser.CV_PARAM):
        if cv_param.attrib['name'] == "position x":
            x = int(cv_param.attrib['value'])
        elif cv_param.attrib['name'] == "position y":
            y = int(cv_param.attrib['value'])

    bdal = spectra.find(ImzMLFileParser.BINARY_DATA_ARRAY_LIST)

    for element in bdal.findall(ImzMLFileParser.BINARY_DATA_ARRAY):
        for cv_param in element.iter(ImzMLFileParser.CV_PARAM):
            if cv_param.attrib['name'] == "external array length":
                length = int(cv_param.attrib['value'])
            if cv_param.attrib['name'] == "external encoded length":
                encoded_length = int(cv_param.attrib['value'])
            if cv_param.attrib['name'] == "external offset":
                offset = int(cv_param.attrib['value'])

        if element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] == 'mzArray':
            mzs = {
                'length': length,
                'encoded_length': encoded_length,
                'offset': offset
            }
        elif element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] == 'intensities':
            intesities = {
                'length': length,
                'encoded_length': encoded_length,
                'offset': offset
            }

    return {'x': x, 'y': y, 'mzs': mzs, 'intensities': intesities}

def save_numpy_matrix(path: str, sample: str,  reference_mz: np.ndarray, intensities: np.ndarray, coordinates: np.ndarray) -> None:
    '''
    Save the computed values into dedicated .npy files.

    Parameters
    ----------
    path : str
        Path to the directory where the .npy files will be saved.
    sample : str
        Name of the sample. This will be used to name the files.
    reference_mz : np.ndarray
        The reference M/Z values computed from the spectra.
    intensities : np.ndarray
        The interpolated intensities values.
    coordinates : np.ndarray
        The coordinates of the spectra in the imzML file.
    '''

    # Create the output folder
    output_folder = os.path.join(path, 'processed')
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Save the reference M/Z values
    np.save(os.path.join(output_folder, f'{sample}_reference_mz.npy'), reference_mz)

    # Save the intensities values
    np.save(os.path.join(output_folder, f'{sample}_intensities.npy'), intensities)

    # Save the coordinates values
    np.save(os.path.join(output_folder, f'{sample}_coordinates.npy'), coordinates)

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

def maldi_windowed_mapping(original_mz: np.ndarray, original_intensity: np.ndarray, reference_mz: np.ndarray, ppm_tolerance: int = 20, dynamic_window: bool = True, dynamic_window_factor: float = 1e6) -> np.ndarray:
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
    dynamic_window : bool
        If True, dynamic peak windowing will be used, otherwise the fixed baseline will be used.
    dynamic_window_factor : float
        Factor for dynamic peak windowing.

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
    if dynamic_window == True:
        window = mz * ppm_tolerance / dynamic_window_factor
        lower = mz - window
        upper = mz + window
    else:
        window = np.zeros_like(mz)
        window[:] = np.min(mz) * ppm_tolerance / dynamic_window_factor
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


def compute_reference_mz(spectra_list, prominence = 0.01):
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