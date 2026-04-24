# Scientific Overview

## The spatial multiomics integration challenge

Modern spatial biology experiments increasingly combine multiple measurement modalities acquired on the same tissue section or on consecutive serial sections from the same tissue block. A representative experiment might include H&E or fluorescence microscopy for high-resolution morphological context, mass spectrometry imaging (MSI) to characterize the tissue lipidome at cellular resolution, Raman spectroscopy for label-free biochemical fingerprinting, and spatial transcriptomics to quantify spatially resolved gene expression. Each of these modalities captures a distinct and complementary dimension of tissue biology that the others cannot access.

The fundamental challenge in jointly analyzing such datasets is that each modality is acquired on a different instrument, at a different spatial resolution, and in a different coordinate system. Microscopy images may have pixel sizes of 0.1–1 µm, while MSI spot sizes are typically 10–100 µm and spatial transcriptomics capture areas may be 55 µm (Visium) or sub-cellular (Xenium, MERFISH). The raw coordinate systems are instrument-specific and bear no direct relationship to one another. Before any joint analysis is possible, all modalities must be co-registered — that is, mapped into a single, shared spatial coordinate frame — so that a feature vector from any modality can be unambiguously associated with a specific location in the tissue.

This co-registration problem is technically demanding: it requires handling heterogeneous file formats, bridging differences in spatial resolution spanning one to two orders of magnitude, and producing transformations that are accurate enough to preserve the biological signal of interest. Existing tools either handle only one modality transition or require substantial programming expertise, creating a barrier for biologists who are domain experts in their instruments but not in image registration or spatial data engineering.

---

## Design goals of FOCUS

FOCUS was designed around five principles that together address the full scope of the integration challenge:

1. **Modality-agnostic preprocessing with domain-specific algorithms.** Each instrument type requires a different quality-control and normalization strategy. FOCUS implements dedicated preprocessing modules for each supported modality — including consensus m/z alignment for MSI, BaSiC illumination correction and ASHLAR tile stitching for Raman, and leiden clustering with mitochondrial-gene filtering for spatial transcriptomics — while exposing a uniform pipeline interface.

2. **Interactive, expert-guided alignment rather than fully automated approaches.** Fully automated image registration algorithms can fail when modalities have very different appearances (e.g., comparing an ion image to a fluorescence micrograph) or when tissue morphology is complex. FOCUS instead uses an interactive alignment GUI in which the user visually overlays the reference modality onto the target modality using drag controls (translation, rotation, and scale). This approach is robust to appearance differences and gives the domain expert direct control over registration quality. The transformation is recorded automatically once the user confirms the alignment.

3. **Flexible registration strategies matched to modality type.** After alignment, FOCUS uses two fundamentally different strategies to map features onto the reference coordinate grid. For high-resolution image modalities, deep-learning patch embeddings capture rich morphological and spectral information at each reference spot location. For sparse omics modalities, Gaussian-weighted spatial interpolation transfers feature vectors from instrument measurement locations onto the reference grid with minimal information loss. Both strategies produce per-modality AnnData objects with harmonized observation indices.

4. **No programming required for end users; full Python API for developers.** The entire pipeline is driven by a JSON configuration file and an interactive web GUI, so biologists can run FOCUS without writing any code. The same pipeline is fully accessible programmatically through a Python API for developers who need to integrate FOCUS into automated workflows, extend its modality support, or perform custom post-processing.

5. **Output in open, interoperable formats compatible with the established ecosystem.** FOCUS writes results as AnnData (`.h5ad`) and MuData (`.h5mu`) files, which are the standard data containers in the scanpy and squidpy single-cell and spatial omics ecosystem. This means that the output of FOCUS can be loaded directly into any analysis workflow that uses these packages, without additional conversion steps.

---

## Supported modalities and their scientific role

### Microscopy (brightfield and fluorescence)

Fluorescence and brightfield microscopy provide the highest-resolution spatial context in a typical spatial multiomics experiment. Fluorescence staining (e.g., DAPI for nuclei, phalloidin for actin) or histological staining (H&E) reveals tissue morphology, cell-type distribution, and spatial organization at micrometer or sub-micrometer resolution. In FOCUS, microscopy images are processed into multi-resolution OME-TIFF pyramids during preprocessing. During registration, a pretrained pathology vision model (Prov-GigaPath) encodes 224 × 224 px patches centered at reference spot locations into 1536-dimensional feature embeddings that capture local morphological context. Because of its fine spatial resolution and rich landmark content, the microscopy image is the most common choice for the reference modality.

### MSI (mass spectrometry imaging)

Mass spectrometry imaging spatially resolves the molecular composition of a tissue section without the need for labels or antibodies. Each MSI pixel records the intensity of hundreds to thousands of lipid species (identified by their mass-to-charge ratio, m/z) at a spatial resolution of approximately 10–100 µm. Data are acquired separately in positive and negative ion mode, which together cover complementary subsets of the lipidome. Input data are in the vendor-neutral imzML format (metadata file `.imzML` + binary data file `.ibd`). FOCUS performs consensus m/z alignment across spots and samples, intensity normalization, and background removal during preprocessing, producing a sparse AnnData matrix of ion intensities. Spot interpolation is used during registration to transfer MSI features onto the reference coordinate grid.

### Raman spectroscopy imaging

Raman spectroscopy imaging provides spatially resolved, label-free biochemical fingerprinting of tissue based on inelastic light scattering. Each spatial location yields a full vibrational spectrum from which the presence and relative abundance of proteins, lipids, nucleic acids, and other biomolecules can be inferred — without staining or prior knowledge of analyte identity. Raman data from Leica `.lif` files are processed using BaSiC illumination correction (run in a dedicated conda environment) and ASHLAR stitching to assemble individual tile scans into a hyperspectral OME-TIFF image. During registration, Raman spectral data at pixel locations near each reference spot are aggregated via Gaussian-weighted averaging. As specialized feature extraction methods for Raman spectroscopy become available, this will be upgraded to extract richer spectral embeddings rather than simple averaging.

### Spatial transcriptomics (Visium, Xenium, MERFISH, and others)

Spatial transcriptomics technologies measure the expression levels of hundreds to tens of thousands of genes at spatially defined locations within a tissue section. FOCUS accepts spatial transcriptomics data as AnnData `.h5ad` files, which is the standard export format from 10x Genomics Space Ranger (Visium), Xenium Explorer, and most computational pipelines for MERFISH and seqFISH data. During preprocessing, FOCUS applies library-size normalization, log-transformation, and Leiden clustering to the gene expression matrix. If the spatial transcriptomics modality is chosen as the reference, its spot grid directly defines the coordinate frame; otherwise, spot interpolation maps gene expression onto the reference grid.

---

## Pipeline architecture

FOCUS implements a four-stage architecture that separates concerns clearly and produces checkpointed intermediate files at every stage, enabling resumption from any point without reprocessing.

### Stage 1 — Preprocessing

Each modality is preprocessed independently by a dedicated module that applies instrument-appropriate algorithms. Outputs are written as OME-TIFF pyramids (image modalities) or AnnData `.h5ad` files (omics modalities) under `{sample_id}/preprocessing/{modality_name}/`. Preprocessing parameters are fully configurable per modality in the JSON configuration file.

### Stage 2 — Alignment

Alignment is performed interactively for each sample and each non-reference modality. The web-based alignment GUI (served locally at `http://localhost:8000`) displays the reference and target modalities side by side. The user drags the reference modality to visually align it with the target; FOCUS records the transformation and stores it as an additional spatial key in the reference modality AnnData (`.obsm['{non_ref_name}_spatial']`). Modalities that share a coordinate system with the reference can be configured as `pre_aligned`, bypassing the GUI.

### Stage 3 — Registration

Registration uses the alignment transforms to map each modality's feature content onto the reference coordinate grid. Microscopy images use **feature extraction** (pretrained vision model patch embeddings); omics modalities and Raman use **Gaussian-weighted spot interpolation**:

$$
\hat{f}(r) = \frac{\sum_{i=1}^{k} w_i \cdot f(s_i)}{\sum_{i=1}^{k} w_i}, \quad w_i = \exp\!\left(-\frac{\|r - s_i\|^2}{2\sigma^2}\right)
$$

where $r$ is the reference spot location expressed in the target modality's coordinate space, $s_i$ are the $k$ nearest target spots, $f(s_i)$ is the feature vector at spot $s_i$, and $\sigma$ is set proportional to the target modality's `spot_size`. The output is a per-modality AnnData with `.obsm['spatial']` aligned to the reference frame.

### Stage 4 — Compilation

All registered per-modality AnnData files are merged into a single MuData object. Observation indices are harmonized so that row $i$ in every modality corresponds to the same reference spot. If spatial annotations (QuPath-style GeoJSON region files) are provided, region labels are assigned to each spot using polygon intersection and stored in `.obs['spatial_annotation']`. The final file is written to `{dataset_path}/merged/multimodal_dataset.h5mu`.

!!! note "Algorithm details"
    Detailed descriptions of the algorithms used in each stage — including the m/z consensus procedure for MSI, the Gaussian interpolation kernel, and the patch extraction strategy — are provided in the individual scientific method pages linked from the navigation.
