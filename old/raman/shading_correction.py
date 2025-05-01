import numpy as np
import matplotlib.pyplot as plt
import multiprocessing as mp
import os
import sys
module_path = os.path.abspath(os.getcwd())
if module_path not in sys.path:
    sys.path.append(module_path)
    
from basicpy import BaSiC
from ...utils import execute_indexed_parallel_mp


def performing_correction_basic(images):

    basic = BaSiC(get_darkfield=True)

    basic.fit(images)

    transformed = basic.transform(images)
    
    return transformed, basic.flatfield, basic.darkfield, basic.baseline


def shading_correction_basic(images: np.ndarray, pool, plot=False):
    
    result = execute_indexed_parallel_mp(pool, performing_correction_basic, args=[(images[:, i, :, :].reshape((images.shape[0], *images.shape[-2:])),) for i in range(images.shape[1])])
    
    for i, (transformed, flatfield, darkfield, baseline) in result:
        images[:, i, :, :] = transformed

    if plot:
        for i, (_, flatfield, darkfield, baseline) in result:
            fig, axes = plt.subplots(1, 3, figsize=(9, 3))
            im = axes[0].imshow(flatfield)
            fig.colorbar(im, ax=axes[0])
            axes[0].set_title("Flatfield")
            im = axes[1].imshow(darkfield)
            fig.colorbar(im, ax=axes[1])
            axes[1].set_title("Darkfield")
            axes[2].plot(baseline)
            axes[2].set_xlabel("Frame")
            axes[2].set_ylabel("Baseline")
            fig.suptitle(i)
            fig.tight_layout()
        for i in range(images.shape[1]):
            fig, axes = plt.subplots(1, 2, figsize=(6, 3), sharex=True, sharey=True)
            im = axes[0].imshow(images[10, i, :, :])
            fig.colorbar(im, ax=axes[0])
            axes[0].set_title("Original")
            im = axes[1].imshow(images[10, i, :, :])
            fig.colorbar(im, ax=axes[1])
            axes[1].set_title("Corrected")
            fig.tight_layout()
            
        plt.show()
        
    return images

