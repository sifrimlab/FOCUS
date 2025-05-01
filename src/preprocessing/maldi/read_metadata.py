import os
import numpy as np
import xml.etree.ElementTree as ET

from .constants import ImzMLFileParser
from .interpolation import maldi_windowed_mapping, compute_reference_mz
from tqdm import tqdm

def read_imzml_file(path: str, sample: str, dtype: type = np.float32) -> tuple[float, float]:
    '''
    Read the imzML file to obtain the MSI experiment metadata. Returns the physical size
    of each detected spot in μm.

    Parameters
    ----------

    path : str
        Path to the imzML file. The first imzML file found in this directory will be used.
    dtype : type
        Data type of the output array. Default is np.float32.

    Returns
    -------
    tuple[float, float]
        Physical size of each detected spot in μm. (x, y)
    '''

    # Check input parameters types
    if type(path) != str or type(dtype) != type or type(sample) != str:
        raise TypeError('Invalid input types. Expected (str, str, type).')
    
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

    # Define an omogenous M/Z array that aggregates datapoints to achieve a common spectrum
    #unified_mz_values = np.arange(np.floor(low_mz), np.ceil(high_mz), 1)
    unified_mz_values = compute_reference_mz([(mzs[i], intensities[i]) for i in range(len(mzs))])

    # Define the final data matrix to store the intensities values
    merged_intensities = np.zeros((len(intensities), len(unified_mz_values)), dtype = final_dtype)

    for index in tqdm(range(0, len(mzs)), desc="Interpolating intensities"):
        # Interpolate the intensities values to the unified M/Z values
        merged_intensities[index, :] = maldi_windowed_mapping(mzs[index], intensities[index], unified_mz_values, 20)

    # Parse the coordinates from the imzML file
    coordinates = [(metadata["x"], metadata['y']) for metadata in parsed_spectra]
    coordinates = np.array(coordinates, dtype = np.int32)

    # Save the processing output
    save_numpy_matrix(path = path, sample = sample, reference_mz = unified_mz_values, intensities = merged_intensities, coordinates = coordinates)

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


    # Save the reference M/Z values
    np.save(os.path.join(path, f'{sample}_reference_mz.npy'), reference_mz)

    # Save the intensities values
    np.save(os.path.join(path, f'{sample}_intensities.npy'), intensities)

    # Save the coordinates values
    np.save(os.path.join(path, f'{sample}_coordinates.npy'), coordinates)


if __name__ == "__main__":
    path = '/mnt/data/lorenzo/VSC_DATA/Jelle/0001/maldi'

    import matplotlib.pyplot as plt

    # Check if the reference file exists
    reference_intensities, reference_mzs, intensities, mzs = read_imzml_file(path)
        
    print(f"Ref. Intensities shape: {reference_intensities.shape}")
    print(f"Ref. M/Zs shape: {reference_mzs.shape}")
    print(f"Intensities shape (for element 0): {(len(intensities), intensities[0].shape)}")
    print(f"M/Zs shape (for element 0): {(len(mzs), mzs[0].shape)}")

    # Sum all the intensities for each spectrum
    reference_intensities = np.average(reference_intensities, axis=0)



    # Plot an histogram with mzs value as x-axis and intensities as y-axis
    plt.figure(figsize=(10, 5))
    plt.bar(mzs[0], intensities[0])
    plt.xlabel('M/Z')
    plt.ylabel('Intensity')
    plt.title('First Spectrum')
    plt.show()

    plt.figure(figsize=(10, 5))
    # Plot the reference spectrum
    plt.bar(reference_mzs, reference_intensities[0])
    plt.xlabel('M/Z')
    plt.ylabel('Intensity')
    plt.title('Reference Spectrum')
    plt.show()