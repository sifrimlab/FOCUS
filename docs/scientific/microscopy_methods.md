# Microscopy Image Preprocessing Methods

Bright-field and fluorescence microscopy images provide the morphological context essential for interpreting spatial omics data. FOCUS implements a uniform preprocessing pipeline that normalises pixel values, optionally enhances image contrast, removes background, crops to tissue, and exports a multi-resolution OME-TIFF pyramid for downstream co-registration and visualisation.

---

## 1. Data Formats

FOCUS supports two input formats, resolved in priority order:

**TIFF / OME-TIFF** (`.ome.tiff`, `.ome.tif`, `.tiff`, `.tif`): standard raster format at arbitrary bit depth (8-bit, 16-bit, 32-bit) and channel count. OME-TIFF files carry embedded OME-XML metadata; FOCUS reads only the pixel array and normalises it independently of the metadata. Multi-channel images with more than three channels are clipped to the first three.

**CZI** (Carl Zeiss Image, `.czi`): proprietary format from the Zeiss Zen acquisition software. CZI files may encode multiple scenes, timepoints, and z-planes. FOCUS uses `czifile` to load the raw array and selects the first scene and first timepoint by iteratively squeezing leading singleton dimensions until a 3D $(C \times H \times W)$ or 2D $(H \times W)$ array is obtained. A warning is emitted when the CZI contains multiple scenes.

After loading, the pixel array is transposed to channels-last layout $(H \times W \times C)$ by moving the minimum-size axis to position 2.

---

## 2. Normalisation

All images are converted to float32 in $[0, 1]$ after loading. The normalisation rule depends on the input dtype:

- **Integer types** (uint8, uint16, uint32): divide by the dtype maximum $I_\text{max}$ (e.g. 255 for uint8, 65535 for uint16):

$$I' = \frac{I}{I_\text{max}}$$

- **Float types already in $[0, 1]$**: retained as-is.
- **Float types exceeding 1.0**: min-max normalised using the image maximum as the divisor.

This ensures that all downstream operations receive a consistently scaled input regardless of acquisition depth.

---

## 3. Color Enhancement (Optional)

Two sequential contrast transforms are applied when `color_enhancement=True`. Both are applied per-pixel independently of spatial context.

### 3.1 Gamma Correction

Gamma correction applies a power-law transform to the normalised pixel values:

$$I' = I^{1/\gamma}$$

with default $\gamma = 0.45$. Since $1/\gamma > 1$ for $\gamma < 1$, values in $(0, 1)$ are mapped to larger values, brightening underexposed images. Conversely, $\gamma > 1$ darkens bright images. The transform is applied in-place using `numpy.power`.

### 3.2 Contrast Stretching

Contrast stretching (histogram clipping and rescaling) is applied after gamma correction. The implementation operates only on non-zero pixels (background zeroes are excluded from percentile estimation to prevent them from anchoring the histogram):

1. Compute the saturation percentiles on the non-zero subset:

$$p_\text{low} = s / 2, \quad p_\text{high} = 100 - s / 2$$

where $s$ is `contrast_saturation` (default 0.35, interpreted as a percentage). Thus the bottom 0.175% and top 0.175% of non-zero pixels are clipped by default.

2. Clip all pixel values to $[q_{p_\text{low}},\, q_{p_\text{high}}]$, then rescale to $[0, 1]$:

$$I'' = \frac{\min(I', q_{p_\text{high}}) - q_{p_\text{low}}}{q_{p_\text{high}} - q_{p_\text{low}}}$$

clipped to $[0, 1]$.

If $q_{p_\text{high}} \leq q_{p_\text{low}}$ (degenerate histogram), the operation is skipped.

---

## 4. Background Removal (Optional)

The background removal step (`remove_background=True`) produces a binary foreground mask and fills non-tissue pixels with a uniform colour. The mask is estimated on a blurred version of the image to suppress tissue texture.

**Pipeline** (`MicroscopyImage._remove_background`):

1. **Convert to uint8**: the float32 image is scaled to $[0, 255]$. Purely black pixels (all channels zero) are replaced with white $(255, 255, 255)$ to prevent them from being misclassified as foreground after inversion.

2. **Grayscale conversion and inversion**: the uint8 image is converted to grayscale using the standard luminance formula (OpenCV `COLOR_RGB2GRAY`), then bitwise-inverted so that a white background becomes black ($I_\text{inv} = 255 - I_\text{gray}$).

3. **Intensity clipping**: the inverted grayscale is clipped at the `clip_percentile`-th percentile (default 99th) to reduce the influence of bright outliers on the subsequent blur:

$$I_\text{clipped}(x, y) = \min\!\left(I_\text{inv}(x, y),\; P_\text{clip}(I_\text{inv})\right)$$

4. **Gaussian blur**: a Gaussian kernel of size $k \times k$ (default $251 \times 251$, $\sigma = 0$ for automatic scale) is applied to $I_\text{clipped}$ to suppress fine-scale tissue texture and preserve only the large-scale tissue outline:

$$G = I_\text{clipped} * g_k$$

5. **Otsu threshold on blurred image**: Otsu's method is applied to $G$ to compute the binary threshold $\tau_\text{Otsu}$, maximising between-class variance. The threshold is then applied to the original inverted grayscale $I_\text{inv}$ (not the blurred image), preserving the sharpness of the tissue boundary:

$$\text{mask}(x, y) = [I_\text{inv}(x, y) \geq \tau_\text{Otsu}]$$

6. **Morphological cleanup**:
    - `skimage.morphology.remove_small_objects`: removes connected components with fewer than `min_object_size` pixels (default 500).
    - `scipy.ndimage.binary_fill_holes`: fills enclosed holes within the tissue mask.

7. **Contour-based area filtering**: contours are extracted from the binary mask using `cv2.findContours`. Contours with area $< \text{min\_object\_coverage} \times H \times W$ (default 1% of the image area) are discarded, retaining only large coherent tissue regions.

8. **Background replacement**: pixels outside the final tissue mask are replaced with the `background_color` fill value: white $[1.0, 1.0, 1.0]$ or black $[0.0, 0.0, 0.0]$ (float32).

---

## 5. Tissue Cropping (Optional)

When `crop_to_tissue=True`, the image is cropped to the axis-aligned bounding box of the foreground region with an additional margin:

1. Identify the foreground region by finding non-background pixels (pixels not equal to the fill colour within tolerance $10^{-3}$).
2. Compute the bounding box: $[y_\text{min}, y_\text{max}] \times [x_\text{min}, x_\text{max}]$.
3. Expand by `crop_margin` pixels (default 250) in all directions:

$$y_\text{min}' = \max(0,\, y_\text{min} - m), \quad y_\text{max}' = \min(H-1,\, y_\text{max} + m)$$
$$x_\text{min}' = \max(0,\, x_\text{min} - m), \quad x_\text{max}' = \min(W-1,\, x_\text{max} + m)$$

The crop margin ensures that downstream co-registration algorithms have sufficient context at the tissue boundary.

---

## 6. OME-TIFF Pyramid Construction

The processed image is saved as a **multi-resolution OME-TIFF** pyramid for efficient large-image viewing and registration.

**Pyramid generation**: `pyramid_levels` resolution levels are generated (default 4: full, $\frac{1}{2}$, $\frac{1}{4}$, $\frac{1}{8}$ resolution). Each lower level is downsampled from the original full-resolution image using OpenCV `INTER_AREA` interpolation (anti-aliased area averaging):

$$I^{(l)}(x, y) = \text{INTER\_AREA}\!\left(I^{(0)},\; \text{scale} = 2^{-l}\right), \quad l = 0, 1, \ldots, L-1$$

**File format**: the pyramid is written as a BigTIFF (supporting files $> 4$ GB) using `tifffile.TiffWriter`. Each resolution level is a separate top-level IFD. Compression: zlib. Photometric encoding:

- 3-channel images: `'rgb'` with interleaved channels (YXC layout, `samples_per_pixel=3`).
- Grayscale or multi-channel images: `'minisblack'` with separate planes per channel.

**OME-XML metadata**: a valid OME-XML block is embedded in the first IFD's `ImageDescription` tag, encoding `SizeX`, `SizeY`, `SizeC`, `Type`, `DimensionOrder`, and per-channel `Channel` elements. This ensures compatibility with OME-compliant readers.

The output path follows the FOCUS directory convention: `{source_path}/{sample_id}/preprocessing/{modality_name}/{modality_name}_{sample_id}_processed.ome.tiff`.

---

## 7. Parameter Selection Guidance

| Parameter | Default | Scientific Effect | Recommended Values |
|---|---|---|---|
| `gamma` | 0.45 | Power-law brightening of underexposed images. Values $<1$ brighten; $>1$ darken. | 0.3–0.6 for H&E; 1.0 to disable |
| `contrast_saturation` | 0.35 | Percentage of pixels saturated at each end of the histogram. Higher values increase contrast but may clip tissue features. | 0.1–1.0%; increase for low-contrast images |
| `gaussian_blur_kernel_size` | 251 | Spatial scale of the background estimation blur. Larger kernels suppress finer tissue structure. Must be odd. | 101–501; scale with tissue size |
| `clip_percentile` | 99 | Percentile for intensity clipping before Gaussian blur. Reduces the influence of saturated pixels on the threshold. | 95–99 |
| `min_object_size` | 500 | Minimum pixel count for foreground object retention after morphological cleanup. | 200–5000; scale with image resolution |
| `min_object_coverage` | 0.01 | Minimum area fraction for a contour to be retained as tissue. Filters debris and mounting artefacts. | 0.005–0.05 |
| `crop_margin` | 250 | Pixel margin around the tissue bounding box after cropping. | 100–500; larger for registration tasks |
| `pyramid_levels` | 4 | Number of resolution levels in the OME-TIFF pyramid. | 3–6; increase for very large images |
