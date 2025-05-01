import numpy as np
import numba as nb
import os
import tifffile

nb.core.entrypoints.init_all = lambda: None

@nb.njit
def remove_spikes(y, window=10, factor=10):
    for i in range(y.shape[0]):
        for j in range(0, y.shape[1], window):
            w = np.copy(y[i, j: j + window])
            w -= np.median(w)
            med = np.median(np.abs(w))
            if med == 0:
                continue
            w /= med
            spike = w > factor
            if spike.sum() > 0:
                indices = np.flatnonzero(spike)
                for index in indices:
                    if j + index + 1 == y.shape[1]:
                        y[i, j + index] = 2 * y[i, j + index - 1] - y[i, j + index - 2]
                    elif j + index - 1 == 0:
                        y[i, 0] = 2 * y[i, 1] - y[i, 2]
                    else:
                        y[i, j + index] = (y[i, j + index - 1] + y[i, j + index + 1]) / 2
    
    return y

def spike_removal(path: str, filename: str):
    
    nb_tilescans = len(list(filter(lambda x: os.path.isdir(f'{path}/{x}') and x.startswith('tilescan_'), os.listdir(path))))
    
    size_wavenumbers = np.ndarray((nb_tilescans,), dtype=np.uint32)
    for i in range(nb_tilescans):
        for file in os.listdir(f'{path}/tilescan_{i}/'):
            if file.startswith('series'):
                break
        
        d = tifffile.imread(f'{path}/tilescan_{i}/{file}')
        
        size_wavenumbers[i] = d.shape[0]
    
    data = np.float32(np.load(f'{path}/{filename}'))
    sz = data.shape
    
    data = data.reshape((data.shape[0] * data.shape[1], data.shape[2]))

    current = 0
    
    # Perform spike removal separately for the different tilescans
    for i in range(nb_tilescans):
        print(f'Removing spikes from tilescan {i} with {size_wavenumbers[i]} wavenumbers.')
        
        data[:, current: current + size_wavenumbers[i]] = remove_spikes(data[:, current: current + size_wavenumbers[i]])
            
        current += size_wavenumbers[i]
            
    np.save(f'{path}/spikes_removed.npy', data.reshape(sz))
    
def spike_removal_after_basic(data, window=10, factor=10):
    
    for i in range(data.shape[0]):
        d = data[i, :, :, :]
        
        d = d.T
        
        sz = d.shape
        
        d = d.reshape((d.shape[0] * d.shape[1], d.shape[2]))
            
        d = remove_spikes(d, window, factor)
        
        d = d.reshape(sz)
        
        data[i, :, :, :] = d.T
            
    return data


if __name__ == '__main__':
    
    
    # path = f'{os.environ["VSC_SCRATCH"]}/RAMALDI/00103993-1/raman'
    # filename = 'ashlar.npy'
    # spike_removal(path, filename)
    
    data = np.random.randint(0, 255, size=(512, 512, 64, 3)).T
    data = np.float32(data)
    spike_removal_after_basic(data)
        
    