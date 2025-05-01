from scipy.signal import savgol_filter
import numpy as np
import os


def smooth_sample(path, w, p):
        
    data = np.load(f'{path}')
        
    data = savgol_filter(data, w, p, axis=2)
    
    data = np.clip(data, a_min=0, a_max=None)
    
    np.save(f"{path.replace('baseline_corrected', 'smooth')}", data)


if __name__ == "__main__":
    
    w = 9
    p = 2
    
    # sample = '00033464'
    
    # sample = '00071845'
    
    # sample = 'ito'
    
    sample = '00071300'
    
    for p_poly in range(1):
        path = f'{os.getcwd()}/data/{sample}/raman/baseline_corrected_{p_poly}.npy'
        
        smooth_sample(path, w, p)

