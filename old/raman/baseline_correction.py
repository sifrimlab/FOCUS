import numpy as np
import numba as nb
import scipy as sp
import os
import multiprocessing as mp
import sys
import tifffile
import time
module_path = os.path.abspath(os.getcwd())
if module_path not in sys.path:
    sys.path.append(module_path)

from ...utils import execute_indexed_parallel_mp

from pybaselines.api import Baseline
import scipy.sparse as sps

nb.core.entrypoints.init_all = lambda: None

# For information on the baseline correction implemented you can always contact me, see paper for (improved) als: https://www.researchgate.net/publication/262804999_Baseline_Correction_for_Raman_Spectra_Using_Improved_Asymmetric_Least_Squares 

def baseline_als(y, λ: np.float32=np.float32(1e7), p: np.float32=np.float32(0.05), niter: int=15):
    L = y.shape[0]
    one = np.float32(1)
    D = sp.sparse.diags([1, -2, 1], [0, -1, -2], shape=(L, L - 2), dtype=y.dtype)
    D = λ * D.dot(D.transpose()) # Precompute this term since it does not depend on `w`
    w = np.ones(L, dtype=y.dtype)
    prev_diag = np.zeros_like(w, dtype=y.dtype)
    W = sp.sparse.spdiags(w, 0, L, L)
    for _ in range(niter):
        if np.linalg.norm(w - prev_diag) < 1e-5:
            break
        W.setdiag(w) # Do not create a new matrix, just update diagonal values
        z = sp.sparse.linalg.spsolve(W + D, w * y)
        prev_diag = w
        w = p * (y > z) + (one - p) * (y < z)
    return z

def baseline_als_full(y, λ: np.float32=np.float32(1e7), p: np.float32=np.float32(0.05), niter: int=15):
    L = len(y)
    one = np.float32(1)
    # D_pre = sp.sparse.diags([1, -2, 1], [0, -1, -2], shape=(L, L - 2), dtype=y.dtype).toarray()
    col = np.zeros((L,))
    col[: 3] = np.array([1, -2, 1])
    row = np.zeros((L-2,))
    row[0] = 1
    D_pre = sp.linalg.toeplitz(col, row)
    D_pre = λ * np.dot(D_pre, D_pre.T) # Precompute this term since it does not depend on `w`
    D = np.ndarray((5, L))
    for i in range(5):
        D[i, np.max([0, 2 - i]): np.min([L, L + 2 - i])] = np.diagonal(D_pre, offset=2 - i)
    w = np.ones(L, dtype=y.dtype)
    prev_diag = np.zeros_like(w, dtype=y.dtype)
    W = np.zeros_like(D)
    W[2, :] = w
    for _ in range(niter):
        if np.linalg.norm(w - prev_diag) < 1e-5:
            break
        W[2, :] = w
        z = sp.linalg.solve_banded((2, 2), D + W, w * y)
        prev_diag = w
        w = p * (y > z) + (one - p) * (y < z)
    return z

def baseline_als_improved(y, λ1=1e7, λ=1e7, p=0.05, niter=15):
    # (W.T W + λ1 D1 + λ D) z = (W.T W + λ1 D1) y
    L = len(y)
    D = sp.sparse.diags([1, -2, 1], [0, -1, -2], shape=(L, L - 2))
    D1 = sp.sparse.diags([-1, 1], [0, -1], shape=(L, L - 1))
    D = λ * D.dot(D.transpose()) # Precompute this term since it does not depend on `w`
    D1 = λ1 * D1.dot(D1.transpose())
    DD = D + D1
    D1y = D1 @ y
    w = np.ones(L, dtype=y.dtype)
    diag = np.zeros_like(w, dtype=y.dtype)
    W = sp.sparse.spdiags(w, 0, L, L)
    for _ in range(niter):
        prev_diag = diag
        diag = w ** 2
        if np.linalg.norm(diag - prev_diag) < 1e-5:
            break
        W.setdiag(diag) # Do not create a new matrix, just update diagonal values
        Z = W + DD
        z = sp.sparse.linalg.spsolve(Z, W @ y + D1y)
        w = p * (y > z) + (1 - p) * (y < z)
    return z

def remove_baseline(y: np.ndarray, λ: np.float32=np.float32(1e7), p: np.float32=np.float32(0.05), niter: int=10) -> np.ndarray:
    return y - baseline_als(y, λ, p, niter)

@nb.njit
def baseline_polynomial(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    A = np.power(np.arange(y.shape[1], dtype=y.dtype)[:, np.newaxis] / y.shape[1], np.arange(p + 1, dtype=y.dtype))
    x, residuals, rank, s = np.linalg.lstsq(A, y.T)
    result = (A @ x).T
    temp = y - result
    for i in range(result.shape[0]):
        result[i, :] += np.min(temp[i, :])
    return result

def remove_baseline_polynomial(y: np.ndarray, p: np.ndarray) -> np.ndarray:
    return y - baseline_polynomial(y, p)

def remove_baseline_raman(path: str, filename: str, p_poly: int=0):
    
    nb_tilescans = len(list(filter(lambda x: os.path.isdir(f'{path}/{x}') and x.startswith('tilescan_'), os.listdir(path))))
    
    size_wavenumbers = np.ndarray((nb_tilescans,), dtype=np.uint32)
    for i in range(nb_tilescans):
        for file in os.listdir(f'{path}/tilescan_{i}/'):
            if file.startswith('series'):
                break
        
        d = tifffile.imread(f'{path}/tilescan_{i}/{file}')
        
        size_wavenumbers[i] = d.shape[0]

    # λ = np.float32(1e1)
    # p = np.float32(0.0001)
    
    with mp.Pool(processes=mp.cpu_count() // 2) as pool:
        
        for p in [p_poly]:
            
            data = np.float32(np.load(f'{path}/{filename}'))
            sz = data.shape
            
            data = data.reshape((data.shape[0] * data.shape[1], data.shape[2]))

            current = 0
            
            # Quite slow for large raman images even with mp -> Dask?
            # Perform baseline correction separately for the different tilescans
            for i in range(nb_tilescans):
                print(f"Performing baseline correction for Tilescan {i + 1}")
                s = time.perf_counter_ns()
                
                # # Similar in speed
                # baseline_solver = Baseline()
                # result = execute_indexed_parallel_mp(pool, baseline_solver.asls, args=[(data[j, current: current + size_wavenumbers[i]], λ, p) for j in range(data.shape[0])])
                # for j, d in result:
                #     data[j, current: current + size_wavenumbers[i]] -= d[0]
                
                # result = execute_indexed_parallel_mp(pool, remove_baseline, args=[(data[j, current: current + size_wavenumbers[i]], λ, p) for j in range(data.shape[0])])
                # for j, d in result:
                #     data[j, current: current + size_wavenumbers[i]] = d
                
                # Polynomial
                data[:, current: current + size_wavenumbers[i]] = remove_baseline_polynomial(data[:, current: current + size_wavenumbers[i]], p)
                
                print(f'\tPerformed baseline correction on {data.shape[0]} pixels with {size_wavenumbers[i]} channels in {np.round((time.perf_counter_ns() - s) / 1e9, decimals=2)} seconds.')
                    
                current += size_wavenumbers[i]
                    
            np.save(f'{path}/baseline_corrected_{p}.npy', data.reshape(sz))


if __name__ == '__main__':
    
    
    # path = './data/00033464/raman'
    # main(path)
    
    # path = './data/00071845/raman'
    # main(path)
        
    # path = './data/ito/raman'
    # main(path)
    
    path = './data/00071300/raman'
    filename = 'ashlar.npy'
    remove_baseline_raman(path, filename)
        
    