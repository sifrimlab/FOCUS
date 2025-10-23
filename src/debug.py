# %%
import sys, os, tifffile, tqdm
import numpy as np
import matplotlib.pyplot as plt

# Include src directory in the path dynamically
notebook_dir = os.path.abspath("")
src_path = os.path.join(notebook_dir, "../src")
sys.path.append(src_path)

import preprocessing.microscopy_image as mi
from constants import ContainerEngine

# %%
PATH = "/mnt/data/lorenzo/FOCUS/ARNEO/"
MODALITY_NAME = "HE"

SAMPLE_ID_LIST = [
    "BPH1",
    "BPH2",
    "BPH3",
    "BPH4",
    "CRPC1",
    "CRPC2",
    "CRPC3",
    "CRPC4",
    "CRPC5",
]

# %%
patch_extractor = mi.PatchEmbeddingExtractor(hf_token = "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")

# %%
samples = []

# Create the samples
for sample_id in SAMPLE_ID_LIST:
	sample = mi.MicroscopyImage(
		source_path=PATH,
		sample_id=sample_id,
		modality_name=MODALITY_NAME,
		patch_extractor=patch_extractor
	)
	samples.append(sample)

# %%
for sample in tqdm.tqdm(samples, desc="Processing microscopy images"):
    sample.process_image(min_tissue_area=5000)

# %%
for sample in samples:
    with tifffile.TiffFile(os.path.join(sample.output_folder, f"{sample.sample_id}_{sample.modality_name}_processed.ome.tiff")) as tif:
        image = tif.asarray()

    plt.figure(figsize=(10, 10))
    plt.imshow(image)
    plt.axis('off')
    plt.show()


