import pandas as pd
import re
from readlif.reader import LifFile


def get_wavelength(lif_file, begin="LambdaExcitationBeginDouble", end="LambdaExcitationEndDouble", length=7):
    
    xml_metadata = lif_file.xml_header
    #begin
    pattern_begin = re.compile(f'{begin}(.{{{length}}})')
    WL_begin = pattern_begin.findall(xml_metadata)
    WL_begin = [float(item.strip('="')) for item in WL_begin]
    
    #end
    pattern_end = re.compile(f'{end}(.{{{length}}})')
    WL_end = pattern_end.findall(xml_metadata)
    WL_end = [float(item.strip('="')) for item in WL_end]
    
    return WL_begin, WL_end

def get_wavenumber(tuned_wavelength, fixed_wavelength=1032):  #SRS modality
    return (1 / tuned_wavelength - 1 / fixed_wavelength) * 1e7

def describe_lif_file(lif_file):
    names = get_scan_names(lif_file)

    WL_begin, WL_end = get_wavelength(lif_file)
    
    dim_list = []
    wavenumber_list = []
    for i in range(len(names)):
        my_dict = lif_file.get_image(i).info["dims_n"]
        values_list = [value for value in my_dict.values()]
        dim_list.append(str(values_list))
        wavenumber_list.append(my_dict[9])
    df = pd.DataFrame({
    "Name": names,
    "Begin (nm)": WL_begin,
    "End (nm)": WL_end,
    "Dims": dim_list,
    "Wavenumber": wavenumber_list
    }) 
    
    return df

def get_scan_names(leica_file):
    image_ls = leica_file.image_list
    names = []
    for i in image_ls:
        names.append(i["name"])
    return names
    
    
if __name__ == '__main__':
    
    import os
    lif_file = LifFile(f'{os.getcwd()}/data/00071300/raman/20241024_PDAC_T008_00071300.lif')
    
    df = describe_lif_file(lif_file)
    print(df)
    
