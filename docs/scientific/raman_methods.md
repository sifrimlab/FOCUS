# Raman Preprocessing Methods

Implementation reference for `focus/preprocessing/raman.py`. `RamanDataset.process_dataset()` iterates
over samples and, for each one, calls `load_source()`, `basic_correct()`, `remove_background()`,
`process_raw_tiles()` and `ashlar_stitch()` in that order, skipping the whole sample when its final
OME-TIFF already exists and `force_recomputing` is false. Exceptions raised while processing a sample
are caught, printed as `Error processing sample <id>: <error>`, and the loop moves to the next sample.

---

## 1. Input and metadata extraction

`load_source()` scans `{dataset}/{sample_id}/{modality}/` and loads the first entry whose name ends in
`.lif` (case-insensitive); with no such file it raises `FileNotFoundError`.

`_parse_lif_metadata` walks the LIF XML (`readlif`'s `xml_root`) and returns one `RamanMetadata` per
image element. Elements are collected from the `Children` container of each top-level `Element`, with
fallbacks for LIF files whose header is a flat `LMSDataContainerHeader`; when nothing is found it
raises `ValueError("No elements found in the LIF file or unexpected XML structure.")`.

Per element:

| Field | XML source |
|---|---|
| `scan_height`, `pixel_size[0]` | `Data/Image/ImageDescription/Dimensions/DimensionDescription[@DimID=1]`: `NumberOfElements`, and `Length / NumberOfElements` |
| `scan_width`, `pixel_size[1]` | same, `@DimID=2` |
| `lambda_steps` | same, `@DimID=9`: `NumberOfElements` |
| `tile_number` | same, `@DimID=10`: `NumberOfElements` |
| `lambda_begin`, `lambda_end` | `LambdaDefinition/LambdaExcitation`: `LambdaExcitationBeginDouble` / `LambdaExcitationEndDouble`, falling back to the non-`Double` attribute names |
| `lambda_stokes`, `laser_type` | first `LaserArray/Laser` element carrying a `PumpWavelength`, plus its `LaserName` |
| `tiles_coordinates` | `Data/Image/Attachment[@Name="TileScanInfo"]/Tile`: `PosX`, `PosY` |

The wavelength and laser attributes are searched at
`Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition` and, if not found there,
at the same path under `LDM_Block_Sequential/LDM_Block_Sequential_Master`.

Units: each spatial `DimensionDescription` carries a `Unit`, `m` or `um`, giving a scaling factor of
\(10^6\) or \(1\) to micrometers; any other unit raises `ValueError`. The same factor converts the
tile `PosX`/`PosY` to micrometers, so tile coordinates and pixel sizes are both in µm. When the number
of `Tile` entries does not match `DimID=10`, a `RuntimeWarning` line is printed and the element is
dropped.

`_load_lif` then keeps only elements with `tile_number >= 2`. Single-field acquisitions and any
automatically stitched image are ignored. An element still missing `tile_number`,
`lambda_steps`, `scan_width` or `scan_height` is reported as a probably corrupted scan and skipped;
a missing `tiles_coordinates` or a non-positive `pixel_size` raises `ValueError`.

Pixel data is read plane by plane through `readlif`
(`image.get_plane(display_dims=(1, 2), c=0, requested_dims={9: spectral_index, 10: tile_index})`) into
a `float32` array of shape `(tile_number, lambda_steps, scan_width, scan_height)`. `float32` is
required by the BaSiC step downstream.

---

## 2. Wavenumber axis

For each element, with \(\lambda_i\) the `lambda_steps` values of
`linspace(lambda_begin, lambda_end, lambda_steps)` and \(\lambda_{stokes}\) the laser `PumpWavelength`
(`_compute_wavenumbers`):

\[
\tilde\nu_i = \left(\frac{1}{\lambda_i} - \frac{1}{\lambda_{stokes}}\right)\times 10^7
\]

The factor \(10^7\) converts \(\text{nm}^{-1}\) to \(\text{cm}^{-1}\). The concatenated result is
stored as `float32` on `RamanImage.wavenumbers`.

The axis is consumed only by §5, as the spectral axis handed to RamanSPy. It is never written to disk:
neither the stitched OME-TIFF nor any sidecar file records it.

---

## 3. Merging scans, overlap trimming, intensity scaling

Elements that pass the filters above are merged into single arrays:

- tile data concatenated along the channel axis, in element order (requires identical tile count and
  tile size across elements);
- wavenumber vectors concatenated in the same order;
- tile coordinates stacked to `(tiles, scans, 2)`;
- pixel sizes averaged over elements into one `(2,)` vector.

Each element's channel range is recorded in `_spectra_slices` as an inclusive `(start, end)` pair.
These slices are the unit of work in §5 and the unit of output in §6, and they allow one sample to
carry several acquisitions with different spectral ranges.

**Overlap trimming** (`_check_wavenumbers_overlaps`). Within one scan the wavelength axis is a
`linspace`, so the wavenumber axis is monotonic; a concatenation of scans that re-cover part of the
spectrum is not. The first index \(b\) where monotonicity breaks is located, the index
\(c=\arg\min_{i<b}\lvert\tilde\nu_i-\tilde\nu_b\rvert\) marks where the overlap starts in the preceding
block, and channels \([c, b)\) are dropped from the wavenumber vector and from the tile array. The
slice boundaries are re-indexed by the shift \(b-c\): slices before the affected one are unchanged, the
affected one is truncated at \(c-1\), the one containing \(b\) restarts at \(c\), and later slices are
shifted down. A monotonic axis is returned untouched, and only one overlapping region is removed.

**Intensity scaling.** The merged stack is rescaled once, from the global maximum: values in
\((1, 255]\) are divided by 255, values in \((255, 65535]\) by 65535, a maximum above 65535 raises
`ValueError`, and a maximum at or below 1 is left as is.

The `RamanImage.metadata` object that results is a summary of the merged stack, not a copy of any
single element's metadata. Its `tile_number` and `lambda_steps` are the merged array's first two
dimensions, its `scan_height` and `scan_width` are its last two, and its `pixel_size` is the
cross-scan mean. The per-element arrays are allocated as
`(tiles, channels, scan_width, scan_height)`, so `scan_height` and `scan_width` are swapped on this
summary object relative to the per-element metadata. Their only consumer, the
`tile_size=(scan_height, scan_width)` argument of `_extract_tiles_segmentation_from_mosaic` in §5,
reads them in the merged array's own axis order.

---

## 4. BaSiC correction

`basic_correct()` runs BaSiCpy channel-wise in the external conda environment `FOCUS_BaSiCpy`. Before
starting, it verifies that `conda` is on `PATH`, that a conda environment path containing
`FOCUS_BaSiCpy` is listed by `conda env list --json`, and that `tools/BaSiCpy/main.py` exists; each
failed check raises.

Per channel \(c\):

1. Write `basic_input_{c}.npy`, the `(tiles, rows, columns)` slice for that channel.
2. `conda run -n FOCUS_BaSiCpy python tools/BaSiCpy/main.py {output_path} {c}`, with
   `JAX_PLATFORM_NAME=cpu` in the subprocess environment. `main.py` calls `BaSiC().fit(...)` followed
   by `.transform(...)` on that stack and writes `basic_output_{c}.npy`.
3. Poll for the output file for up to 10 s (0.2 s interval), then load it and delete both temporaries.
   A missing file at the end of the timeout raises `TimeoutError`.

Channels are dispatched concurrently with `ThreadPoolExecutor(max_workers)`. When every channel has
returned, the assembled `float32` tensor is min-max normalized **globally** (one minimum and one
maximum over all tiles and channels, applied only when the maximum exceeds the minimum) and cached to
`basic_corrected_tiles.npy`.

---

## 5. Background removal

`remove_background()` segments tissue once on a quick-stitched preview mosaic (`_quick_stitch`) and
back-projects the mask onto the tiles. It requires the BaSiC-corrected tiles to be in memory and
raises `RuntimeError` otherwise, even when its own cache exists.

1. **Distance-transform feathered blend.** Tiles are placed at their stage coordinates divided by
   `pixel_size[0]`. Each tile is weighted by the Euclidean distance transform of its footprint,
   normalized to its maximum, \(w(u,v)=\mathrm{EDT}(u,v)/\max \mathrm{EDT}\), so a pixel near the
   tile centre carries more weight than one near its border. The mosaic is the weighted sum divided by
   the accumulated weights, \(M = \bigl(\sum_t w_t\,T_t\bigr) \big/ \bigl(\sum_t w_t\bigr)\), per
   channel, with zeros wherever no tile contributed.
2. **PCA to one component.** Pixels that are zero in every channel are excluded; an all-zero mosaic
   raises `RuntimeError("The mosaic is completely black.")`. The remaining \(C\)-channel vectors
   are projected onto their first principal component (`sklearn.decomposition.PCA(n_components=1)`).
   The scores are clipped to their 2nd and 98th percentiles and rescaled to \([0,1]\); excluded pixels
   are written back as 0.
3. **CLAHE.** The grayscale image is scaled to `uint8` and equalized with
   `cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))`. The result is the preview mosaic used below.
4. **Otsu segmentation.** The preview mosaic is clipped at its 95th percentile and Otsu's threshold is
   computed on that copy (between-class-variance criterion, as in
   [microscopy](microscopy_methods.md#3-background-removal-remove_background)). The threshold is
   multiplied by `otsu_threshold_factor` (default 0.7), truncated to an integer, and applied to the
   **unclipped** preview mosaic.
5. **Morphological cleanup.**
   `skimage.morphology.remove_small_objects(mask, max_size=min_object_size)` removes connected
   components (4-connectivity) whose area is at most `min_object_size` pixels (the `max_size`
   argument is inclusive), followed by `scipy.ndimage.binary_fill_holes`.
6. **Contour area filter.** External contours (`cv2.RETR_EXTERNAL`) are kept when their area is at
   least `bg_min_area_fraction` × (mosaic height × mosaic width) and filled to form the tissue mask.
   When `cv2.findContours` returns nothing, a warning is printed and the mask from step 5 is kept
   unrefined.
7. **Back-projection.** `_extract_tiles_segmentation_from_mosaic` cuts the mosaic mask at each tile's
   position into a boolean array shaped like the tile stack, using that tile's coordinates for the
   scan the channel belongs to. That array multiplies the BaSiC-corrected tiles.
   Background pixels become exactly 0 in every channel. The masked tiles are cached to
   `segmented_tiles.npy` and replace the BaSiC-corrected tiles in memory, so §6 operates on them.

---

## 6. Spectral cleaning (RamanSPy)

`process_raw_tiles()` builds one work unit per `(tile, spectra slice)` pair, so each scan's channel
block is cleaned independently with its own wavenumber sub-range. Units are dispatched with
`joblib.Parallel(n_jobs=max_workers, return_as="generator")`; `parallel=False` runs the identical
per-unit function in a plain loop.

Inside `_process_tile_parallel` the block is reshaped to one spectrum per pixel and run through a fixed
`rp.preprocessing.Pipeline`, in order:

1. **Whitaker-Hayes despiking** (`WhitakerHayes()`, RamanSPy defaults `kernel_size=3`,
   `threshold=8`): points whose modified \(z\)-score
   \(\lvert 0.6745\,(d_i-\mathrm{med}(d))/\mathrm{MAD}(d)\rvert\) on the consecutive-difference series
   \(d=\mathrm{diff}(y)\) exceeds the threshold are flagged as cosmic-ray spikes; each flagged point is
   replaced by the mean of the non-flagged points within \(\pm\)`kernel_size`, iterating until no
   further point can be fixed.
2. **Savitzky-Golay denoising** (`SavGol(window_length=savgol_window, polyorder=savgol_polyorder)`):
   least-squares fit of a degree-`savgol_polyorder` polynomial (default 3) within a sliding window of
   `savgol_window` channels (default 7), evaluated at the window centre; smooths noise while
   preserving peak shape. SciPy raises `ValueError` when the polynomial order is not smaller than the
   window, or when the window is longer than the block being filtered.
3. **IASLS baseline correction** (`IASLS()`, RamanSPy defaults `lam=1e6`, `p=1e-2`, `lam_1=1e-4`,
   `max_iter=50`, `tol=1e-3`, `diff_order=2`): Improved Asymmetric Least Squares estimates a smooth
   baseline \(z\) minimizing an asymmetrically weighted penalized least-squares objective
   \(\sum_i w_i (y_i-z_i)^2 + \lambda \lVert D^2 z\rVert^2\) with an additional first-derivative
   term, where the weights \(w_i\) penalize points above the baseline far less than those below, and
   subtracts \(z\).
4. **Min-max normalization** (`MinMax()`, RamanSPy default `pixelwise=True`): each spectrum is
   rescaled to \([0,1]\) individually.

Only the Savitzky-Golay window and polynomial order are exposed as parameters.

**Zero-variance spectra.** `_zero_variance_spectra` computes the median absolute deviation of each
spectrum's consecutive-difference series and flags the spectra whose MAD is exactly 0. These are flat
spectra, including the background pixels zeroed in §5. That MAD is the denominator of the Whitaker-Hayes
modified \(z\)-score, so those spectra are dropped from the unit before the pipeline runs and their
pixels are written back as zeros. A unit in which every spectrum is flat returns an all-zero block
without invoking the pipeline.

The cleaned stack is cached to `raman_corrected_tiles.npy`.

---

## 7. ASHLAR stitching

`_prepare_for_ashlar` prepares the inputs:

- mirror the tile \(y\) coordinates within their own range per scan,
  \(y' = \max y + \min y - y\) (Leica → OME-TIFF convention);
- `np.nan_to_num`, then scale by 255 and cast to `uint8`;
- choose the alignment channel from the first spectra slice: take each channel's per-tile mean over
  the tile's pixels, reduce over tiles with a maximum, and select the `argmax` over channels;
- write one `ashlar_input_cycle_{n}.ome.tiff` per spectra slice, one OME image per tile, zlib
  compressed, carrying `PhysicalSizeX`/`PhysicalSizeY` in µm, per-plane `PositionX`/`PositionY` in µm,
  and channel names `Channel_0 … Channel_{C-1}`.

`ashlar_stitch()` then runs `conda run -n FOCUS_ASHLAR python -u tools/ASHLAR/main.py {output_path}
{align_channel}` with `check=True`, so a non-zero exit propagates as `CalledProcessError`. The script:

1. sorts the `ashlar_input*` files by name;
2. builds a Bio-Formats reader per file (hence the Java dependency) and calls
   `process_axis_flip(reader, False, False)`, which leaves the tile positions as written;
3. runs `reg.EdgeAligner` on the first cycle with `channel=align_channel` and `max_shift=15` µm
   (`do_make_thumbnail=False` when there is only one cycle), then a `reg.LayerAligner` per further
   cycle against that result, with the same `channel` and `max_shift`;
4. writes every cycle's mosaic through `reg.PyramidWriter`, with `peak_size` computed so the smallest
   pyramid level holds at most \(3000\times3000 = 9\times10^6\) pixels;
5. deletes the cycle input files.

ASHLAR reads the physical pixel size from the cycle files and refuses inputs whose `PhysicalSizeX` and
`PhysicalSizeY` differ by more than a relative tolerance of `1e-4` (`Can't handle non-square pixels`).

FOCUS renames `ashlar_output.ome.tiff` to the final preprocessing output path and loads it into
`RamanImage.mosaic`.

---

## 8. Caching and intermediates

Stage outputs are cached in the sample's preprocessing directory:

| File | Stage |
|---|---|
| `basic_corrected_tiles.npy` | §4 |
| `segmented_tiles.npy` | §5 |
| `raman_corrected_tiles.npy` | §6 |

Each stage loads its cache instead of recomputing whenever the file exists and `force_recomputing` is
false; the caches record no parameter values, so a changed parameter is only honoured with
`force_recomputing: true` or after the files are removed. §1 is never cached: the LIF file is re-read
on every run that does not already have a final OME-TIFF, because the tile coordinates, pixel size and
slice boundaries feed the later stages.

`process_dataset()` deletes all three files after the sample's final OME-TIFF has been produced, so
they persist only after an interrupted or failed run. In that case they let a rerun resume
mid-sample.

---

## 9. Parameters reflected by implementation

Defaults come from the `RamanImage` class constants and are applied both by the config settings
extractor (`_extract_raman_settings`) and by the `RamanDataset.process_dataset()` signature. They are
written below as the configuration values you would supply.

| Parameter | Default | Stage |
|---|---|---|
| `force_recomputing` | `false` | all |
| `max_workers` | 8 | §4 (threads), §6 (`joblib` workers) |
| `savgol_window` | 7 | §6 |
| `savgol_polyorder` | 3 | §6 |
| `bg_min_area_fraction` | 0.05 | §5 |
| `otsu_threshold_factor` | 0.7 | §5 |
| `min_object_size` | 500 | §5 |

`max_workers` is also read by `_create_raman_samples`, which passes it to each `RamanImage`
constructor; `process_dataset()` re-assigns it on every sample before processing.

---

## 10. Outputs

Per sample:

```text
{dataset_path}/{sample_id}/preprocessing/{modality}/{modality}_{sample_id}_processed.ome.tiff
```

`uint8`, one channel per surviving spectral channel in scan order, Adobe-Deflate compressed with a
horizontal predictor in 1024 × 1024 tiles, with 2× pyramid levels. The OME-XML holds the ASHLAR creator
string and `PhysicalSizeX`/`PhysicalSizeY` in µm, and no channel names. As a result,
`raman_pixel_interpolation` names the registered features `Channel_0 … Channel_{C-1}`, and the
wavenumber axis is not recoverable from the file. No merged file is produced for Raman.

The stitched OME-TIFF is the input to the [alignment stage](alignment_methods.md) and to
[`raman_pixel_interpolation` registration](registration_methods.md#5-raman-pixel-interpolation-raman_pixel_interpolation),
both of which work in its pixel coordinates.
