# Transform .imzml file to matrix
# Melanie NIJS

import numpy as np
import xml.etree.ElementTree as ET
import time
import os
import multiprocessing as mp

from collections import namedtuple

from .interpolation import interpolate
from ...utils import execute_indexed_parallel_mp

type_map = {
    "64-bit float": np.float64,
    "32-bit float": np.float32
}
    
def read_imzml(path, dtype, low_mz, high_mz):
    
    ext = '.imzML'
        
    with mp.Pool(processes=mp.cpu_count()) as pool:
        for name in os.listdir(path):
            if name.endswith(ext):
                name = name.split(ext)[0]
                PATH_SAVE = path
                fname = os.path.join(path, name)
                print(PATH_SAVE, fname)
                start = time.time()
                print('.imzml file = ', fname)
                
                # read .imzl file and extract pixels and spectra
                hupostr = '{http://psi.hupo.org/ms/mzml}'
                spectrumtup = namedtuple('spectrumtup', ['x', 'y', 'mzs', 'intensities'])
                datatup = namedtuple('datatup', ['length', 'encoded', 'offset'])

                def spectrum2dict(e):
                    x = None
                    y = None

                    scanlist = e.find(hupostr + 'scanList')
                    scan = scanlist.find(hupostr + 'scan')
                    for cvpar in scan.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'position x':
                            x = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'position y':
                            y = int(cvpar.attrib['value'])

                    bdlst = e.find(hupostr + 'binaryDataArrayList').findall(hupostr + 'binaryDataArray')
                    mzselem, spectelem = bdlst  # fixme: not robust enough, mzarray not necessarily first

                    for cvpar in mzselem.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'external array length':
                            mzlength = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external encoded length':
                            mzencoded = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external offset':
                            mzoffset = int(cvpar.attrib['value'])
                    mzs = datatup(length=mzlength, encoded=mzencoded, offset=mzoffset)

                    for cvpar in spectelem.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'external array length':
                            intlength = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external encoded length':
                            intencoded = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external offset':
                            intoffset = int(cvpar.attrib['value'])
                    intensities = datatup(length=intlength, encoded=intencoded, offset=intoffset)

                    return spectrumtup(x=x, y=y, mzs=mzs, intensities=intensities)

                def save_data():

                    xmltree = ET.parse(fname + '.imzML')
                    xmlroot = xmltree.getroot()
                    runkey = '{http://psi.hupo.org/ms/mzml}run'
                    run = xmlroot.find(runkey)
                    spectrumlistkey = '{http://psi.hupo.org/ms/mzml}spectrumList'
                    spectrumlist = run.find(spectrumlistkey)
                    spectrumkey = '{http://psi.hupo.org/ms/mzml}spectrum'
                    spectraelems = spectrumlist.findall(spectrumkey)
                
                    imzml_data = list(map(spectrum2dict, spectraelems))
                    
                    mzs = list(map(lambda x: np.memmap(filename=fname + '.ibd', dtype='float64', mode='r', offset=x.mzs.offset, shape=(x.mzs.length,)), imzml_data))
                    print(np.min(np.array(list(map(lambda x: np.min(x), mzs)))), np.max(np.array(list(map(lambda x: np.max(x), mzs)))))
                    shape = int(np.round(np.mean(np.array(list(map(lambda x: np.argmin(np.abs(high_mz - x) - np.argmin(np.abs(low_mz - x))), mzs))))))
                    mz_vector = np.arange(shape) / (shape - 1) * (high_mz - low_mz) + low_mz
                    
                    intensities = list(map(lambda x: np.float64(np.memmap(filename=fname + '.ibd', dtype='float32', mode='r', offset=x.intensities.offset, shape=(x.intensities.length,))), imzml_data))
                    
                    data_matrix = np.empty((len(mzs), shape), dtype=np.float64)
                    
                    # Multiprocessing
                    for i in range(len(mzs)):
                        data_matrix[i, :] = interpolate(mz_vector, mzs[i], intensities[i])
                    
                    row2grid = np.array(list(map(lambda p: (p.x, p.y), imzml_data)), dtype=int)

                    if dtype != np.float64:
                        data_matrix = dtype(data_matrix)
                        
                    for i in range(data_matrix.shape[0]):
                        if np.isclose(np.sum(data_matrix[i, :]), 0, atol=1e-4):
                            data_matrix[i, :] = np.ones_like(data_matrix[i, :], dtype=data_matrix.dtype)

                    np.save(PATH_SAVE + '/' + name + "_noTIC_matrix.npy", data_matrix)
                    print('Shape matrix =', data_matrix.shape)

                    np.save(PATH_SAVE + '/' + name + "_row2grid.npy", row2grid)

                    np.save(PATH_SAVE + '/' + name + "_mz_vector.npy", mz_vector if dtype == mz_vector.dtype else dtype(mz_vector))
                            
                save_data()

                end = time.time()
                timeVal = end - start
                print('finished in ' + str(timeVal) + ' seconds.')
                
def read_imzml_only_peaks(path, dtype):
    
    ext = '.imzML'
        
    with mp.Pool(processes=mp.cpu_count()) as pool:
        for name in os.listdir(path):
            if name.endswith(ext):
                name = name.split(ext)[0]
                sample = path.split("/")[-3]
                PATH_SAVE = path
                fname = os.path.join(path, name)
                start = time.time()
                print('.imzml file = ', fname)
                
                # read .imzl file and extract pixels and spectra
                hupostr = '{http://psi.hupo.org/ms/mzml}'
                spectrumtup = namedtuple('spectrumtup', ['x', 'y', 'mzs', 'intensities'])
                datatup = namedtuple('datatup', ['length', 'encoded', 'offset'])

                def spectrum2dict(e):
                    x = None
                    y = None

                    scanlist = e.find(hupostr + 'scanList')
                    scan = scanlist.find(hupostr + 'scan')
                    for cvpar in scan.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'position x':
                            x = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'position y':
                            y = int(cvpar.attrib['value'])

                    bdlst = e.find(hupostr + 'binaryDataArrayList').findall(hupostr + 'binaryDataArray')
                    mzselem, spectelem = bdlst  # fixme: not robust enough, mzarray not necessarily first

                    for cvpar in mzselem.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'external array length':
                            mzlength = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external encoded length':
                            mzencoded = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external offset':
                            mzoffset = int(cvpar.attrib['value'])
                    mzs = datatup(length=mzlength, encoded=mzencoded, offset=mzoffset)

                    for cvpar in spectelem.iter(hupostr + 'cvParam'):
                        if cvpar.attrib['name'] == 'external array length':
                            intlength = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external encoded length':
                            intencoded = int(cvpar.attrib['value'])
                        if cvpar.attrib['name'] == 'external offset':
                            intoffset = int(cvpar.attrib['value'])
                    intensities = datatup(length=intlength, encoded=intencoded, offset=intoffset)

                    return spectrumtup(x=x, y=y, mzs=mzs, intensities=intensities)

                def save_data():

                    xmltree = ET.parse(fname + '.imzML')
                    xmlroot = xmltree.getroot()
                    
                    for scansettings in xmlroot.find("{http://psi.hupo.org/ms/mzml}scanSettingsList"):
                        for cvparam in scansettings:
                            if cvparam.attrib["name"].startswith("pixel size"):
                                if cvparam.attrib["name"].endswith("x"):
                                    physical_size_x = cvparam.attrib["value"]
                                elif cvparam.attrib["name"].endswith("y"):
                                    physical_size_y = cvparam.attrib["value"]
                    
                    for referenceableParamGroup in xmlroot.find("{http://psi.hupo.org/ms/mzml}referenceableParamGroupList"):
                        if referenceableParamGroup.attrib['id'] == 'mzArray':
                            for cvparam in referenceableParamGroup:
                                if 'float' in cvparam.attrib['name']:
                                    mz_dtype = type_map[cvparam.attrib['name']]
                        elif referenceableParamGroup.attrib['id'] == 'intensities':
                            for cvparam in referenceableParamGroup:
                                if 'float' in cvparam.attrib['name']:
                                    intensities_dtype = type_map[cvparam.attrib['name']]
                    
                    runkey = '{http://psi.hupo.org/ms/mzml}run'
                    run = xmlroot.find(runkey)
                    spectrumlistkey = '{http://psi.hupo.org/ms/mzml}spectrumList'
                    spectrumlist = run.find(spectrumlistkey)
                    spectrumkey = '{http://psi.hupo.org/ms/mzml}spectrum'
                    spectraelems = spectrumlist.findall(spectrumkey)
                
                    imzml_data = list(map(spectrum2dict, spectraelems))
                    
                    dtype_most_bytes = intensities_dtype if np.dtype(intensities_dtype).itemsize >= np.dtype(mz_dtype).itemsize else mz_dtype
                    
                    mzs = list(map(lambda x: np.memmap(filename=fname + '.ibd', 
                                                       dtype=mz_dtype, 
                                                       mode='r', 
                                                       offset=x.mzs.offset, 
                                                       shape=(x.mzs.length,)).astype(dtype_most_bytes), imzml_data))
                    print(f'MSI data measured from {np.min(np.array(list(map(lambda x: np.min(x), mzs))))} Da to {np.max(np.array(list(map(lambda x: np.max(x), mzs))))} Da.')

                    high_mz = np.max(np.array(list(map(lambda x: np.max(x), mzs))))
                    low_mz = np.min(np.array(list(map(lambda x: np.min(x), mzs))))
                    
                    # Bins are of width 1 Da, which is similar to the expected distance between peaks
                    mz_vector = np.arange(np.floor(low_mz), np.ceil(high_mz))
                    
                    shape = mz_vector.shape[0]
                    
                    intensities = list(map(lambda x: np.memmap(filename=fname + '.ibd', 
                                                               dtype=intensities_dtype, 
                                                               mode='r', 
                                                               offset=x.intensities.offset, 
                                                               shape=(x.intensities.length,)).astype(dtype_most_bytes), imzml_data))
                    
                    data_matrix = np.ndarray((len(mzs), shape), dtype=dtype_most_bytes)
                    
                    # Multiprocessing
                    result = execute_indexed_parallel_mp(pool, interpolate, args=[(mz_vector, mzs[i], intensities[i]) for i in range(len(mzs))])
                    for i, d in result:
                        data_matrix[i, :] = d
                    
                    row2grid = np.array(list(map(lambda p: (p.x, p.y), imzml_data)), dtype=int)

                    if dtype != np.float64:
                        data_matrix = dtype(data_matrix)
                        
                    for i in range(data_matrix.shape[0]):
                        if np.isclose(np.sum(data_matrix[i, :]), 0, atol=1e-4):
                            data_matrix[i, :] = np.ones_like(data_matrix[i, :], dtype=data_matrix.dtype)

                    np.save(PATH_SAVE + '/' + sample + "_noTIC_matrix.npy", data_matrix if dtype == data_matrix.dtype else dtype(data_matrix))
                    print('Shape matrix =', data_matrix.shape)

                    np.save(PATH_SAVE + '/' + sample + "_row2grid.npy", row2grid)

                    np.save(PATH_SAVE + '/' + sample + "_mz_vector.npy", mz_vector if dtype == mz_vector.dtype else dtype(mz_vector))
                    
                    return physical_size_x, physical_size_y
                            
                physical_size_x, physical_size_y = save_data()

                end = time.time()
                timeVal = end - start
                print('Finished in ' + str(timeVal) + ' seconds.')
                return float(physical_size_x), float(physical_size_y)


# iterating over directory and finds .imzml (and .ibd) files
if __name__ == '__main__':
    
    for sample in ['00103993-1']:#, '00103993-2', '00103994-1', '00103994-2']:
    
        # giving directory name
        path = os.getcwd() + f'/transfer_428938_files_ee05f1c2/{sample}/maldi'
        dtype = np.float32
        
        # giving file extensions
        ext = ('.imzML')
        
        print(read_imzml_only_peaks(path, dtype))
                    