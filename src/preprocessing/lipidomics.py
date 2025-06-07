import numpy as np
import torch, os
from tqdm import tqdm, trange
import xml.etree.ElementTree as ET

from constants import ImzMLFileParser

def preprocess_lipidomics(path: str, sample_id: str, modality_name: str, peak_picking: bool, prominence: float, window_tolerance: float, dynamic_window: bool, dynamic_window_factor: float, reference_mz: np.ndarray | None = None) -> None:
	'''
	Read the imzML file to obtain the MSI experiment metadata.

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
	reference_mz : np.ndarray | None
		Reference M/Z values to use for peak picking. If None, a consensus M/Z vector will be computed from the spectra.

	'''

	# Check input parameters types
	if type(path) != str or type(sample_id) != str or type(modality_name) != str or not isinstance(peak_picking, bool) or \
		not isinstance(prominence, float) or not isinstance(window_tolerance, int) or \
			not isinstance(dynamic_window, bool) or not isinstance(dynamic_window_factor, float):
		raise TypeError('Invalid input types. Expected str, bool, float, float, bool, float.')
	
	if isinstance(reference_mz, np.ndarray) == False and reference_mz is not None:
		raise TypeError('Invalid input type for reference_mz. Expected np.ndarray or None.')
	
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

	mz_dtype, intensities_dtype = None, None

	# Define utility to convert string to dtype
	str_to_dtype = lambda s: np.float32 if s == "32-bit float" else np.float64

	for rpg in root.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_LIST):
		if rpg.attrib['id'] in ['mzArray']:
			for cv_param in rpg:
				if "float" in cv_param.attrib['name']:
					mz_dtype = str_to_dtype(cv_param.attrib['name'])
		elif rpg.attrib['id'] in ['intensities', "intensityArray"]:
			for cv_param in rpg:
				if "float" in cv_param.attrib['name']:
					intensities_dtype = str_to_dtype(cv_param.attrib['name'])

	if mz_dtype is None or intensities_dtype is None:
		raise KeyError("Could not find the data types for mz and intensities in the imzML file. Check the metadata name")

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
		if reference_mz is None:
			# Define an omogenous M/Z array that aggregates datapoints to achieve a common spectrum
			unified_mz_values = compute_reference_mz([(mzs[i], intensities[i]) for i in range(len(mzs))], mass_tollerance = window_tolerance, frequency_threshold = prominence)
		else:
			# Use the provided reference M/Z values
			unified_mz_values = reference_mz

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

		if element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] in ['mzArray']:
			mzs = {
				'length': length,
				'encoded_length': encoded_length,
				'offset': offset
			}
		elif element.find(ImzMLFileParser.REFERENCEABLE_PARAM_GROUP_REF).attrib['ref'] in ['intensities', "intensityArray"]:
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
	intensities_matrix = np.zeros((coordinates[:, 0].max(), coordinates[:, 1].max(), reference_mz.shape[0]), dtype = intensities.dtype)
	for i in range(coordinates.shape[0]):
		intensities_matrix[coordinates[i, 0] - 1, coordinates[i, 1] - 1, :] = intensities[i, :]

	# Save the reference M/Z values
	np.save(os.path.join(path, f'{sample}_reference_mz.npy'), reference_mz)

	# Save the intensities values
	np.save(os.path.join(path, f'{sample}_intensities_matrix.npy'), intensities_matrix)

def maldi_windowed_mapping(original_mz: np.ndarray, original_intensity: np.ndarray, reference_mz: np.ndarray, ppm_tolerance: int = 20, dynamic_window: bool = True, dynamic_window_factor: float = 1e6) -> np.ndarray:
	"""
	GPU-accelerated MALDI intensity mapping using variable-size windowing.

	Parameters
	----------
	original_mz : np.ndarray
		Original m/z values with shape (N, ).
	original_intensity : np.ndarray
		Corresponding intensity values with shape (N, ).
	reference_mz : np.ndarray
		Target reference m/z values with shape (M, ).
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

def compute_tolerance_matrix(input: torch.Tensor) -> torch.Tensor:
	"""
	Scan the tolerance matrix and select to which row to uniquely assign an M/Z value (column).
	This method takes a boolean matrix of shape (N, M) that represents the overlapping clusters of M/Z values.
	It determines to which cluster to assign an M/Z value based on the weight of the clusters.
	This ensures that each M/Z value that falls within multiple clusters is always assigned to the cluster with the highest density.
	
	Parameters:
	-----------
		input: torch.Tensor
			A boolean tensor of shape (N, M) where N is the number of rows (clusters) and M is the number of columns (M/Z values).
		
	Returns:
	-----------
		torch.Tensor
			A boolean tensor of shape (N, M) where each column has a single True value indicating the selected row for that M/Z value.
			All other values are False.
	"""

	# Create boolean mask from non-zero values
	bool_mask = (input != 0)
	
	# Calculate row sums of numeric values
	row_sums = input.sum(dim=1)  # Shape: (N,)
	
	# Create scoring matrix with -inf for zeros
	score = torch.where(bool_mask, row_sums.unsqueeze(1), -torch.inf)
	
	# Find best rows per column
	max_indices = score.argmax(dim=0)
	
	# Identify active columns
	has_nonzero = bool_mask.any(dim=0)
	
	# Build output mask
	mask = torch.zeros_like(bool_mask)
	valid_cols = has_nonzero.nonzero().squeeze(-1)
	
	if valid_cols.numel() > 0:
		mask[max_indices[valid_cols], valid_cols] = True
		
	return mask

def compute_reference_mz(spectra_list: list[np.ndarray], mass_tollerance: int = 10, frequency_threshold: float = 0.01, batch_size: int = 10000) -> np.ndarray:
	"""
	Create consensus reference m/z vector using adaptive mass tolerance
	Reference: 10.1021/acs.analchem.0c03833

	Parameters:
	-----------

	spectra_list : list of np.ndarray
		List of m/z arrays from different spectra.
	mass_tollerance : int
		Mass tolerance in ppm for grouping m/z values.
	frequency_threshold : float
		Minimum frequency threshold for m/z values to be included in the consensus.

	Returns:
	-----------

	consensus_mz : np.ndarray
		Consensus m/z values after grouping and filtering.
	"""

	# Group the m/z values from all spectra and count occurrences
	all_mz = np.concatenate(spectra_list)
	all_mz = all_mz.astype(np.float32)                                  # Ensure all m/z values are float32 for consistency
	all_mz = np.round(all_mz, decimals = 5)                               # Round m/z values to 5 decimal places to reduce numerical noise
	all_mz.sort()
	unique_mz, counts = np.unique(all_mz, return_counts=True)

	# Cast unique_mz and counts to float32 for consistency
	unique_mz = unique_mz.astype(np.float32)
	counts = counts.astype(np.float32)

	unique_mz = torch.from_numpy(unique_mz).float().cuda()
	counts = torch.from_numpy(counts).float().cuda()

	# Get the total length of unique m/z values
	total_length = unique_mz.shape[0]

	# Store the totals
	total_unique_mz, total_weights = None, None

	print(f"Total unique m/z values: {total_length}. The process will iterate as long as there are overlapping clusters.\n")

	# Iterate over the unique m/z values and create consensus peaks
	while total_unique_mz is None or torch.equal(total_unique_mz, unique_mz) == False:

		# Define the new unique_mz as the result of the previous iteration
		if total_unique_mz is not None:
			unique_mz = total_unique_mz
			counts = total_weights
			total_length = unique_mz.shape[0]

			# Reset the totals for the next iteration
			total_unique_mz, total_weights = None, None

		for batch_start in trange(0, total_length, batch_size, desc = "Computing consensus m/z", unit = "batch"):

			# Get the batch slice
			batch_end: int = min(batch_start + batch_size, total_length)

			unique_mz_batch: torch.Tensor = unique_mz[batch_start:batch_end]
			counts_batch: torch.Tensor = counts[batch_start:batch_end]

			# Computing adaptive mass tolerance windows around each m/z to compute the overlapping clusters
			tolerance_mask = torch.zeros((unique_mz_batch.shape[0], unique_mz_batch.shape[0]), dtype=bool)
			tolerance_mask = torch.abs(unique_mz_batch[:, None] - unique_mz_batch[None, :]) <= (unique_mz_batch[:, None] * mass_tollerance * 1e-6)

			# Count the number of overlapping clusters
			cluster_mz = torch.where(tolerance_mask, unique_mz_batch[None, :], torch.nan)
			cluster_weights = torch.where(tolerance_mask, counts_batch[None, :], 0)

			# Compute a unicity filter mask
			unicity_mask = compute_tolerance_matrix(cluster_weights)

			# Apply the unicity mask to the clusters
			cluster_mz = torch.where(unicity_mask, cluster_mz, torch.nan)
			cluster_weights = torch.where(unicity_mask, cluster_weights, torch.nan)

			centroid_mz = torch.nanmean(cluster_mz, axis = 1)
			centroid_weights = torch.nansum(cluster_weights, axis = 1)

			# Filter duplicated m/z values and sum their intensities
			unique_centroid_mz = torch.unique(centroid_mz)
			unique_weights = torch.zeros_like(unique_centroid_mz, dtype=counts_batch.dtype)
			weights_matrix = centroid_weights * (centroid_mz == unique_centroid_mz[:, None])
			weights_matrix[weights_matrix == 0] = torch.nan
			unique_weights = torch.nanmean(weights_matrix, axis=1)

			# Get the indices of NaN values
			nan_indices = torch.isnan(unique_centroid_mz)
			# Remove NaN values
			unique_centroid_mz = unique_centroid_mz[~nan_indices]
			unique_weights = unique_weights[~nan_indices]

			if total_unique_mz is None:
				total_unique_mz = unique_centroid_mz
				total_weights = unique_weights
			else:
				# Concatenate the results
				total_unique_mz = torch.concatenate((total_unique_mz, unique_centroid_mz))
				total_weights = torch.concatenate((total_weights, unique_weights))

			del unique_mz_batch, counts_batch, tolerance_mask, cluster_mz, cluster_weights, unique_centroid_mz, unique_weights
			del unicity_mask, centroid_mz, centroid_weights, weights_matrix
			torch.cuda.empty_cache()
			torch.cuda.synchronize()

	# Apply a frequency threshold to filter out low-frequency m/z values
	if frequency_threshold > 0:
		peak_indices: torch.Tensor = _find_peaks_torch(total_weights, prominence_factor = frequency_threshold)
		consensus_mz: torch.Tensor = total_unique_mz[peak_indices]
	else:
		consensus_mz: torch.Tensor = total_unique_mz

	consensus_mz_cpu = consensus_mz.cpu().numpy()

	# Free GPU memory
	del unique_mz, counts, total_unique_mz, total_weights, consensus_mz
	torch.cuda.empty_cache()
	torch.cuda.synchronize()

	return consensus_mz_cpu

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
