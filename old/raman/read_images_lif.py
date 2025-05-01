import bioformats as bf
import javabridge as jv
import skimage.io as skio
import os
import numpy as np
import multiprocessing as mp
import re

from typing import List
from xml.etree import ElementTree as ETree
from readlif.reader import LifFile

from .shading_correction import shading_correction_basic
from .spike_removal import spike_removal_after_basic
    
    
def start(max_heap_size='8G'):
    """Start the Java Virtual Machine, enabling bioformats IO.

    Parameters
    ----------
    max_heap_size : string, optional
        The maximum memory usage by the virtual machine. Valid strings
        include '256M', '64k', and '2G'. Expect to need a lot.
    """
    jv.start_vm(class_path=bf.JARS, max_heap_size=max_heap_size, run_headless=True)
    jv.JClassWrapper('loci.common.DebugTools').enableLogging('ERROR')

def done():
    """Kill the JVM. Once killed, it cannot be restarted.

    Notes
    -----
    See the python-javabridge documentation for more information.
    """
    jv.kill_vm()

def parse_metadata(path):
    md = bf.get_omexml_metadata(path)
    mdroot = ETree.fromstring(md)
    metadata_list = []
    metadata = []
    name = ''

    for child in mdroot:
        if child.tag.endswith('Image') and 'tilescan' in child.attrib['Name'].lower():
            if 'merged' in name.lower():
                metadata = []
            if child.attrib['Name'] != name and len(metadata) > 0:
                metadata_list.append(metadata)
                metadata = []
            
            name = child.attrib['Name']
            for grandchild in child:
                if grandchild.tag.endswith('Pixels'):
                    
                    for ggrandchild in grandchild:
                        if ggrandchild.tag.endswith("Plane"):
                            metadata.append({
                                            "Name": child.attrib['Name'],
                                            "ID": grandchild.attrib['ID'], 
                                            "PhysicalSizeX": grandchild.attrib["PhysicalSizeX"], 
                                            "PhysicalSizeXUnit": grandchild.attrib["PhysicalSizeXUnit"], 
                                            "PhysicalSizeY": grandchild.attrib["PhysicalSizeY"], 
                                            "PhysicalSizeYUnit": grandchild.attrib["PhysicalSizeYUnit"],
                                            "Type": grandchild.attrib["Type"],
                                            "PositionX": ggrandchild.attrib["PositionX"], 
                                            "PositionXUnit": ggrandchild.attrib["PositionXUnit"], 
                                            "PositionY": ggrandchild.attrib["PositionY"], 
                                            "PositionYUnit": ggrandchild.attrib["PositionYUnit"]
                            })
                            break
    if 'merged' not in name.lower():
        metadata_list.append(metadata)
    
    return metadata_list

def get_wavelength(lif_file: LifFile, indices: List[int], axis_wavenumber: int=9, begin: str="LambdaExcitationBegin", end: str="LambdaExcitationEnd", step: str="LambdaExcitationStepSize", step_count: str='LambdaExcitationStepCount', pattern: str='LambdaExcitation'):
    
    number_of_wavelengths = np.array([lif_file.get_image(i).dims_n[axis_wavenumber] for i in range(lif_file.num_images)])[np.array(indices)]
    
    xml_metadata = lif_file.xml_header
     
    pattern_start = f'<{pattern}'
    start_excitation = [m.start() for m in re.finditer(pattern_start, xml_metadata)]
    pattern_stop = f'</{pattern}>'
    stop_excitation = [m.start() for m in re.finditer(pattern_stop, xml_metadata)]
    
    strings = [xml_metadata[start_excitation[i]: stop_excitation[i]] for i in range(len(start_excitation))]
    
    strings = list(filter(lambda x: begin in x and end in x and step in x, strings))
    
    filtered_strings = []
    for s in strings:
        t = s.split(f'{step_count}="')[-1]
        t = int(t.split('"')[0])
        if t == number_of_wavelengths[len(filtered_strings)]:
            filtered_strings.append(s)
        if len(filtered_strings) == number_of_wavelengths.shape[0]:
            break
    
    wl_begin = []
    wl_end = []
    wl_step = []
    for s in filtered_strings:
        # wl begin
        if f'{begin}Double' in s: 
            t = s.split(f'{begin}Double="')[-1]
        else:
            t = s.split(f'{begin}="')[-1]
        t = t.split('"')[0]
        wl_begin.append(t)
        
        # wl end
        if f'{end}Double' in s: 
            t = s.split(f'{end}Double="')[-1]
        else:
            t = s.split(f'{end}="')[-1]
        t = t.split('"')[0]
        wl_end.append(t)
        
        # wl step
        t = s.split(f'{step}="')[-1]
        t = t.split('"')[0]
        wl_step.append(t)
    
    return wl_begin, wl_end, wl_step

def get_scan_names(lif_file: LifFile):
    image_ls = lif_file.image_list
    names = []
    for i in image_ls:
        names.append(i["name"])
    return names

def get_wavenumber(tuned_wavelength, fixed_wavelength=1032):  #SRS modality
    return (1 / tuned_wavelength - 1 / fixed_wavelength) * 1e7

def get_wavelength_vector(lif_file: LifFile, indices: List[int]):
    
    wl_begin, wl_end, wl_step = get_wavelength(lif_file, indices)
    
    vector = []
    for i in range(len(indices)):
        number_of_steps = int(np.round((float(wl_end[i]) - float(wl_begin[i])) / float(wl_step[i]))) + 1
        decimals = np.max([len(wl_begin[i].split('.')[-1]), len(wl_end[i].split('.')[-1]), len(wl_step[i].split('.')[-1])])
        vector += [np.round(float(wl_begin[i]) + float(wl_step[i]) * j, decimals=decimals) for j in range(number_of_steps)]
    
    return np.array(vector)
    
def get_indices(names: List[str]):
    return list(filter(lambda x: 'tilescan' in names[x].lower() and 'merged' not in names[x].lower(), range(len(names))))

def read_lif(path_to_file: str, filename: str, axis_wavenumber: int=9, axis_tiles: int=10, run_basic: bool=True):
    
    lif_file = LifFile(f'{path_to_file}/{filename}')
    names = get_scan_names(lif_file)
    indices = get_indices(names)
    
    start('16G')
    metadata_list = parse_metadata(f'{path_to_file}/{filename}')
    done()
    
    wavelength_vector = get_wavelength_vector(lif_file, indices)
    np.save(f'{path_to_file}/wavelength_vector.npy', wavelength_vector)
        
    # Generate filenames for all the images in a tilescan
    filenames = []
    image = lif_file.get_image(indices[0])
    for series_id in range(image.dims_n[axis_tiles]):
        sid = ''.join([str(0) for _ in range(len(str(image.dims_n[axis_tiles])) - len(str(series_id)))]) + str(series_id)
        filenames.append(f"series_{sid}.tiff")
    
    with mp.Pool(processes=mp.cpu_count() // 2) as pool:
        
        for i, index in enumerate(indices):
            print(f'Tilescan {i + 1} of {len(indices)}')
            img = lif_file.get_image(index)

            tile_list = []
            for j in range(img.dims_n[axis_tiles]):
                plane_list = []
                
                for k in range(img.dims_n[axis_wavenumber]):
                    plane = img.get_plane(requested_dims = {axis_wavenumber: k, axis_tiles: j})  #not for 16 bit files
                    plane = np.array(plane)
                    plane_list.append(plane.reshape((1, *plane.shape)))
                tile = np.concatenate(plane_list, axis=0)
                tile_list.append(tile.reshape((1, *tile.shape)))
            image = np.concatenate(tile_list, axis=0)
                
            if not os.path.exists(f"{path_to_file}/tilescan_{i}/"):
                os.makedirs(f"{path_to_file}/tilescan_{i}/")
            
            if run_basic:
                image = shading_correction_basic(np.float32(image), pool, plot=False)
                
                image = spike_removal_after_basic(image)
                print('Spikes removed.')
                
                # ASHLAR only handles uint8 and uint16 so scale and cast
                image = np.uint16((2 ** 16 - 1) * (image - np.min(image)) / (np.max(image) - np.min(image)))
                
            image = np.swapaxes(image, axis1=2, axis2=3)
                
            for j in range(img.dims_n[axis_tiles]):
                metadata = metadata_list[i][j]
                skio.imsave(f'{path_to_file}/tilescan_{i}/{filenames[j]}', image[j, :, :, :], photometric='minisblack', metadata=metadata)
                
        metadata = metadata_list[0][0]
        return metadata['PhysicalSizeX'], metadata['PhysicalSizeY']


if __name__ == '__main__':
        
    # path_to_file = './data/00033464/raman'
    # filename = '20240617_PDAC_00033464.lif'
    
    # path_to_file = './data/00071845/raman'
    # filename = '20240618_00071815.lif'
    
    # path_to_file = './data/ito/raman'
    # filename = '20240619_ITO.lif'
    
    # path_to_file = './data/00033464/raman' 
    # filename = '20240617_PDAC_00033464.lif'
    
    # path_to_file = './data/test/raman'
    # filename = 'PDAC_fingerprint_test.lif'
    
    path_to_file = './data/00071300/raman'
    filename = '20241024_PDAC_T008_00071300.lif'
    
    read_lif(path_to_file, filename)
    
    
