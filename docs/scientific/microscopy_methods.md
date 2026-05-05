# Microscopy Preprocessing Methods

## 1. Input normalization

Supported inputs: `.ome.tiff`, `.ome.tif`, `.tiff`, `.tif`, `.czi`.

Loading behavior:

- CZI: extra leading dimensions are squeezed iteratively; if multiple scenes exist, first scene is used.
- Channel axis is moved to last position using a smallest-dimension heuristic.
- Arrays are converted to float32 and scaled to `[0, 1]`.
- Channel count is clipped to at most 3.

---

## 2. Color enhancement (`color_enhancement`)

Two operations in sequence:

1. Gamma correction (`utils.gamma_correction`):
\[
I' = I^{\gamma}
\]
(default `gamma=0.45`, which brightens for `I in (0,1)`).

2. Contrast stretching (`utils.enhance_contrast`) on non-zero pixels with symmetric saturation parameter `contrast_saturation` (default `0.35`).

---

## 3. Background removal (`remove_background`)

Implemented in `_remove_background`:

1. Convert to uint8 and replace pure-black pixels by white (artifact guard).
2. Convert to grayscale and invert.
3. Clip intensities at `clip_percentile` (default 99).
4. Apply Gaussian blur (`gaussian_blur_kernel_size`, default 251; coerced to odd).
5. Compute Otsu threshold on blurred image; apply threshold to original inverted grayscale.
6. Remove small connected components and fill holes.
7. Contour area filtering using `min_object_coverage` fraction of image area.
8. Fill background with selected `background_color` (`white` or `black`).

Note on implementation detail: current code calls `skimage.morphology.remove_small_objects(..., max_size=min_object_size)`; this should be interpreted as connected-component cleanup with threshold parameter `min_object_size` in current dependency versions.

---

## 4. Tissue cropping (`crop_to_tissue`)

Foreground is defined as pixels different from fill color (tolerance `1e-3`).

Bounding box is expanded by `crop_margin` (default 250 px) and clamped to image bounds.

---

## 5. OME-TIFF pyramid output

`_save_image_pyramid` writes a BigTIFF with `pyramid_levels` resized levels (default 4), scale factor `0.5^l` at level `l`, using `cv2.INTER_AREA`.

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
- `pyramid_levels` (default 4)
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
