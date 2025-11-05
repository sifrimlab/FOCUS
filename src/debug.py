# %%
import sys, os, tifffile, tqdm, anndata
import numpy as np
import matplotlib.pyplot as plt

# Include src directory in the path dynamically
notebook_dir = os.path.abspath("")
src_path = os.path.join(notebook_dir, "../src")
sys.path.append(src_path)

import preprocessing.microscopy_image as mi

# %%
PATH = "/staging/leuven/stg_00077/projects/Lorenzo/FOCUS/p_PDA/d_C1"
MODALITY_NAME = "HE"

SAMPLE_ID_LIST = [
    "PDAC001T1",
    "PDAC001T2",
    "PDAC002N0",
    "PDAC002T1",
    "PDAC002T2",
    "PDAC003N0",
    "PDAC003T0",
    "PDAC004N0",
    "PDAC007N0",
    "PDAC007T1",
    "PDAC007T2",
    #"PDAC010N0",
    "PDAC010T0",
    "PDAC011N0",
    "PDAC011T0",
]

# %%
patch_extractor = mi.PatchEmbeddingExtractor(hf_token = "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")

# %%
samples: list[mi.MicroscopyImage] = []

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
# Create merged AnnData object
adata_list = []
for sample in samples:
    adata = anndata.read_h5ad(os.path.join(sample.output_folder, f"{sample.sample_id}_{sample.modality_name}.h5ad"))
    adata.var_names_make_unique()
    adata.obs_names_make_unique()
    adata_list.append(adata)

# Merge all AnnData objects into one
merged_adata = anndata.concat(adata_list, label="sample_id", join="outer", merge="unique", index_unique='_')

if not os.path.exists(os.path.join(PATH, "merged", "preprocessing")):
    os.makedirs(os.path.join(PATH, "merged", "preprocessing"))

# Save the merged AnnData object
merged_adata.write_h5ad(os.path.join(PATH, "merged", "preprocessing", f"{MODALITY_NAME}.h5ad"))

# %%
for sample in samples:
    # read the image
    with tifffile.TiffFile(os.path.join(sample.output_folder, f"{sample.sample_id}_{sample.modality_name}_processed.ome.tiff")) as tif:
        hires_image = tif.asarray()

    # Read the patch embeddings
    embeddings = anndata.read_h5ad(os.path.join(sample.output_folder, f"{sample.sample_id}_{sample.modality_name}.h5ad"))

    plt.figure(figsize=(10, 10))
    plt.imshow(hires_image)
    plt.scatter(
        embeddings.obsm['spatial'][:, 0],
        embeddings.obsm['spatial'][:, 1],
        c='red',
        s=5,
        alpha=0.5
    )
    plt.show()


