# Microscopy Preprocessing Methods

## 1. Input normalization

Supported inputs: `.ome.tiff`, `.ome.tif`, `.qptiff`, `.tiff`, `.tif`, `.czi`.

Loading behavior:

- qpTIFF: all series/pyramid levels in the file are compared by pixel count; only the single highest-resolution one is loaded (auxiliary thumbnail/macro/label images and lower pyramid levels are discarded).
- CZI: extra leading dimensions are squeezed iteratively; if multiple scenes exist, first scene is used.
- Channel axis is moved to last position using a smallest-dimension heuristic.
- Arrays are converted to float32 and scaled to `[0, 1]`.
- Channel count is clipped to at most 3.

---

## 2. Color enhancement (`color_enhancement`)

Two operations in sequence, applied per channel on the float image in \([0,1]\):

1. **Gamma correction** (`utils.gamma_correction`):
\[
I' = I^{\gamma}
\]
(default `gamma=0.45`; since \(\gamma<1\), this brightens midtones for \(I\in(0,1)\)).

2. **Contrast stretching** (`utils.enhance_contrast`) on the non-zero pixels of the channel.
The parameter `contrast_saturation` is a **percentage** (default `0.35`, i.e. 0.35 %). Let
\(p_\text{lo}\) and \(p_\text{hi}\) be the `contrast_saturation` and \(100-\)`contrast_saturation`
percentiles of the non-zero intensities. The channel is clipped to \([p_\text{lo},p_\text{hi}]\) and
linearly rescaled:
\[
I'' = \frac{\operatorname{clip}(I',\,p_\text{lo},\,p_\text{hi}) - p_\text{lo}}{p_\text{hi}-p_\text{lo}}
\]
(applied only when \(p_\text{hi}>p_\text{lo}\)). This saturates the darkest/brightest ~0.35 % of
non-zero pixels and stretches the remainder across the full range.

---

## 3. Background removal (`remove_background`)

Implemented in `_remove_background`:

1. Convert to uint8 and replace pure-black pixels by white (artifact guard).
2. Convert to grayscale and invert.
3. Clip intensities at `clip_percentile` (default 99).
4. Apply Gaussian blur (`gaussian_blur_kernel_size`, default 251; coerced to odd).
5. Compute the **Otsu threshold** on the blurred image; apply it to the original inverted grayscale.
6. Remove small connected components and fill holes.
7. Contour area filtering using `min_object_coverage` fraction of image area.
8. Fill background with the selected `background_color` (`white` or `black`).

**Otsu threshold.** Otsu's method selects the gray level \(t^\*\) maximizing the between-class
variance of the (blurred) intensity histogram,
\[
t^\* = \arg\max_t\ \omega_0(t)\,\omega_1(t)\,\bigl(\mu_0(t)-\mu_1(t)\bigr)^2,
\]
where \(\omega_0,\omega_1\) are the mass fractions of pixels below/above \(t\) and \(\mu_0,\mu_1\)
their mean intensities. (The same criterion is reused for Raman segmentation.)

**Connected-component cleanup.** Performed with
`skimage.morphology.remove_small_objects(mask, min_size=min_object_size)`, removing foreground
components smaller than `min_object_size` pixels, followed by hole filling.

**Contour area filtering.** External contours are kept only when their area is at least
`min_object_coverage` × (image area); the retained contours are filled to form the tissue mask.

---

## 4. Tissue cropping (`crop_to_tissue`)

Foreground is defined as pixels different from fill color (tolerance `1e-3`).

Bounding box is expanded by `crop_margin` (default 250 px) and clamped to image bounds.

---

## 5. OME-TIFF pyramid output

`_save_image_pyramid` writes a BigTIFF with a scale factor of \(0.5^{\ell}\) at level \(\ell\), using
`cv2.INTER_AREA`. The number of levels is not a parameter — `_compute_pyramid_levels` derives it from
the image dimensions so the smallest level stays within a \(3000\times3000 = 9\times10^6\) pixel cap.
For a base image of \(H\times W\) pixels with cap \(P_{\max}\),
\[
L = \begin{cases}
1, & HW \le P_{\max}\\[2pt]
\left\lceil \log_4\!\dfrac{HW}{P_{\max}} \right\rceil + 1, & HW > P_{\max}
\end{cases}
\]
(base 4 because each level halves both dimensions, quartering the pixel count).

Encoding:

- RGB (`C=3`): `photometric='rgb'`, axes `YXC`
- otherwise: per-channel `photometric='minisblack'`, axes `YX`

Compression is zlib.

---

## 6. Parameters reflected by implementation

- `color_enhancement` (default true)
- `remove_background` (default true)
- `crop_to_tissue` (default true)
- `background_color` (`white`/`black`, default `white`)
- `min_object_coverage` (default 0.01)
- `gaussian_blur_kernel_size` (default 251)
- `min_object_size` (default 500)
- `clip_percentile` (default 99)
- `crop_margin` (default 250)
- `gamma` (default 0.45)
- `contrast_saturation` (default 0.35)
- `force_recomputing` (default false)

---

## 7. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.ome.tiff
```

This file is the image input for downstream alignment and (if configured) feature-extraction registration.
