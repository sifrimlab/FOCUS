# Microscopy Preprocessing Methods

Implementation reference for `focus/preprocessing/microscopy_image.py`.
`MicroscopyImageDataset.process_dataset()` iterates over samples and calls
`MicroscopyImage.process_image()` on each, forwarding every parameter verbatim. Exceptions raised
while processing a sample are caught, printed as `Error processing sample <id>: <error>`, and the
loop moves to the next sample.

When the output OME-TIFF is present and `force_recomputing` is false, `process_image()` prints
`Processed image already exists. Using cached results.` and returns the existing output path.
There are no intermediate caches, so this is the only reuse point.

---

## 1. Input normalization

Supported inputs: `.ome.tiff`, `.ome.tif`, `.qptiff`, `.tiff`, `.tif`, `.czi`.

`_find_image_file` walks that extension list in order and returns the first directory entry whose
lower-cased name ends with the highest-priority extension present. The `MicroscopyImage`
**constructor** raises `FileNotFoundError` when nothing matches. This happens before any processing
and outside the per-sample `try`/`except` of `process_dataset()`, so a missing file aborts the
modality rather than skipping one sample.

Loading behavior:

- TIFF/OME-TIFF (`_load_tiff`): `tifffile.TiffFile(file).asarray()` (the first series at its base
  level). An existing pyramid in the source file is not reused; FOCUS rebuilds one in §5.
- qpTIFF (`_load_qptiff`): every level of every series is measured with `utils.hw_from_axes` and only
  the one with the most pixels is read, so pyramid levels, thumbnail, macro and label images are
  discarded regardless of series order. With more than one candidate an `INFO:` line reports the
  chosen resolution.
- CZI (`_load_czi`): `czifile.CziFile(file).asarray()`, then leading axes are dropped by taking index
  `0` until three axes remain; a `WARNING:` line is printed when the outermost axis has more than one
  entry.

`_normalize_image` then applies, in order:

1. a 2D array gains a trailing axis, `(H, W) → (H, W, 1)`;
2. when `shape[2] > min(shape[0], shape[1])`, the smallest axis is transposed to the end (the
   channels-first heuristic);
3. dtype conversion to `float32` in \([0,1]\): a `float32` array whose maximum is ≤ 1 is left
   untouched; an integer array is divided by `np.iinfo(dtype).max`; any other float array is divided
   by its own maximum. Division is skipped when that divisor is 0;
4. channels beyond the third are dropped.

The source dtype is captured before normalization and drives `_resolve_storage_dtype` in §5.

---

## 2. Color enhancement (`color_enhancement`)

Two operations in sequence on the float image in \([0,1]\):

1. **Gamma correction** (`utils.gamma_correction`), elementwise and in place:
\[
I' = I^{\gamma}
\]
(default `gamma=0.45`; since \(\gamma<1\), this brightens midtones for \(I\in(0,1)\)).

2. **Contrast stretching** (`utils.enhance_contrast`). The percentiles are computed over the pixels
\(>0\) of the **whole array, every channel pooled**, not per channel. The parameter
`contrast_saturation` is a **percentage** (default `0.35`, i.e. 0.35 %). With \(p_\text{lo}\) and
\(p_\text{hi}\) the `contrast_saturation` and \(100-\)`contrast_saturation` percentiles of those
values:
\[
I'' = \frac{\operatorname{clip}(I',\,p_\text{lo},\,p_\text{hi}) - p_\text{lo}}{p_\text{hi}-p_\text{lo}}
\]
applied only when \(p_\text{hi}>p_\text{lo}\), and with the same \(p_\text{lo}, p_\text{hi}\) for
every channel. This saturates the darkest and brightest ~0.35 % of non-zero pixels each and stretches
the remainder across the full range.

When more than `_DETECTION_MAX_PIXELS` (\(9\times10^6\)) pixels are non-zero, \(p_\text{lo}\) and
\(p_\text{hi}\) are estimated from a strided subsample of the non-zero values rather than the full
population. The clip/rescale above is still applied to every pixel.

---

## 3. Background removal (`remove_background`)

Detection (`_detect_tissue_mask`, orchestrated by `_compute_tissue_mask`) and application
(`_remove_background`) are separate steps. Detection runs whenever `remove_background` **or**
`crop_to_tissue` is enabled, and the resulting mask is shared by §3 and §4.

**Detection**, on a downsampled proxy (see "Detection resolution" below):

1. Convert to uint8 and, when the channel count is not 3, promote to 3 channels
   (`_as_detection_rgb`) so `cv2.COLOR_RGB2GRAY` accepts the array.
2. Resolve the polarity (see "Background polarity" below) and reduce to a grayscale whose bright
   class is the tissue: the bright-background branch replaces pixels that are 0 in every channel by
   white (artifact guard), converts with `cv2.COLOR_RGB2GRAY` and inverts; the dark-background branch
   converts and stops there.
3. Clip the grayscale at `clip_percentile` (default 99).
4. Apply Gaussian blur (`_DETECTION_BLUR_KERNEL_SIZE`, fixed at 25 px, `sigma=0`).
5. Compute the **Otsu threshold** on the blurred image; apply it to the original grayscale,
   unblurred and unclipped.
6. Remove small connected components (`_DETECTION_MAX_OBJECT_SIZE`, fixed at 50 px) and fill holes.
7. Contour area filtering using `min_object_coverage` fraction of the (proxy) image area.

Only steps 3-7 are shared by both polarities; everything from the percentile clip onwards assumes
tissue is the bright class.

**Channel promotion** (`_as_detection_rgb`). 1 channel is replicated three times, 2 channels gain a
zero third channel, 3 channels are returned unchanged (the same object, so the 3-channel path copies
nothing). This mirrors the alignment GUI's display conversion
([`_image_to_rgb_uint8`](alignment_methods.md#5-gui-image-representation-details)), so the mask comes
from the same composite the alignment overlay shows. The luma of a replicated single channel is that
channel exactly (the BGR weights sum to 1), and the zero third channel of a 2-channel image only
scales the luma uniformly, which leaves Otsu's split unchanged. Promotion happens before the artifact
guard: the replicated and padded channels are zero exactly where the source channels are, so
`np.all(... == 0, axis=-1)` selects the same pixels either way.

**Background polarity** (`_has_dark_background`). The bright-background assumption (invert, then keep
the darkest pixels) is correct for brightfield but selects the background on a fluorescence image
whose dark background carries any noise (the artifact guard only catches exactly-zero pixels). The
polarity is therefore probed on the promoted grayscale: with \(k=\max(1,\ \operatorname{round}(0.05
\cdot \min(H,W)))\), the median of the border frame (the outer \(k\) rows and columns) is compared
with the median of the whole grayscale, and the background counts as dark when the border median is
strictly lower. A scan filled edge to edge with tissue gives equal medians and takes the
bright-background branch. The probe runs **only when the image was promoted** (1 or 2 channels), so
3-channel input always follows the bright-background branch and its result is bit-identical to the
behaviour before promotion existed.

**Application**: the resulting boolean mask is upsampled (nearest-neighbor) back to full resolution
and used to fill background with the selected `background_color`. That value is a scalar `1.0`
(`white`) or `0.0` (`black`) that broadcasts over any channel count; anything else → `ValueError`.
The fill colour is a configuration value and is not derived from the detected polarity.

**Detection resolution.** Segmentation (steps 1-7) runs on a proxy scaled by
\(\min\bigl(1, \sqrt{P_{\max}/(HW)}\bigr)\) with `cv2.INTER_AREA`, where
\(P_{\max}=\) `_DETECTION_MAX_PIXELS` \(= 3000\times3000 = 9\times10^6\) px. This cap is independent
of the OME-TIFF pyramid cap in §5. An image already at or below the cap is passed through unscaled and
detection runs at its own resolution. The scale factor travels with the mask so §3 and §4 can map it
back to full-resolution coordinates.

Because detection always operates on a canvas of at most `_DETECTION_MAX_PIXELS`, the blur kernel and
the speck-removal size are constants expressed in proxy pixels (`_DETECTION_BLUR_KERNEL_SIZE=25`,
`_DETECTION_MAX_OBJECT_SIZE=50`) rather than parameters expressed in the source image's native
resolution. There is no `gaussian_blur_kernel_size`/`min_object_size` *microscopy* process parameter.
Raman segmentation does expose an identically named `min_object_size` (default 500) for its own
mosaic (see [Raman §5](raman_methods.md#5-background-removal)), but it is a separate parameter that
has no effect here.

**Otsu threshold.** Otsu's method selects the gray level \(t^\*\) maximizing the between-class
variance of the (blurred) intensity histogram,
\[
t^\* = \arg\max_t\ \omega_0(t)\,\omega_1(t)\,\bigl(\mu_0(t)-\mu_1(t)\bigr)^2,
\]
where \(\omega_0,\omega_1\) are the mass fractions of pixels below/above \(t\) and \(\mu_0,\mu_1\)
their mean intensities. The same criterion is reused for
[Raman segmentation](raman_methods.md#5-background-removal) and, over a per-spot score histogram
rather than pixel intensities, for
[MSI microgrid detection](msi_methods.md#sample_type-microgrid).

The percentile clip and the Gaussian blur enter only through this histogram: `cv2.threshold(...,
THRESH_OTSU)` is called on the clipped, blurred copy and its return value is \(t^\*\), which is then
applied to the untouched grayscale in a second `cv2.threshold` call. Neither operation reaches the
mask, so the tissue boundary follows the grayscale at full detail; the noise a blurred binarization
would have suppressed is removed instead by the connected-component and contour steps below.

**Connected-component cleanup.** Performed with
`skimage.morphology.remove_small_objects(mask, max_size=_DETECTION_MAX_OBJECT_SIZE)`, which labels the
boolean mask with 4-connectivity and removes every component of **at most** 50 (proxy-resolution)
pixels; `max_size` is inclusive. Hole filling (`scipy.ndimage.binary_fill_holes`) follows.

**Contour area filtering.** External contours (`cv2.RETR_EXTERNAL`) are kept only when their area is
at least `min_object_coverage` × (proxy image area); the retained contours are filled to form the
tissue mask. When `cv2.findContours` returns nothing, a warning is printed and the unrefined mask
from the previous step is kept.

---

## 4. Tissue cropping (`crop_to_tissue`)

Uses the same tissue mask as §3 (`_compute_tissue_mask`/`_detect_tissue_mask`). That mask always
comes from the Otsu-based detection, even when `remove_background=False`. The bounding box is read
directly off the mask (at its detection resolution, then divided by the detection scale to reach
full-resolution coordinates), expanded by `crop_margin` (default 250 px) and clamped to image bounds.
A mask with no foreground raises
`ValueError("The image appears to be entirely background; cannot crop.")`.

Because the mask may come from a downsampled proxy, the bounding box carries an additional boundary
tolerance of at most one proxy-resolution pixel (i.e. up to `1/scale` full-resolution pixels, where
`scale` is the detection downsample factor from §3) on top of `crop_margin`.

---

## 5. OME-TIFF pyramid output

`_save_image_pyramid` writes a BigTIFF with a scale factor of \(0.5^{\ell}\) at level \(\ell\), using
`cv2.INTER_AREA`. The number of levels is not a parameter: `_compute_pyramid_levels` derives it from
the final (post-crop) image dimensions so the smallest level stays within a
\(3000\times3000 = 9\times10^6\) pixel cap. For a base image of \(H\times W\) pixels with cap
\(P_{\max}\),
\[
L = \begin{cases}
1, & HW \le P_{\max}\\[2pt]
\left\lceil \log_4\!\dfrac{HW}{P_{\max}} \right\rceil + 1, & HW > P_{\max}
\end{cases}
\]
(base 4 because each level halves both dimensions, quartering the pixel count).

Storage dtype (`_resolve_storage_dtype`): floats → `float32`; `uint8`/`uint16` → unchanged; every
other integer depth → `uint16`. Quantization happens only at write time: `_quantize_to_dtype`
computes `clip(round(I * dtype_max), 0, dtype_max)`, or casts straight through for `float32`. The
pyramid is therefore generated and resampled in `float32`.

Encoding:

- RGB (`C=3`): `photometric='rgb'`, axes `YXC`, one OME channel `RGB` with `samples_per_pixel=3`;
- otherwise: one `photometric='minisblack'` page per channel, axes `YX`, OME channels `Channel_0 …`.

Compression is zlib with a predictor matched to the storage dtype (3 for `float32`, 2 for integers).
Levels are separate top-level IFDs rather than SubIFDs; the OME-XML built with `ome_types` lists one
`Image` per level (`ResolutionLevel_0 …`) and is written into the description of the first IFD only.

---

## 6. Parameters reflected by implementation

- `color_enhancement` (default `true`)
- `remove_background` (default `true`)
- `crop_to_tissue` (default `true`)
- `background_color` (`white`/`black`, default `white`)
- `min_object_coverage` (default 0.01)
- `clip_percentile` (default 99)
- `crop_margin` (default 250)
- `gamma` (default 0.45)
- `contrast_saturation` (default 0.35)
- `force_recomputing` (default `false`)

The same defaults are applied by the config settings extractor (`_extract_microscopy_settings`) and by
the `process_image()` / `process_dataset()` signatures. Only `background_color` is checked at all, and
only when background removal runs; the numeric fields are passed through unvalidated.

`gaussian_blur_kernel_size`/`min_object_size` are not *microscopy* process parameters. They are the
fixed internal constants `_DETECTION_BLUR_KERNEL_SIZE=25` and `_DETECTION_MAX_OBJECT_SIZE=50`
(see §3). Raman's exposed `min_object_size` is an unrelated parameter of a different stage.

---

## 7. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.ome.tiff
```

This file is the image input for downstream [alignment](alignment_methods.md) and (if configured)
[feature-extraction registration](registration_methods.md#2-feature-extraction-feature_extraction).
No merged file is produced for microscopy.
