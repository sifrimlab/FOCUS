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


PATH = "/staging/leuven/stg_00077/projects/Lorenzo/FOCUS/p_PDA/d_C1"
MODALITY_NAME = "Raman"
SAMPLE_ID_LIST = [
    #"PDAC001T1",
    #"PDAC001T2",
    #"PDAC002N0",
    #"PDAC002T1",
    #"PDAC002T2",
    #"PDAC003N0",
    "PDAC003T0",
    #"PDAC004N0",
    #"PDAC007N0",
    #"PDAC007T1",
    ##"PDAC007T2",
    #"PDAC010N0",
    #"PDAC010T0",
    #"PDAC011N0",
    #"PDAC011T0",
]

MERGED_DATASET_PATH = os.path.join(PATH, "merged", "preprocessing", f"{MODALITY_NAME}_merged.h5ad")

test_sample = raman.RamanImage(
    source_path=PATH,
    sample_id="PDAC003T0",
    modality_name=MODALITY_NAME,
    max_workers=8,
    container_engine=ContainerEngine.PODMAN,
)

test_sample.load_source()
test_sample.basic_correct()
#test_sample._quick_stitch()
test_sample._raman_corrected_tiles = test_sample._basic_corrected_tiles
test_sample.ashlar_stitch()