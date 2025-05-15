import os, torch, cv2
import numpy as np
import tensorly as tl
import matplotlib.pyplot as plt
from readlif.reader import LifFile, LifImage
import xml.etree.ElementTree as ET
from torch.functional import F
from sklearn.cluster import KMeans

def compute_wavenumbers(lambda_begin: float, lambda_end: float, lambda_steps: float, lamnda_stokes: float) -> np.ndarray[np.float32]:
    '''
    Compute the wavenumbers array based on the LIF File metadata
    
    Parameters
    ----------
    lambda_begin : float
        The starting wavelength in nm.
    lambda_end : float
        The ending wavelength in nm.
    lambda_steps : float
        The number of steps in the wavelength range.
    lambda_stokes : float
        The Stokes wavelength in nm.
    '''

    # Compute the laser pump wavelength
    pump_wavelength = np.linspace(lambda_begin, lambda_end, lambda_steps)

    # Compute the Raman wavenumbers
    raman_wavenumbers = ((1 / pump_wavelength) - (1 / lamnda_stokes)) * 1e7

    return raman_wavenumbers

def parse_lif_metadata(lif: LifFile) -> list[dict]:
    
    # Obtain the XML root element from the LifFile object
    root = lif.xml_root

    # Obtain a list of Raman Scans
    top_level_elements = root.findall('./Element')
    if not top_level_elements:
        top_level_elements = root.findall('.')
        if root.tag != 'Element':
                top_level_elements = root.findall('.//Element')

    # Filter out the elements that do not represent scans
    elements_to_process = []
    for top_element in top_level_elements:
        children_tag = top_element.find('Children')
        if children_tag is not None:
            for image_element in children_tag.findall('Element'):
                elements_to_process.append(image_element)
        else:
                if top_element.find('./Data/Image') is not None:
                    elements_to_process.append(top_element)

    # Fallback in case the XML structure is collapsed to a single level
    if not elements_to_process and root.tag == 'LMSDataContainerHeader':
        root_children_tag = root.find('Children')
        if root_children_tag is not None:
            for image_element in root_children_tag.findall('Element'):
                elements_to_process.append(image_element)
        elif root.find('./Element/Data/Image') is not None :
                for image_element in root.findall('Element'):
                    if image_element.find('./Data/Image') is not None:
                        elements_to_process.append(image_element)

    # No elements to process or unexpected structure
    if not elements_to_process:
        raise ValueError("No elements found in the LIF file or unexpected XML structure.")

    result = []

    # Iterate over each element to extract metadata
    for i, element in enumerate(elements_to_process):
        element_name = element.get('Name', f"Unnamed Element {i+1}")
        data_image_tag = element.find('./Data/Image')
        
        metadata = {
            "scan_name": element_name,
            "lambda_steps": None,
            "lambda_begin": None,
            "lambda_end": None,
            "scan_height": None,
            "scan_width": None,
            "laser_type": None,
            "lambda_stokes": None,
            "tile_number": None,
            "tiles_location": None,
        }

        # Get the dimensions
        if data_image_tag is not None:
            image_description = data_image_tag.find('ImageDescription')
            if image_description is not None:                
                dimensions = image_description.find('Dimensions')
                if dimensions is not None and len(list(dimensions)) > 0:
                    for dim_desc in dimensions.findall('DimensionDescription'):
                        id = int(dim_desc.get('DimID', None))
                        size = int(dim_desc.get('NumberOfElements', None))

                        # Interpret the dimension ID for standard Leica LIF files
                        if id == 1:
                            metadata["scan_height"] = size
                        elif id == 2:
                            metadata["scan_width"] = size
                        elif id == 9:
                            metadata["lambda_steps"] = size
                        elif id == 10:
                            metadata["tile_number"] = size

        # Define potential paths for ATLConfocalSettingDefinition
        atl_confocal_paths = [
            './Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition',
            './Data/Image/Attachment[@Name="HardwareSetting"]/LDM_Block_Sequential/LDM_Block_Sequential_Master/ATLConfocalSettingDefinition'
        ]

        found_lambda, found_stokes = False, False

        # Extract Begin and End for Lambda Steps + Lambda Stokes (constant)
        for _, atl_path in enumerate(atl_confocal_paths):
            atl_confocal_setting_def = element.find(atl_path)
            if atl_confocal_setting_def is not None:
                if not found_lambda: 
                    lambda_definition = atl_confocal_setting_def.find('LambdaDefinition')
                    if lambda_definition is not None and len(list(lambda_definition)) > 0:
                        lambda_excitation = lambda_definition.find('LambdaExcitation')
                        
                        if lambda_excitation is not None:
                            metadata["lambda_begin"] = float(lambda_excitation.get('LambdaExcitationBeginDouble', None))
                            metadata["lambda_end"] = float(lambda_excitation.get('LambdaExcitationEndDouble', None))
                            found_lambda = True

                # Extract Lambda Stokes (Pump Wavelength)
                if not found_stokes:
                    laser_array = atl_confocal_setting_def.find('LaserArray')
                    if laser_array is not None:
                        lasers_with_pumpwavelength = []
                        for laser_tag in laser_array.findall('Laser'):
                            pump_wavelength = laser_tag.get('PumpWavelength')
                            if pump_wavelength is not None:
                                laser_name = laser_tag.get('LaserName', 'Unknown Laser')
                                lasers_with_pumpwavelength.append(
                                    f"<Laser Name='{laser_name}' PumpWavelength='{pump_wavelength}' />"
                                )
                        
                        if lasers_with_pumpwavelength:
                            for entry in lasers_with_pumpwavelength:
                                # Parse the entry string into an XML element
                                entry_element = ET.fromstring(entry)
                                metadata["laser_type"] = entry_element.get('Name', None)
                                metadata["lambda_stokes"] = float(entry_element.get('PumpWavelength', None))
                                found_stokes = True
            if found_lambda and found_stokes:
                break
        
        # If the image represents a tile scan, extract info about the tiles
        if metadata["tile_number"] is not None and metadata["tile_number"] > 1:
            tiles = element.findall('./Data/Image/Attachment[@Name="TileScanInfo"]/Tile')
            tiles_metadata = []

            for tile in tiles:
                tiles_metadata.append({
                    "x": float(tile.get('PosX')),
                    "y": float(tile.get('PosY')),
                })
            
            metadata["tiles_location"] = tiles_metadata

        # Append the metadata to the list
        result.append(metadata)

    return result

def load_raman_lif(file: str) -> list[dict]:
    '''
    Load Raman Spectroscopy Imageing data from a Leica LIF file.

    Parameters
    ----------
    file : str
        Path to the LIF file.

    Returns
    ----------
    lif_data : list[dict]
        List of dictionaries containing the image data and metadata.
        Each dictionary contains:
            - "data": List of tiles, each tile is a 3D numpy array (height, width, lambda_steps).
            - "metadata": Dictionary with metadata for the corresponding image.
    '''
    
    lif_file = LifFile(file)
    metadata_array = parse_lif_metadata(lif_file)

    lif_data: list[dict] = []

    # Iterate over the images following the order written in metadata
    for index, metadata in enumerate(metadata_array):

        # Extract only tiled images, ignore automatic stitching
        if metadata["tile_number"] is None or metadata["tile_number"] < 2:
            continue

        # Read the image
        image: LifImage = lif_file.get_image(index)
        tiles: list[np.ndarray[np.float32]] = []

        # Read the tiles
        for tile_idx in range(metadata["tile_number"]):
            tile = np.zeros((metadata["scan_width"], metadata["scan_height"], metadata["lambda_steps"]), dtype=np.float32)

            # For each tile, iterate over the spectral dimensions
            for spectral_idx in range(metadata["lambda_steps"]):
                # Read the image data for each lambda step
                plane = image.get_plane(display_dims=(1, 2), c = 0, requested_dims = {9: spectral_idx, 10: tile_idx})

                # Convert the plane to a numpy array and normalize it between 0 and 1
                plane = np.array(plane, dtype=np.float32)
                plane = cv2.normalize(plane, None, 0, 1, cv2.NORM_MINMAX)
                tile[:, :, spectral_idx] = plane

            # Save the tile
            tiles.append(tile)

        # Save the tile
        lif_data.append({"data": tiles, "metadata": metadata, "wavenumbers": compute_wavenumbers(metadata["lambda_begin"], metadata["lambda_end"], metadata["lambda_steps"], metadata["lambda_stokes"])})

    return lif_data
            

if __name__ == "__main__":
    PATH = "/mnt/data/lorenzo/VSC_DATA/Nina"
    SAMPLE_ID = "00103993-1"
    MODALITY_NAME = "raman"

    INPUT_PATH = os.path.join(PATH, SAMPLE_ID, MODALITY_NAME)
    OUTPUT_PATH =  os.path.join(PATH, SAMPLE_ID, 'preprocessing', MODALITY_NAME)

    lif_data = load_raman_lif(os.path.join(PATH, SAMPLE_ID, MODALITY_NAME, f"{SAMPLE_ID}.lif"))

    print(lif_data)

    print(lif_data[0])
