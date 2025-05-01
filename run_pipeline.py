import yaml, argparse, os
import numpy as np
from pathlib import Path

import src.preprocessing.he.crop_he as read_he
import src.preprocessing.maldi.read_metadata as read_maldi

import src.registration.register_maldi_to_he as register_maldi_to_he

DO_RAMAN = True

if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(description='Run the pipeline for RAMAN-MALDI-H&E processing.')
    parser.add_argument('-c', '--config', type=str, default='config.yaml', help='Name of the configuration file.', required=False)
    parser.add_argument('-r', '--registration', type=str, default='f', help='Run only registration.', required=False)
    
    args = parser.parse_args()
    config_name = args.config
    registration = args.registration == 't'
    
    print('config name')
    print(config_name)
    
    config_path = Path(__file__).absolute().parent / 'configs'
    config_file = config_path / config_name
    if not config_file.is_file():
        raise FileNotFoundError(f"Configuration file {args.config} not found.")
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
        path = os.path.expandvars(config['path'])
        sample = config['sample']
        run_basic = config['run_BaSiC']
        p_poly = config['p_poly']
        iterations_raman_cropping = config['iterations_raman_cropping']
        
    he_physical_size_x = he_physical_size_y = msi_physical_size_x = msi_physical_size_y = raman_physical_size_x = raman_physical_size_y = None
    
    if not registration:
        print('Cropping H&E image')
        he_physical_size_x, he_physical_size_y = read_he.read_he(f'{path}/{sample}/h&e/')
        print(f'Physical size H&E pixel: ({he_physical_size_x} μm, {he_physical_size_y} μm)')
        
        print('Converting .imzML file to .npy')
        dtype = np.float32
        read_maldi.read_imzml_file(f'{path}/{sample}/maldi/', sample, dtype)
        print(f'Physical size MSI pixel: ({msi_physical_size_x} μm, {msi_physical_size_y} μm)')
        
        '''if DO_RAMAN == True:
            # raman preprocessing    
            print('Running ASHLAR')
            # run ashlar
            raman_physical_size_x, raman_physical_size_y = run_ashlar(f'{path}/{sample}/raman/')
            print(f'Physical size Raman pixel: ({raman_physical_size_x} μm, {raman_physical_size_y} μm)')
            
            crop_raman(f'{path}/{sample}/raman/ashlar.npy', iterations=iterations_raman_cropping)
            
            print('Raman: baseline correction')
            remove_baseline_raman(f'{path}/{sample}/raman/', 'ashlar_crop.npy', p_poly=p_poly)
            
            print('Raman: smoothing')
            smooth_sample(f'{path}/{sample}/raman/baseline_corrected_{p_poly}.npy', w=9, p=2)'''
    
    # registration
    print('Register MALDI to H&E')
    he_physical_size_x, he_physical_size_y = (1.0, 1.0)
    msi_physical_size_x, msi_physical_size_y = (10.0, 10.0)
    register_maldi_to_he.register_maldi_to_he(path, sample, (he_physical_size_x, he_physical_size_y), (msi_physical_size_x, msi_physical_size_y))
    try:
        #register_maldi_to_he(path, sample, (he_physical_size_x, he_physical_size_y), (msi_physical_size_x, msi_physical_size_y))
        print('Registration successful!')
    except:
        pass
    
    '''if DO_RAMAN == True:
        data_path = f'{path}/{sample}/raman/ashlar_crop.npy'
        print('Register RAMAN to H&E')
        try:
            register_raman_to_he(path, sample, data_path, (he_physical_size_x, he_physical_size_y), (raman_physical_size_x, raman_physical_size_y))
            print('Registration successful!')
        except:
            pass
    
    '''