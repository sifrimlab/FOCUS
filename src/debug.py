import numpy as np
import os, tqdm
import matplotlib.pyplot as plt
import preprocessing.lipidomics as lipidomics
import preprocessing.raman as raman
import xml.etree.ElementTree as ET
from constants import ImzMLFileParser

def read_ibm_file(path: str, sample_id: str, modality_name: str) -> tuple[np.ndarray, np.ndarray]:

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

    if mz_dtype is None:
        mz_dtype = np.float64 if intensities_dtype == None else intensities_dtype
    if intensities_dtype is None:
        intensities_dtype = mz_dtype

    run = root.find(ImzMLFileParser.RUN_KEY)
    spectrum_list = run.find(ImzMLFileParser.SPECTRUM_LIST_KEY)
    spectra = spectrum_list.findall(ImzMLFileParser.SPECTRUM_KEY)

    # Decode the binary data from the imzML file
    parsed_spectra = [lipidomics.spectra_to_dict(spectrum) for spectrum in spectra]

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

    return mzs, intensities
            

if __name__ == "__main__":
    '''PATH = "/mnt/data/lorenzo/VSC_DATA/Jelle"
    SAMPLE_ID_LIST = [
        "LG001-RAW",
        "LG002-RAW",
    ]
    MODALITY_NAME = "msi"
    OUTPUT_PATH_LIST = [os.path.join(PATH, sample_id, 'preprocessing', MODALITY_NAME) for sample_id in SAMPLE_ID_LIST]

    global_mzs, global_intensities = [], []

    for sample_id in SAMPLE_ID_LIST:
        mzs, intensities = read_ibm_file(PATH, sample_id, MODALITY_NAME)
        global_mzs.append(mzs)
        global_intensities.append(intensities)

    # Define an omogenous M/Z array that aggregates datapoints to achieve a common spectrum
    reference_mz = []
    for sample_index in range(len(SAMPLE_ID_LIST)):
        reference_mz.append(lipidomics.compute_reference_mz(global_mzs[sample_index], mass_tollerance=10, frequency_threshold=0.01))
        print(f"Found {len(reference_mz[-1])} unique M/Z values from sample {SAMPLE_ID_LIST[sample_index]}: [{min(reference_mz[-1])}, {max(reference_mz[-1])}]")

    # Now process again the unified M/Z to reach a global consensus across all samples
    global_reference_mz = lipidomics.compute_reference_mz(reference_mz, mass_tollerance=10, frequency_threshold=0.0)    # No frequency threshold because the frequncies will be more or less homogeneous
    print(f"Found {len(global_reference_mz)} global unique M/Z values: [{min(global_reference_mz)}, {max(global_reference_mz)}]")

    lipidomics.preprocess_lipidomics(
        path = PATH,
        sample_id= SAMPLE_ID_LIST[0],
        modality_name = MODALITY_NAME,
        peak_picking= True,
        prominence=0.01,
        window_tolerance=10,
        dynamic_window=True,
        dynamic_window_factor = 1e6,
        reference_mz = global_reference_mz,
    )'''

    PATH = "/mnt/data/lorenzo/VSC_DATA/Nina"
    SAMPLE_ID = "00103993-1"
    MODALITY_NAME = "raman"

    INPUT_PATH = os.path.join(PATH, SAMPLE_ID, MODALITY_NAME)
    OUTPUT_PATH =  os.path.join(PATH, SAMPLE_ID, 'preprocessing', MODALITY_NAME)
    FILENAME = f"{SAMPLE_ID}.lif"

    # Load LIF file to obtain the tiles for each image and the relevant metadata
    raman_data = raman.RamanImage(filename = os.path.join(INPUT_PATH, FILENAME))

    raman_data._basic_corrected_tiles = raman_data.raw
    raman_data.process_raw_tiles()
