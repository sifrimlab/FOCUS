import numpy as np
import cv2
import tifffile

from sklearn.decomposition import PCA

from .registration_algo import Registration
        

class RegistrationRamanToHE(Registration):
    
    def __init__(self, raman: np.ndarray, he: np.ndarray, spacing_raman: np.ndarray, spacing_he: np.ndarray, path: str) -> None:
        
        super().__init__(raman, he, spacing_raman, spacing_he, path)
        
def get_pixels_part_of_unmeasured_patch(data, sz):
    idxs = np.sum(data, axis=1) == 0
    
    zero_patch = np.zeros((data.shape[0],), dtype=np.uint8)
    zero_patch[idxs] = 1
    zero_patch = np.reshape(zero_patch, (sz[0], sz[1]))
    
    kernel = np.ones((3, 3), zero_patch.dtype)
    zero_patch = cv2.dilate(zero_patch, kernel, iterations=3)
    
    return zero_patch.reshape((np.prod(zero_patch.shape),))

def perform_pca_on_measured_patches(data, sz, zero_patch, k=1):
    d = np.copy(data).reshape((np.prod(sz[: 2]), sz[2]))
    X = np.copy(d)[np.logical_not(zero_patch), :]
    reducer = PCA(n_components=k)
    for i in range(X.shape[0]):
        X[i, :] /= np.sum(X[i, :])
    X = reducer.fit_transform(X)
    X -= np.min(X)
    X /= np.max(X)
    result = 255 * np.ones((np.prod(sz[:2]), k))
    result[np.logical_not(zero_patch), :] = 255 * X
    return result.reshape((*sz[: 2], k))
    
def register_raman_to_he(path, sample, data_path, spacing_he, spacing_raman):
    
    he = tifffile.imread(f'{path}/{sample}/h&e/{sample}_crop.tiff')
    he_image = np.average(he, axis=2)
    
    raman_data = np.load(data_path).astype(np.float32)
    
    k = 1
    sz = raman_data.shape
    d = np.copy(raman_data).reshape((np.prod(raman_data.shape[: 2]), raman_data.shape[2])) 
    zero_patch = get_pixels_part_of_unmeasured_patch(d, sz)
    
    raman_image = np.average(perform_pca_on_measured_patches(d, sz, zero_patch, k), axis=2)
    
    reg = RegistrationRamanToHE(raman_image, he_image, spacing_raman, spacing_he, f"{path}/registration/{sample}/raman")
    reg.set_error_measure('AdvancedMattesMutualInformation')
    reg.compute_transformation(only_affine=False, only_fiducials=False)
        
    grid_x, grid_y = np.meshgrid(np.arange(he_image.shape[1]), np.arange(he_image.shape[0]))
    raman_coordinates_he_space = np.stack([reg.apply_transformation(grid_x), reg.apply_transformation(grid_y)], axis=2)
    
    np.save(f"{path}/{sample}/raman/raman_coordinates.npy", raman_coordinates_he_space)
    
    return raman_coordinates_he_space
    
    
if __name__ == '__main__':
    
    import os
    sample = '00103993-1'
    path = os.environ['VSC_SCRATCH'] + f'/RAMALDI/'
    data_path = f'{path}/{sample}/raman/ashlar_crop.npy'
    register_raman_to_he(path, sample, data_path, (1, 1), (1, 1))
    
    
    