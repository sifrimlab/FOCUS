import yaml
import argparse
import os

from pathlib import Path

from preprocessing.raman.read_images_lif import read_lif


if __name__ == '__main__':
    
    parser = argparse.ArgumentParser(description='Run the pipeline for RAMAN-MALDI-H&E processing.')
    parser.add_argument('-c', '--config', type=str, default='config.yaml', help='Name of the configuration file.', required=False)
    
    args = parser.parse_args()
    config_name = args.config
    
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
        
    print("Extracting data from .lif and applying BaSiC.")
    # extract data from .lif and run basic
    liffile = list(filter(lambda x: x.endswith('.lif'), os.listdir(f'{path}/{sample}/raman/')))
    if len(liffile) == 0:
        raise FileNotFoundError(f"No .lif file found in {path}/{sample}/raman/")
    read_lif(f'{path}/{sample}/raman/', liffile[0], run_basic=run_basic)
    
