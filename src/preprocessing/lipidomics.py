import numpy as np
import torch, math
from scipy.signal import find_peaks
from tqdm import tqdm, trange
import os
import xml.etree.ElementTree as ET

from constants import ImzMLFileParser

def preprocess_lipidomics(path: str, sample_id: str, modality_name: str, peak_picking: bool, prominence: float, window_tolerance: float, dynamic_window: bool, dynamic_window_factor: float) -> tuple[float, float]:
    '''
    Read the imzML file to obtain the MSI experiment metadata. Returns the physical size
    of each detected spot in μm.

    Parameters
    ----------

    path : str
        Path to the data source directory.
    sample_id : str
        Sample ID.
    modality_name : str
        Name of the modality.
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
    if type(path) != str or type(sample_id) != str or type(modality_name) != str or not isinstance(peak_picking, bool) or \
        not isinstance(prominence, float) or not isinstance(window_tolerance, int) or \
            not isinstance(dynamic_window, bool) or not isinstance(dynamic_window_factor, float):
        raise TypeError('Invalid input types. Expected str, bool, float, float, bool, float.')
    
    sample_path = os.path.join(path, sample_id)
    mod_path = os.path.join(sample_path, modality_name)
    
    # Check if the path exists
    if not os.path.exists(mod_path):
        raise FileNotFoundError(f"Path {mod_path} does not exist.")

    # List the files in the given directory and extract the absolute path for the first imzML file
    files = os.listdir(mod_path)
    imzml_files = [f for f in files if f.endswith('.imzML')]
    if len(imzml_files) == 0:
        raise FileNotFoundError(f"No imzML files found in {mod_path}.")
    imzml_file = os.path.join(mod_path, imzml_files[0])
    
    # Obtain the IBD file using the same filename and swapping the extension
    ibd_file = imzml_file.replace('.imzML', '.ibd')
    if not os.path.exists(ibd_file):
        raise FileNotFoundError(f"IBD file {ibd_file} not found in {mod_path}.")
    
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

    # Create the output folder
    output_folder = os.path.join(sample_path, 'preprocessing', modality_name)
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    # Save the processing output
    save_numpy_matrix(path = output_folder, sample = sample_id, reference_mz = unified_mz_values, intensities = merged_intensities, coordinates = coordinates)

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
    if not os.path.exists(path):
        os.makedirs(path)

    # Convert the intensities to a matrix using the coordinates
    intensities_matrix = np.zeros((coordinates[:, 0].max() + 1, coordinates[:, 1].max() + 1, reference_mz.shape[0]), dtype = intensities.dtype)
    for i in range(coordinates.shape[0]):
        intensities_matrix[coordinates[i, 0], coordinates[i, 1], :] = intensities[i, :]

    # Save the reference M/Z values
    np.save(os.path.join(path, f'{sample}_reference_mz.npy'), reference_mz)

    # Save the intensities values
    np.save(os.path.join(path, f'{sample}_intensities_matrix.npy'), intensities_matrix)

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
    
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Move the input to device
    all_mz: torch.Tensor = torch.from_numpy(all_mz).float().to(device)
    x_grid: torch.Tensor = torch.from_numpy(x_grid).float().to(device).reshape(-1, 1)

    total_density: torch.Tensor = torch.zeros(x_grid.shape[0], device=device)

    # Iterate the KDE process
    for _ in trange(n_iter, desc="KDE Consensus Progress"):

        # Randomly sample m/z values from the input array
        idx = torch.randperm(len(all_mz), device=device)[:subsample_size]
        sample = all_mz[idx].reshape(-1, 1)

        # Fit the KDE model (use CUDA with RAPIDS)
        kde = KernelDensity(kernel='gaussian', bandwidth=bandwidth)
        kde.fit(sample)

        # Compute the log density for the grid and convert it to linear
        log_density = kde.score_samples(x_grid)
        total_density += torch.exp(log_density).squeeze()

        # Free up memory
        del kde, sample, log_density, idx
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

    avg_density = (total_density / n_iter).cpu().numpy()

    # Free up memory
    del total_density
    torch.cuda.empty_cache()
    torch.cuda.synchronize()

    return avg_density

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

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Move to GPU
    mz = torch.from_numpy(original_mz).float().to(device)
    intensity = torch.from_numpy(original_intensity).float().to(device)
    ref_mz = torch.from_numpy(reference_mz).float().to(device)

    # Compute PPM window bounds
    if dynamic_window == True:
        window = mz * ppm_tolerance / dynamic_window_factor
        lower = mz - window
        upper = mz + window
    else:
        window = torch.zeros_like(mz)
        window[:] = torch.min(mz) * ppm_tolerance / dynamic_window_factor
        lower = mz - window
        upper = mz + window

    # Expand dimensions for broadcasting
    ref_mz_exp = ref_mz.unsqueeze(0)  # (1, M)
    mz_exp = mz.unsqueeze(1)          # (N, 1)
    lower_exp = lower.unsqueeze(1)    # (N, 1)
    upper_exp = upper.unsqueeze(1)    # (N, 1)

    # Boolean mask for matching windows
    in_window = (ref_mz_exp >= lower_exp) & (ref_mz_exp <= upper_exp)

    # Distance from each mz to each reference mz (masked)
    distances = torch.where(in_window, torch.abs(ref_mz_exp - mz_exp), torch.inf)

    # Find index of closest ref_mz within window
    nearest_idx = torch.argmin(distances, axis=1)
    valid = torch.any(in_window, axis=1)  # mz values that found a match

    # Only keep valid mappings
    valid_idx = nearest_idx[valid]
    valid_intensity = intensity[valid]

    # Accumulate using scatter_add for efficient summation
    result = torch.zeros_like(ref_mz, dtype=intensity.dtype)
    result.scatter_add_(0, valid_idx, valid_intensity)

    return result.cpu().numpy()

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

    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Create high-resolution grid for density estimation
    all_mz: np.ndarray = np.concatenate([s[0] for s in spectra_list])
    x_grid: np.ndarray = np.linspace(all_mz.min(), all_mz.max(), 10000)
    
    avg_density = kde_consensus(all_mz, x_grid.reshape(-1, 1))
    avg_density = torch.from_numpy(avg_density).float().to(device)

    # Find peaks using optimized vectorized approach
    peak_indices = _find_peaks_torch(avg_density, prominence_factor=prominence)
    peak_indices = peak_indices.cpu().numpy()
    candidate_mzs = x_grid[peak_indices]

    return candidate_mzs

def _find_peaks_torch(density: torch.Tensor, prominence_factor: float = 0.01) -> torch.Tensor:
    """
    PyTorch peak detection with basic prominence filtering
    
    Args:
        density: 1D tensor of density values
        prominence_factor: Relative threshold (0.01 = 1% of max density)
    
    Returns:
        Tensor of peak indices
    """
    # Find local maxima
    shifted_left = density[:-2]
    shifted_center = density[1:-1]
    shifted_right = density[2:]
    
    peaks = (shifted_center > shifted_left) & (shifted_center > shifted_right)
    peak_indices = torch.nonzero(peaks).squeeze() + 1  # Compensate for window shift
    
    # Apply prominence filter
    if prominence_factor > 0:
        threshold = prominence_factor * density.max()
        peak_heights = density[peak_indices]
        mask = peak_heights >= threshold
        peak_indices = peak_indices[mask]
    
    return peak_indices

class KernelDensity:
    def __init__(self, bandwidth=1.0, kernel='gaussian'):
        self.bandwidth = bandwidth
        self.kernel = kernel
        self.train_data = None

    def fit(self, X):
        """Store training data for KDE."""
        self.train_data = X
        self.n_samples, self.n_features = X.shape

    def _gaussian_kernel(self, diffs):
        """Gaussian kernel on pairwise differences."""
        # diffs: [n_eval, n_train, n_features]
        exponent = -0.5 * (diffs / self.bandwidth) ** 2  # shape: [n_eval, n_train, n_features]
        exponent = exponent.sum(dim=-1)  # shape: [n_eval, n_train]

        norm_const = (1.0 / (math.sqrt(2 * math.pi) * self.bandwidth)) ** self.n_features
        return norm_const * torch.exp(exponent)

    def score_samples(self, X):
        """Compute log-density estimates at points X."""
        if self.train_data is None:
            raise ValueError("Model must be fit before calling score_samples.")

        device = self.train_data.device
        X = X.to(device)

        # Compute pairwise differences
        X_exp = X.unsqueeze(1)  # [n_eval, 1, n_features]
        train_exp = self.train_data.unsqueeze(0)  # [1, n_train, n_features]
        diffs = X_exp - train_exp  # [n_eval, n_train, n_features]

        if self.kernel == 'gaussian':
            kernel_vals = self._gaussian_kernel(diffs)
        else:
            raise ValueError(f"Unsupported kernel: {self.kernel}")

        # Average and return log
        log_density = torch.log(kernel_vals.mean(dim=1) + 1e-12)  # [n_eval]
        return log_density

    def sample(self, n_samples):
        """Draw samples from the KDE."""
        if self.train_data is None:
            raise ValueError("Model must be fit before sampling.")

        device = self.train_data.device
        idx = torch.randint(0, self.n_samples, (n_samples,), device=device)
        base_samples = self.train_data[idx]
        noise = torch.randn_like(base_samples) * self.bandwidth
        return base_samples + noise