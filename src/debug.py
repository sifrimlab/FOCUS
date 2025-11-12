import os, sys, tqdm
import numpy as np
import matplotlib.pyplot as plt
import anndata as ad
import scanpy as sc
import tifffile

from sklearn.decomposition import NMF

# Include src directory in the path dynamically
notebook_dir = os.path.abspath("")
src_path = os.path.join(notebook_dir, "../src")
sys.path.append(src_path)


import preprocessing.microscopy_image as mi
import preprocessing.raman as raman
from constants import ContainerEngine

import preprocessing.lipidomics as lipidomics
from constants import MsiIntensityNormalization, DecompositionMethod, MsiIonMode

'''
PATH = "/staging/leuven/stg_00077/projects/Lorenzo/FOCUS/p_PDA/d_C1"
MODALITY_NAME = "Raman"


test_sample = raman.RamanImage(
    source_path=PATH,
    sample_id="PDAC007T1",
    modality_name=MODALITY_NAME,
    max_workers=8,
    container_engine=ContainerEngine.PODMAN,
)

test_sample.load_source()
test_sample.basic_correct()
#test_sample._quick_stitch()
test_sample._raman_corrected_tiles = test_sample._basic_corrected_tiles
test_sample.ashlar_stitch()
'''

PATH = "/staging/leuven/stg_00077/projects/Lorenzo/FOCUS/p_PDA/d_C1"
MODALITY_NAME = "MSI"
SAMPLE_ID_LIST = [
    "PDAC010N0"
]

samples: list[lipidomics.MsiSample] = []

# Load each MSI sample and unify POS and NEG ion coordinates
for SAMPLE_ID in tqdm.tqdm(SAMPLE_ID_LIST, desc="Preparing MSI samples", unit="sample"):
    msi_sample = lipidomics.MsiSample(
        source_path = PATH,
        sample_id=SAMPLE_ID,
        modality_name=MODALITY_NAME,
        double_ion_mode=True
    )
    samples.append(msi_sample)

# Create an MSI dataset from the samples
msi_dataset = lipidomics.MsiDataset(samples)

msi_dataset.process_dataset(intensity_normalization=MsiIntensityNormalization.TIC)