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

For images above `_DETECTION_MAX_PIXELS` (~9 megapixels), \(p_\text{lo}\)/\(p_\text{hi}\) are
estimated from a strided subsample of the non-zero pixels rather than the full population —
percentiles are stable under subsampling, so this is a negligible-error approximation. The clip/
rescale formula above is still applied to every pixel of the full-resolution channel.

---

## 3. Background removal (`remove_background`)

Detection (`_detect_tissue_mask`, orchestrated by `_compute_tissue_mask`) and application
(`_remove_background`) are separate steps:

**Detection**, run on a downsampled proxy rather than the full-resolution image (see "Detection
resolution" below):

1. Convert to uint8 and replace pure-black pixels by white (artifact guard).
2. Convert to grayscale and invert.
3. Clip intensities at `clip_percentile` (default 99).
4. Apply Gaussian blur (`_DETECTION_BLUR_KERNEL_SIZE`, fixed at 25 px — not user-configurable, see
   "Detection resolution" below).
5. Compute the **Otsu threshold** on the blurred image; apply it to the original inverted grayscale.
6. Remove small connected components (`_DETECTION_MIN_OBJECT_SIZE`, fixed at 50 px — not
   user-configurable) and fill holes.
7. Contour area filtering using `min_object_coverage` fraction of (proxy) image area.

**Application**: the resulting boolean mask is upsampled (nearest-neighbor) back to full resolution
and used to fill background with the selected `background_color` (`white` or `black`).

**Detection resolution.** Segmentation (steps 1-7) runs on a proxy capped at
`_DETECTION_MAX_PIXELS` (\(3000\times3000 = 9\times10^6\) px, independent of the OME-TIFF pyramid
cap in §5) rather than the full image: a tissue/background boundary is a smooth, low-frequency shape,
so locating it doesn't require full-resolution input, and this keeps Gaussian blur, Otsu thresholding,
and connected-component/hole-filling — the most expensive steps for gigapixel whole-slide images —
fast. If the image is already at or below the cap, no downsampling occurs and detection runs at the
image's own (already ≤ cap) resolution instead.

Because detection always operates on a canvas of at most `_DETECTION_MAX_PIXELS`, the blur kernel and
minimum object size are fixed constants tuned for that canvas (`_DETECTION_BLUR_KERNEL_SIZE=25`,
`_DETECTION_MIN_OBJECT_SIZE=50`) rather than parameters expressed in the source image's native
resolution — there is no longer a `gaussian_blur_kernel_size`/`min_object_size` process parameter.
These are principled best-guess defaults, not empirically validated against real scan data.

**Otsu threshold.** Otsu's method selects the gray level \(t^\*\) maximizing the between-class
variance of the (blurred) intensity histogram,
\[
t^\* = \arg\max_t\ \omega_0(t)\,\omega_1(t)\,\bigl(\mu_0(t)-\mu_1(t)\bigr)^2,
\]
where \(\omega_0,\omega_1\) are the mass fractions of pixels below/above \(t\) and \(\mu_0,\mu_1\)
their mean intensities. (The same criterion is reused for Raman segmentation.)

**Connected-component cleanup.** Performed with
`skimage.morphology.remove_small_objects(mask, min_size=_DETECTION_MIN_OBJECT_SIZE)`, removing
foreground components smaller than 50 (proxy-resolution) pixels, followed by hole filling.

**Contour area filtering.** External contours are kept only when their area is at least
`min_object_coverage` × (image area); the retained contours are filled to form the tissue mask.

---

## 4. Tissue cropping (`crop_to_tissue`)

Uses the same tissue mask from §3 (`_compute_tissue_mask`/`_detect_tissue_mask`) — always the
Otsu-based detection, even when `remove_background=False`. The bounding box is read directly off the
mask (at its detection resolution, then scaled back to full-resolution coordinates), expanded by
`crop_margin` (default 250 px) and clamped to image bounds.

Because the mask may come from a downsampled proxy, the bounding box carries an additional boundary
tolerance of at most one proxy-resolution pixel (i.e. up to `1/scale` full-resolution pixels, where
`scale` is the detection downsample factor from §3) on top of `crop_margin` — negligible in practice
since `crop_margin` (250 px) is already far larger than this for any realistic image size.

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
- `clip_percentile` (default 99)
- `crop_margin` (default 250)
- `gamma` (default 0.45)
- `contrast_saturation` (default 0.35)
- `force_recomputing` (default false)

`gaussian_blur_kernel_size`/`min_object_size` are no longer process parameters — they're fixed
internal constants (`_DETECTION_BLUR_KERNEL_SIZE=25`, `_DETECTION_MIN_OBJECT_SIZE=50`, see §3).

---

## 7. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.ome.tiff
```

This file is the image input for downstream alignment and (if configured) feature-extraction registration.
