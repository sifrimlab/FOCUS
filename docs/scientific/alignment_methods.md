# Alignment Methods

## 1. Motivation

Spatial multiomics datasets are acquired on multiple instruments, each with its own independent coordinate system. A mass spectrometry imaging (MSI) raster, a Raman hyperspectral map, and an H&E microscopy scan of the same tissue section are expressed in incommensurable pixel- or physical-coordinate frames. Before features from different modalities can be compared at the same spatial location, corresponding positions across modalities must be identified.

FOCUS adopts a **direct-mapping** paradigm: rather than attempting automated feature detection — which is unreliable on heterogeneous tissue — the user specifies matching landmarks by eye through an interactive browser-based GUI. This expert-guided approach is robust to staining artefacts, tissue deformation, and the highly heterogeneous appearance of different modality images.

!!! note "Terminology"
    Throughout FOCUS, the modality whose spot grid defines the canonical observation index is called the **reference** (or **anchor**) modality. Every other modality is a **target** modality. Alignment maps the reference spots into each target's coordinate space; the converse is not computed.

---

## 2. Alignment Strategies

FOCUS supports two strategies, configured per non-reference modality via the `alignment_strategy` field.

### 2.1 Manual (default)

The user specifies $N \geq 1$ landmark correspondences through the interactive GUI. Landmarks are selected by clicking on matching anatomical or morphological features visible in both the reference and target panels. One GUI session is required per sample per non-reference modality.

This is the only strategy supported for image-type targets (`microscopy_image`, `raman`). For spot-type targets (`msi`, `st`), spot positions are dragged to their correct positions within the target coordinate frame.

### 2.2 Pre-aligned

The target modality is assumed to be already expressed in the same coordinate system as the reference modality — for example, a 10x Visium spatial transcriptomics dataset whose H&E image is co-registered by design. No GUI interaction occurs. The alignment stage copies `obsm['spatial']` directly into `obsm['{ref_name}_spatial']` on the target AnnData.

!!! warning "Pre-aligned compatibility"
    `pre_aligned` is only valid for spot-type targets (`msi`, `st`). Image-type targets require manual alignment.

---

## 3. Interactive Alignment GUI

The alignment GUI is a Flask web application served on `http://localhost:8000`. It is launched automatically by the pipeline when manual alignment is required and shuts down once all samples for a given modality pair have been processed.

### 3.1 Display and interaction

The GUI presents a **dual-canvas** layout:

- **Left canvas (fixed):** the reference modality for the current sample.
- **Right canvas (target):** the non-reference modality for the current sample.

For image modalities (`microscopy_image`, `raman`), the GUI loads the **lowest pyramid level** of the OME-TIFF for responsive rendering. Multi-channel images are converted to a 3-channel RGB representation: grayscale images are triplicated; two-channel images are zero-padded to three channels; images with four or more channels undergo NMF dimensionality reduction to three components.

For spot modalities (`msi`, `st`), each spot is rendered as a coloured point. Colour is derived from Leiden cluster membership, providing anatomical context to guide landmark selection.

### 3.2 Alignment modes

| Reference type | Target type | Mode | User action |
|----------------|-------------|------|-------------|
| IMAGE | IMAGE | Image-to-Image | Drag four corner handles of a bounding box on the reference image to define the crop region corresponding to the target |
| IMAGE | SPOT | Image-to-Spot | Drag individual spot positions on the right canvas to align with reference anatomy |
| SPOT | SPOT | Spot-to-Spot | Drag individual spot positions on the right canvas to align with reference spot positions |

!!! note
    SPOT → IMAGE alignment (reference is spot-based, target is image) is not currently implemented.

### 3.3 Session flow

1. Pipeline thread loads both modalities for the current sample and sends them to the GUI.
2. GUI blocks the pipeline thread until the user clicks **Confirm**.
3. The confirmed alignment result (corner pixel coordinates for IMAGE–IMAGE, or per-spot pixel coordinates for spot targets) is returned to the pipeline thread.
4. The pipeline thread scales the result from display resolution to full resolution (see Section 4.2) and saves the output.
5. If more samples remain for this modality pair, the GUI advances to the next sample automatically.

---

## 4. Coordinate Transformation

### 4.1 What the GUI produces

**Image-to-Image:** the GUI returns `corner_pixels` — a set of four $(x, y)$ pixel coordinates in the reference image at the lowest pyramid level, defining the bounding box of the region of interest. These coordinates are used to crop the full-resolution reference image to the portion that overlaps with the target.

**Spot targets (Image-to-Spot, Spot-to-Spot):** the GUI returns a list of `spots`, each containing `pixel_x` and `pixel_y` — the repositioned $(x, y)$ coordinates of each reference spot, expressed in the display (downsampled) coordinate system of the reference modality.

### 4.2 Scale recovery

The GUI operates on the lowest pyramid level of OME-TIFF images. Let $H_\text{orig}, W_\text{orig}$ be the height and width of the full-resolution image and $H_\text{low}, W_\text{low}$ the dimensions of the displayed level. The scale factors are:

$$s_y = \frac{H_\text{orig}}{H_\text{low}}, \qquad s_x = \frac{W_\text{orig}}{W_\text{low}}$$

After the user confirms the alignment, the pipeline multiplies the GUI-returned coordinates component-wise:

$$x_\text{full} = x_\text{GUI} \cdot s_x, \qquad y_\text{full} = y_\text{GUI} \cdot s_y$$

For spot modalities, the coordinates are already in physical units (µm) and no scaling is applied ($s_x = s_y = 1$).

### 4.3 Projection of reference spots into target space

The aligned coordinates represent the reference spot positions **expressed in the target modality's coordinate system**. Concretely, after alignment of reference modality $R$ against target modality $T$, the reference AnnData acquires a new obsm key:

$$\texttt{obsm}[\texttt{`}\{T\}_\text{spatial}\texttt{`}] \in \mathbb{R}^{N_R \times 2}$$

where row $i$ contains $(x_i, y_i)$ — the position of reference spot $i$ expressed in $T$'s coordinate frame.

For IMAGE–IMAGE alignment, this is a direct mapping: the GUI returns the bounding-box corner pixels of the reference image crop that corresponds to the target image, and the reference image is cropped accordingly (saved as a new OME-TIFF). For spot-target alignments, the user directly repositions each reference spot, so the output is a per-spot coordinate array in the target's space.

!!! note "No closed-form affine fitting"
    FOCUS does not fit a parametric affine matrix from landmark correspondences. Instead, the GUI operates as a **direct mapping** tool: the user drags each entity (image corners or spots) to its correct position in the target frame, and those final positions are recorded verbatim (after scale recovery). This is why the class is named `DirectMappingAligner`.

---

## 5. Output Data Structure

### 5.1 Spot-target modalities (MSI, ST)

For each sample, the preprocessed target AnnData is loaded and a new `obsm` key is added:

| Key | Shape | Description |
|-----|-------|-------------|
| `obsm['{ref_name}_spatial']` | $(N_\text{ref}, 2)$ | Reference spot positions in target coordinate space |
| `obsm['spatial']` | $(N_\text{target}, 2)$ | Target spot positions (unchanged from preprocessing) |

These per-sample files are concatenated into a merged aligned file at:

```
{dataset_path}/merged/alignment/{target_name}_merged_processed_aligned.h5ad
```

The `obsm` key accumulates across multiple alignment passes: if the reference is aligned to two targets sequentially, the target AnnData for the first pair is reloaded and extended with the second pair's key, preserving both.

### 5.2 Image-target modalities (microscopy_image, raman)

The reference image is cropped to the bounding box defined by the user and saved as a new compressed OME-TIFF:

```
{dataset_path}/{sample_id}/alignment/{target_name}_{sample_id}_processed_aligned.ome.tiff
```

### 5.3 Downstream use

The aligned coordinates in `obsm['{target_name}_spatial']` are the sole input to the registration stage. They place each reference spot at a known position in the target modality's coordinate frame, enabling feature extraction (image patches) or Gaussian interpolation (spot features) at those positions.

---

## 6. Parameter Reference

| Parameter | Scope | Options | Default | Description |
|-----------|-------|---------|---------|-------------|
| `perform_alignment` | global | bool | `true` | Execute the alignment stage |
| `alignment_force_recomputing` | global | bool | `false` | Re-run alignment even if cached output exists |
| `alignment_strategy` | per non-reference modality | `manual`, `pre_aligned` | `manual` | How alignment is performed for this modality |

!!! tip "Caching behaviour"
    FOCUS checks for existing aligned files before launching the GUI. If the expected `obsm` key is already present in the aligned file for all samples, the GUI is not started. Set `alignment_force_recomputing: true` to override this.
