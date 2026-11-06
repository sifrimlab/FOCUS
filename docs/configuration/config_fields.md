# Configuration Field Reference

This page documents all configuration fields that are referenced by the curated user, modality, and pipeline documentation.

---

## Top-Level Fields

### `dataset_path`

- **Type**: `string`
- **Required**: Yes
- **Description**: Absolute path to the dataset root containing sample directories.

Example:

```json
"dataset_path": "/data/my_tissue_cohort"
```

---

### `reference_modality`

- **Type**: `string`
- **Required**: Yes
- **Description**: Name of the modality that defines the shared output coordinate system.
- **Constraint**: Must exactly match one modality `name`.

Example:

```json
"reference_modality": "st"
```

---

### `perform_alignment`

- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Globally enable or disable alignment.

Example:

```json
"perform_alignment": true
```

---

### `perform_registration`

- **Type**: `boolean`
- **Required**: No
- **Default**: `true`
- **Description**: Globally enable or disable registration.

Example:

```json
"perform_registration": true
```

---

### `huggingface_token`

- **Type**: `string` or `null`
- **Required**: No
- **Default**: `null`
- **Description**: HuggingFace token used to download Prov-GigaPath for `feature_extraction`.
- **Required when**: Any modality uses `registration_type: "feature_extraction"`.

Example:

```json
"huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

---

### `spatial_annotations`

- **Type**: `object` or `null`
- **Required**: No
- **Default**: `null`
- **Description**: Enable spatial annotation transfer from per-sample GeoJSON files.

Structure:

```json
"spatial_annotations": {
  "modality_name": "microscopy",
  "file_type": "geojson"
}
```

Fields:

- `modality_name` (`string`): Name of a declared modality whose per-sample directory contains the `.geojson` files. Exactly one `.geojson` file must be present per sample.
- `file_type` (`string`): Currently only `"geojson"`.

!!! warning "Alignment required for non-reference annotation modalities"
    If `modality_name` is **not** the reference modality, `perform_alignment` must be `true`. Transferring annotations from a non-reference modality relies on the aligned coordinates (`obsm['{modality_name}_spatial']`) that the alignment stage produces; validation fails otherwise.

---

### `modalities`

- **Type**: `array`
- **Required**: Yes
- **Description**: List of modality definitions.

Each modality object includes:

- `name`
- `type`
- `alignment_strategy`
- `registration_type`
- `processing_settings`
- `registration_settings`

---

## Per-Modality Fields

### `name`

- **Type**: `string`
- **Required**: Yes
- **Description**: Unique modality identifier.
- **Constraint**: Must match the modality folder name in every sample.

Example:

```json
"name": "msi"
```

---

### `type`

- **Type**: `string`
- **Required**: Yes
- **Allowed values**:
  - `"microscopy_image"`
  - `"msi"`
  - `"raman"`
  - `"st"`

Example:

```json
"type": "microscopy_image"
```

---

### `alignment_strategy`

- **Type**: `string`
- **Required**: No
- **Default**: `"manual"`
- **Allowed values**:
  - `"manual"`
  - `"pre_aligned"`

Example:

```json
"alignment_strategy": "manual"
```

---

### `alignment_force_recomputing`

- **Type**: `boolean`
- **Required**: No
- **Default**: `false`
- **Description**: Re-run alignment for this modality even if cached alignment outputs are already present. Set to `true` to force re-alignment of this specific reference-target pair without affecting other modalities.

Example:

```json
"alignment_force_recomputing": false
```

---

### `registration_type`

- **Type**: `string`
- **Required**: No
- **Default**: `"none"`
- **Allowed values**:
  - `"none"` (any modality; skips registration)
  - `"feature_extraction"` (`microscopy_image` only, and only for H&E-stained brightfield images; rejected for fluorescence images; see [Registration Settings](#feature_extraction))
  - `"spot_interpolation"` (`msi`, `st`)
  - `"raman_pixel_interpolation"` (`raman`)
  - `"pixel_composition"` (`raman`, and `microscopy_image` recorded as fluorescence; rejected for brightfield images; see [Registration Settings](#pixel_composition))
- Compatibility is enforced: an incompatible `registration_type`/modality-type pairing raises a validation error.

Example:

```json
"registration_type": "spot_interpolation"
```

---

### `processing_settings`

- **Type**: `object`
- **Required**: Yes
- **Description**: Preprocessing parameters specific to the modality `type`.

---

### `registration_settings`

- **Type**: `object`
- **Required**: No
- **Default**: `{}`
- **Description**: Registration-method-specific settings.

---

## Processing Settings by Modality

## `microscopy_image`

| Field | Type | Default |
|---|---|---|
| `image_type` | string | `"auto"` |
| `z_projection` | string | `"auto"` |
| `crop_to_tissue` | bool | `false` |
| `remove_background` | bool | `false` |
| `pixel_size` | float or null | `null` |
| `force_recomputing` | bool | `false` |

- `image_type`: `"auto"`, `"brightfield"` or `"fluorescence"`. `"auto"` reads the acquisition class from the metadata, then the channel layout, then the image polarity (the last rule warns and asks for an explicit value). It selects the tissue detection, the `auto` Z projection, the background fill, the display rendering and the codec. See [Microscopy image types](../modalities/microscopy.md#image-types).
- `z_projection`: `"auto"`, `"max"`, `"best_focus"` or `"middle"`; how a Z-stack becomes one plane. `"auto"` is the maximum-intensity projection for fluorescence and the best-focus plane for brightfield.
- `crop_to_tissue`: crops to the tissue bounding box plus a fixed 100 µm margin (250 px without a pixel size). Pixels inside the box are unchanged; the box origin is recorded as the crop offset.
- `remove_background`: sets every pixel outside the tissue to the fill of the image type (the maximum of the sample type, white, for brightfield; 0 for fluorescence) and records it as `focus_background_fill`.
- `pixel_size`: µm per pixel, used only when the file metadata has none. A metadata value always wins; a difference over 5% is reported.
- `force_recomputing`: microscopy keeps no intermediate caches. The only cache is the output OME-TIFF itself, which is reused while it carries the current fingerprint and this flag is false.

Tissue is always detected (its outline is stored as an OME ROI and its pixels set the display windows), whether or not cropping or the fill is enabled. The output is lossless: the source sample type, every channel and no contrast change; contrast is stored as display windows. The detection thresholds, the crop margin and the pyramid levels are not configurable. See [Microscopy Preprocessing Methods](../scientific/microscopy_methods.md#10-parameters-and-effective-defaults).

Validation rejects an `image_type` or `z_projection` outside the values above, a `pixel_size` that is not a positive number, and `image_type: "fluorescence"` with `registration_type: "feature_extraction"`. The settings `color_enhancement`, `gamma`, `contrast_saturation`, `background_color`, `min_object_coverage`, `clip_percentile` and `crop_margin` were removed in `microscopy-3`; a configuration that sets one fails validation and names the reason.

Example:

```json
"processing_settings": {
  "image_type": "auto",
  "z_projection": "auto",
  "crop_to_tissue": false,
  "remove_background": false,
  "pixel_size": null,
  "force_recomputing": false
}
```

---

## `msi`

| Field | Type | Default |
|---|---|---|
| `mass_tolerance` | number | `10` |
| `sample_type` | string | `"tissue"` |
| `intensity_normalization` | string | `"none"` |
| `resolution` | string | `"standard"` |
| `recalibration_reference` | dict, string or null | `null` |
| `internal_standard_mz` | dict, string or null | `null` |
| `lipid_annotation_db` | string or null | `null` |
| `force_recomputing` | bool | `false` |

- `intensity_normalization` allowed values: `"none"`, `"tic"`, `"median"`, `"rms"`, `"internal_standard"`, applied per sample and per ion mode at a fixed scale shared by every sample. `"tic"` makes each spectrum sum to its number of features; `"median"` sets the median of its nonzero features to 1; `"rms"` sets its root mean square to 1; `"internal_standard"` sets the summed intensity of the internal standards to 1 and requires `internal_standard_mz`.
- `recalibration_reference` and `internal_standard_mz`: per-ion-mode m/z values, as a mapping (`{"pos": [760.585, 798.541], "neg": [885.550]}`) or as the string `"pos: 760.585, 798.541; neg: 885.550"`. `recalibration_reference` replaces the automatic reference selection; `internal_standard_mz` lists sprayed internal standards, which are always used as recalibration references.
- `sample_type` allowed values: `"tissue"` (section on a clean or empty background), `"tissue_noisy"` (section on a background rich in noise centroids), `"cell_culture"` (uniform culture, domains along gradients), `"microgrid"` (isolated cells of about one pixel in matrix). It sets the foreground detector and its spatial cleanup ([MSI methods §5](../scientific/msi_methods.md#5-foreground-detection)); foreground detection always runs. With both polarities, `cell_culture` pairs them by their recorded stage positions when their content cannot be registered ([MSI methods §2.2](../scientific/msi_methods.md#22-dual-ion-mode-affine-harmonization)).
- `resolution` allowed values: `"standard"` (default, native pixels), `"sub_cellular"` (weak high-resolution pixels, summed into raster blocks until the median block detects 200 features). It sets the aggregation of the alignment clustering; `sample_type: microgrid` does not use it.
- `lipid_annotation_db`: path to a CSV or JSON file with columns `db_name`, `ionized_mass`, `ion_mode`.
- `mass_tolerance`: positive number of ppm (integer or decimal). It sets the feature detection, the backbone merge span, the binning reach and the annotation window.
- Removed in `msi-13` and rejected by validation with the reason: `detect_background`, `frequency_threshold`, `min_intensity_threshold`, and the normalizations `"log"`, `"clr"` (use `"none"`) and `"tic_mean_scaled"` (use `"tic"`).

The example below enables TIC normalization explicitly; it is not the default.

Example:

```json
"processing_settings": {
  "mass_tolerance": 10,
  "intensity_normalization": "tic",
  "sample_type": "tissue",
  "recalibration_reference": null,
  "lipid_annotation_db": null,
  "force_recomputing": false
}
```

---

## `raman`

| Field | Type | Default | Applied by |
|---|---|---|---|
| `denoise` | bool | `true` | Preprocessing (stored pixels) |
| `remove_offset` | bool | `false` | Registration (averaged spectra) |
| `normalization` | string | `"none"` | Registration (averaged spectra) |
| `force_recomputing` | bool | `false` | Preprocessing |

- `denoise`: projects every pixel spectrum of a multi-channel acquisition on the spectral components that are real at the cellular scale (4.5 µm bins). The projection is linear, so the stored values stay linear in concentration.
- `remove_offset`: recorded per multi-channel image as `focus_spot_offset: "min"`. Registration (`raman_pixel_interpolation`, `pixel_composition`) subtracts from each averaged spectrum (spot mean, spot-sized block) its minimum over the image's channels, the near-flat non-resonant background. The stored pixels are not changed.
- `normalization`: `"none"` or `"area"`. `"area"` is recorded per multi-channel image as `focus_spot_normalization: "area"`; registration divides each averaged spectrum by its mean over the image's channels (NaN when that mean is not positive). `"none"` keeps concentration-linear spot spectra.
- `force_recomputing`: Raman keeps no intermediate caches. The only cache is the output OME-TIFF, reused while it carries the current fingerprint and this flag is false.

The stored pixels are always linear intensities: on a single noisy pixel the minimum and the mean are biased by an amount that follows the illumination, so offset removal and normalization are applied to spectra averaged over many pixels. `remove_offset` and `normalization` stay in the preprocessing fingerprint: changing one reprocesses the sample but leaves its pixels unchanged, so the alignments stay valid and the registration is recomputed. Single-channel acquisitions are neither offset-corrected nor normalized. See [Raman](../modalities/raman.md#processing-parameters) and [Raman Preprocessing Methods](../scientific/raman_methods.md#51-offset-and-normalisation).

Validation rejects a `normalization` outside the values above and non-boolean `denoise` or `remove_offset`. The settings `savgol_window`, `savgol_polyorder`, `bg_min_area_fraction`, `otsu_threshold_factor`, `min_object_size` and `max_workers` were removed in `raman-2`; a configuration that sets one fails validation and names the reason.

Example:

```json
"processing_settings": {
  "denoise": true,
  "remove_offset": false,
  "normalization": "none",
  "force_recomputing": false
}
```

---

## `st`

| Field | Type | Default |
|---|---|---|
| `capture` | string | `"sequencing"` |
| `unit` | string | `"spot"` |
| `normalization` | string | `"auto"` |
| `quality_control` | bool | `true` |
| `remove_controls_and_empty` | bool | `true` |
| `remove_mitochondrial_genes` | bool | `false` |
| `force_recomputing` | bool | `false` |

- `capture`: `"sequencing"` (Visium, Visium HD, Stereo-seq, Slide-seq) or `"imaging"` (Xenium, MERFISH, CosMx). Sets the gene rule (sequencing: detected in at least 20 spots of one sample; imaging: every detected gene) and the `auto` normalization.
- `unit`: `"spot"` (multicellular spots), `"bin"` (sub-cellular square bins or beads) or `"cell"` (segmented cells). Sets the outlier rule of `quality_control` and the clustering preset of the alignment colours.
- `normalization`: `"auto"`, `"none"`, `"log_normalize"`, `"cp10k_log"`, `"size_factors"` or `"area_log_normalize"`, applied once to the merged dataset. Any value but `"none"` keeps the counts in `.layers['raw']`. See [ST normalization](../modalities/transcriptomics.md#normalization).
- `quality_control`: removes the outlier spots of the unit's rule (local outliers for spots, global outliers for cells, none for bins). Out-of-tissue spots are removed in every case.
- `remove_controls_and_empty`: when `true` (default), removes the control features (probes, codewords, blanks) and the spots without counts. Set `false` to keep every feature and every observation, for example when an imaging panel holds only probes and the cells serve as alignment targets. `obs['pct_counts_control']` is recorded either way.
- `remove_mitochondrial_genes`: when `true`, drops mitochondrial genes (flagged by a case-insensitive `MT-`/`MT.` name prefix) from the feature set. Applied per sample, after QC metrics are computed, so `pct_counts_mt` still describes the matrix before removal.
- The settings `resolution`, `min_count_per_spot`, `max_count_per_spot`, `min_genes_per_spot`, `max_genes_per_spot`, `min_spots_per_gene`, `min_count_spots_ratio_per_gene`, `total_counts_normalize` and `log1p_transform` were removed; a configuration that sets one fails validation and names the replacement.

Example:

```json
"processing_settings": {
  "capture": "sequencing",
  "unit": "bin",
  "normalization": "auto",
  "quality_control": true,
  "remove_controls_and_empty": true,
  "remove_mitochondrial_genes": false,
  "force_recomputing": false
}
```

---

## Registration Settings

### `feature_extraction`

Compatible modality type: `microscopy_image`, and only for **brightfield** images; the encoder (Prov-GigaPath) is pretrained on tiles from H&E whole-slide images. An image recorded as fluorescence is rejected: at validation time when `image_type` is `"fluorescence"`, and at registration time from the `focus_image_type` recorded by preprocessing. The stain of a brightfield image is not verified, so an IHC or other-stain image is encoded without error and yields embeddings that carry no morphological meaning. Use `"none"` for those modalities. See [Registration](../pipeline/registration.md#feature_extraction).

| Field | Type | Default |
|---|---|---|
| `background_color` | string | `"white"` |
| `patch_embeddings` | bool | `false` |
| `force_recomputing` | bool | `false` |

- `background_color`: `"white"` or `"black"`. This is the colour counted by the background test: an encoder token whose pixels match it in at least 99% of positions is never read, and a reference spot whose footprint holds only such tokens is stored as an all-zero embedding. It applies to images that preprocessing did not fill; when `remove_background` filled the image, the recorded `focus_background_fill` is used instead (0 counts as black, any other value as white).
- `patch_embeddings`: also store, for every reference spot, the embedding of the 224 px encoder patch around it (`obsm['patch_embedding']`), that patch's centre in the reference frame (`obsm['patch_spatial']`) and the patch index (`obs['patch_id']`, unique within a sample, `-1` for spots with an all-zero embedding). The patch embedding describes about 112 × 112 µm of tissue and is shared by the spots of one patch, so it is coarser but much less noisy than the spot embedding in `X`. It roughly doubles the size of the registered file. Changing it recomputes the registration.
- `force_recomputing`: recompute even when the cached registration matches its inputs.

The encoder window and the encoded pyramid level are not settings: the window is the encoder's input size and the level is the one closest to 0.5 µm per pixel. A `patch_size` key is ignored with a warning.

Example:

```json
"registration_settings": {
  "background_color": "white",
  "patch_embeddings": false,
  "force_recomputing": false
}
```

### `spot_interpolation`

Compatible modality types: `msi`, `st`. Registers the target spots onto each anchor footprint by overlap area, for any combination of spot sizes. ST counts are shared by overlap and normalized once on the merged file; MSI intensities are area-weighted means. Reference and target coordinates and `spot_size` must be in µm. `spot_aggregation` was removed and fails validation. See [Registration](../pipeline/registration.md#spot_interpolation).

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

### `raman_pixel_interpolation`

Compatible modality type: `raman`. Uses the same Gaussian footprint interpolation as `spot_interpolation`, applied to the pixels of the hyperspectral OME-TIFF. See [Registration](../pipeline/registration.md#raman_pixel_interpolation).

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

### `pixel_composition`

Compatible modality types: `raman`, and `microscopy_image` recorded as fluorescence. Each reference spot gets the fraction of its footprint occupied by each pixel phenotype of a dataset-level codebook (Raman: linear spectral unmixing endmembers; fluorescence: Pixie pixel clusters), with the mean pixel values in `obsm['channel_mean']` and a per-sample label map. The number of phenotypes is set by the data; there are no method parameters. See [Registration](../pipeline/registration.md#pixel_composition).

| Field | Type | Default |
|---|---|---|
| `force_recomputing` | bool | `false` |

Example:

```json
"registration_settings": {
  "force_recomputing": false
}
```

---

## Consistent End-to-End Example

```json
{
  "dataset_path": "/data/my_tissue_cohort",
  "reference_modality": "st",
  "perform_alignment": true,
  "perform_registration": true,
  "huggingface_token": "hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "spatial_annotations": {
    "modality_name": "microscopy",
    "file_type": "geojson"
  },
  "modalities": [
    {
      "name": "st",
      "type": "st",
      "alignment_strategy": "manual",
      "registration_type": "none",
      "processing_settings": {
        "capture": "sequencing",
        "unit": "spot",
        "normalization": "auto",
        "force_recomputing": false
      },
      "registration_settings": {}
    },
    {
      "name": "msi",
      "type": "msi",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "spot_interpolation",
      "processing_settings": {
        "mass_tolerance": 10,
        "intensity_normalization": "tic",
        "sample_type": "tissue",
        "recalibration_reference": null,
        "lipid_annotation_db": "resources/lipid_db.csv",
        "force_recomputing": false
      },
      "registration_settings": {
        "force_recomputing": false
      }
    },
    {
      "name": "raman",
      "type": "raman",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "raman_pixel_interpolation",
      "processing_settings": {
        "denoise": true,
        "remove_offset": false,
        "normalization": "none",
        "force_recomputing": false
      },
      "registration_settings": {
        "force_recomputing": false
      }
    },
    {
      "name": "microscopy",
      "type": "microscopy_image",
      "alignment_strategy": "manual",
      "alignment_force_recomputing": false,
      "registration_type": "feature_extraction",
      "processing_settings": {
        "image_type": "brightfield",
        "z_projection": "auto",
        "crop_to_tissue": false,
        "remove_background": false,
        "pixel_size": null,
        "force_recomputing": false
      },
      "registration_settings": {
        "background_color": "white",
        "force_recomputing": false
      }
    }
  ]
}
```

---

## Validation Checklist

- `dataset_path` exists and is readable/writable.
- `reference_modality` matches one declared modality `name`.
- Every sample directory contains every declared modality subdirectory.
- If `registration_type: "feature_extraction"` is used, `huggingface_token` is set; a CUDA GPU is strongly recommended (the CPU fallback is impractically slow for real images).
- `alignment_strategy: "pre_aligned"` is only used where modalities are already co-registered.
- `spatial_annotations.file_type` is `"geojson"` when annotations are enabled.
