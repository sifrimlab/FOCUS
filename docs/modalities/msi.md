# Mass Spectrometry Imaging (MSI / Lipidomics)

## Overview

Mass Spectrometry Imaging (MSI) provides spatially resolved molecular profiles, most commonly lipid distributions, across tissue sections. FOCUS reads the imzML/IBD file format produced by Bruker instruments and compatible exporters. Both positive and negative ion modes are supported, either independently or as a paired dual-mode acquisition from the same tissue section.

The preprocessing pipeline parses the imzML metadata, corrects any instrument rotation error, optionally aligns dual ion mode acquisitions, recalibrates the m/z of every spectrum, builds a consensus m/z grid across all spectra and samples, bins each spectrum onto that grid, detects tissue versus background spots, normalises intensities, and outputs an AnnData object per sample plus a merged multi-sample dataset.

---

## Input Format

MSI data is stored as two paired files:

| File | Role |
|------|------|
| `.imzML` | XML metadata: pixel grid, scan settings, per-spectrum binary offsets and data types |
| `.ibd` | Binary file: raw m/z arrays and intensity arrays, indexed by the offsets in the imzML |

FOCUS automatically locates the `.imzML` file in each ion mode directory and pairs it with the `.ibd` file of the same base name in the same directory.

---

## Directory Layout

The `.imzML` / `.ibd` files always live in a `pos/` or `neg/` subdirectory of the modality folder, never directly in it. There is one subdirectory per acquired ion mode.

=== "Single ion mode"

    ```
    dataset_root/
    ├── sample_A/
    │   └── lipidomics/
    │       └── pos/
    │           ├── data.imzML
    │           └── data.ibd
    ```

=== "Dual ion mode (positive + negative)"

    ```
    dataset_root/
    ├── sample_A/
    │   └── lipidomics/
    │       ├── pos/
    │       │   ├── data.imzML
    │       │   └── data.ibd
    │       └── neg/
    │           ├── data.imzML
    │           └── data.ibd
    ```

FOCUS decides a sample's ion modes from the **files** it finds, not from the directory structure: an ion mode counts as acquired when its subdirectory holds a complete `.imzML` + `.ibd` pair. An ion mode subdirectory that holds neither file is read as "this polarity was not acquired" and is ignored, so the empty `neg/` (or `pos/`) folder that the GUI scaffolds for every MSI sample needs no cleanup. Ion modes can differ from sample to sample within one dataset.

Two cases are errors: a sample with no complete pair in either subdirectory, and a subdirectory holding an incomplete acquisition (an `.imzML` without its `.ibd`, an `.ibd` without its `.imzML`, or files whose base names do not match). Both fail during configuration validation, before any processing starts.

!!! tip "File naming"
    Any base name is accepted, as long as the `.imzML` and `.ibd` share it. If a directory holds several acquisitions, FOCUS uses the alphabetically first `.imzML` that has a matching `.ibd`.

---

## Reserved Directories

Within `<dataset_path>`, certain directory names are reserved and treated specially by FOCUS:

| Directory | Purpose |
|-----------|---------|
| `merged/` | Stores merged outputs from all pipeline stages (preprocessing, alignment, registration, compilation). FOCUS creates and manages this directory automatically. |
| `resources/` | User-controlled directory for storing additional resources and reference files needed for this dataset (e.g., lipid annotation databases, custom scripts, supplementary data). FOCUS ignores this directory when discovering samples. |
| `plots/` | User-controlled directory for storing plots and visualizations generated during downstream analysis. FOCUS ignores this directory when discovering samples. |
| `preprocessing/`, `alignment/`, `registration/`, `annotations/` | Per-stage output directory names. These are the same names used *inside* each sample folder, and they are excluded from sample discovery at the dataset root as well. |

All other directories at `<dataset_path>/` are treated as sample directories, with the directory name becoming the `sample_id` in all outputs. None of the seven reserved names above can be used as a `sample_id`.

---

## Lipid Annotation Database

### Format

The lipid annotation database is a **tabular** file (CSV or JSON) with one row per ionized lipid species. It must contain the following three columns:

| Column | Type | Description |
|--------|------|-------------|
| `db_name` | string | Lipid name or identifier |
| `ionized_mass` | float | Theoretical ionized (m/z) mass of the species |
| `ion_mode` | string | Ion mode of the species: `pos` or `neg` |

CSV example:

```csv
db_name,ionized_mass,ion_mode
Phosphatidylcholine(32:0),734.569,pos
Phosphatidylcholine(34:1),760.584,pos
Sphingomyelin(d18:1/16:0),703.598,pos
Phosphatidylethanolamine(36:2),764.536,neg
Phosphatidylserine(38:4),834.525,neg
```

JSON equivalent (a list of records, or any orientation `pandas.read_json` accepts):

```json
[
  {"db_name": "Phosphatidylcholine(32:0)", "ionized_mass": 734.569, "ion_mode": "pos"},
  {"db_name": "Phosphatidylcholine(34:1)", "ionized_mass": 760.584, "ion_mode": "pos"},
  {"db_name": "Phosphatidylethanolamine(36:2)", "ionized_mass": 764.536, "ion_mode": "neg"}
]
```

### Location

The annotation database can be stored:

1. **In the dataset's `resources/` folder** (recommended for convenience):
   ```
   dataset_root/
   ├── resources/
   │   └── lipid_annotation_db.json
   ├── sample_A/
   │   └── lipidomics/
   │       ├── pos/
   │       │   ├── data.imzML
   │       │   └── data.ibd
   │       └── neg/
   │           ├── data.imzML
   │           └── data.ibd
   ```

2. **At any system path**: Configure the full path in the `lipid_annotation_db` field of your FOCUS config.

### Configuration

In your FOCUS configuration file, specify the database location via the `lipid_annotation_db` parameter:

```yaml
modalities:
  - name: lipidomics
    type: msi
    processing_settings:
      lipid_annotation_db: resources/lipid_annotation_db.json  # Path relative to dataset_root
      # ... other settings
```

If the database is omitted or set to `None`, FOCUS will process the data without annotation.

---

## Preprocessing Steps

The steps run in this order; [MSI methods](../scientific/msi_methods.md) gives every method with its references.

1. **Parse imzML metadata**: pixel grid coordinates, stage coordinates (µm), pixel size, per-spectrum binary offsets and data types. Arrays are identified by their `m/z array` and `intensity array` terms; intensities may be floats or integers. The pixel size is read as a decimal number of µm; when the file declares none it is inferred from the stage positions, and without stage positions preprocessing stops with an error. Compressed binary arrays and profile spectra raise `ValueError`: FOCUS reads uncompressed, centroided spectra. Peaks at m/z 0 or below are dropped.

2. **Rotation correction**: a linear regression is fit to the stage coordinates of the most densely sampled pixel column, and the resulting slope angle is used to de-rotate all physical coordinates to align the scan with the Cartesian axes.

3. **Dual ion mode alignment**: when both ion modes are present, each positive spectrum is paired with the negative spectrum acquired at the same stage position, after removing the constant offset between the two acquisitions ([MSI methods §2.2](../scientific/msi_methods.md#22-dual-ion-mode-affine-harmonization)); spectra acquired in one mode only are removed. Files without stage positions are paired by pixel index, which is correct only when both modes cover the same raster. An affine transformation is fitted from positive to negative mode physical coordinates; the transformed positive coordinates are averaged with the negative coordinates to yield the consensus spot centre positions.

4. **Recalibration** ([MSI methods §4](../scientific/msi_methods.md#4-recalibration)): unless a `recalibration_reference` is supplied, each sample gets its own ten reference peaks per ion mode, selected from **all** its spectra before any foreground is known, so matrix ions present on the whole slide are eligible calibrants. Sprayed internal standards given in `internal_standard_mz` are always included and anchor the mass scale; without them, one ppm shift per sample puts all samples on a common mass scale. Every spectrum then gets its own correction, linear in m/z: a per-scan-line drift plus a per-pixel residual. The applied correction is stored per spot in `obs["recalibration_ppm_<mode>"]`.

5. **Foreground detection** ([MSI methods §5](../scientific/msi_methods.md#5-foreground-detection)): always runs, on the recalibrated spectra, without a lipid database; `sample_type` selects the detector.
    - `tissue`, `tissue_noisy`, `cell_culture`: each spectrum is scored by the number of recurrent ions it holds above its own noise, which noise centroids never raise. A two-component mixture separates a background only when the two score modes are cleanly apart and the lower one holds at most half the ions of the higher one; otherwise every spectrum is foreground. The cleanup follows the preset: hole filling and opening for a section, a stronger opening and removal of small components for a noisy background, a majority vote that keeps gaps between cells for a culture.
    - `microgrid`: cells of about one pixel are local outliers against the ions present on most of the slide, thresholded with hysteresis, without spatial cleanup.

    The result is stored in `obs["foreground"]` and `obs["foreground_score"]`; all spectra remain in the output.

    !!! note "Alignment of microgrid experiments"
        When any MSI modality has `sample_type="microgrid"`, the alignment GUI shows only the foreground spots of every spot modality of the experiment, all in one colour, because microgrid alignment relies on the positions of the foreground spots and not on cluster patterns. The confirmed transform is applied to all spots, foreground and background. Image modalities are displayed at their lowest pyramid level as usual.

6. **Per-sample feature selection** ([MSI methods §6](../scientific/msi_methods.md#6-per-sample-feature-selection)), from the foreground spectra of each sample:
    - **Frequency**: the ions that recur at the same m/z above the local background, whatever their intensity, present in at least 1% of the spectra of the sample's most present ion.
    - **Annotation** with the `lipid_annotation_db`, reported with the number of annotations expected by chance.
    - **S/N filter**: a feature is kept when its median signal-to-noise over the spectra holding it is at least 3, the noise being measured on the sample's **background** spectra. Annotated features are always kept. Without an independent background (a section that fills the raster, or a background without noise centroids), the filter is skipped and every feature is kept.

7. **Backbone**: the kept features of all samples are pooled and split at their largest gaps until every group spans at most `mass_tolerance` ppm, so no feature is lost and one ion seen a few ppm apart in two samples becomes one feature ([MSI methods §7](../scientific/msi_methods.md#7-backbone-construction)). Each feature carries a Gaussian width measured from the spread of its peaks (`var["mz_sigma_ppm"]`).

8. **Intensity binning**: a feature alone within `mass_tolerance` ppm of a peak takes the whole peak; features closer than the tolerance share it by Gaussian weight of their widths, so the share moves smoothly with the peak position instead of switching between features, and every peak's intensity is conserved ([MSI methods §8](../scientific/msi_methods.md#8-intensity-binning)). The binned matrix is sparse and stored in `.layers['raw']`.

9. **Intensity normalisation** ([MSI methods §9](../scientific/msi_methods.md#9-intensity-normalization)): `none`, `tic` (rows sum to the number of features), `median` (median of the nonzero features is 1), `rms` (root mean square is 1) or `internal_standard` (standard intensity is 1; requires `internal_standard_mz`). Each divides a spectrum by one value at a fixed scale, per sample and ion mode, so all samples share the scale.

10. **Per-sample cluster labels**: labels used only to colour spots in the alignment GUI, stored in `.obs['cluster']`. Samples with more than 250,000 spots are first *coarsened* into k x k blocks of the acquisition raster whose spots are summed. Then PCA, each unit's PCs joined to the mean PCs of its spatial neighbourhood (1 ring for `resolution="standard"`, 2 rings for `"sub_cellular"`, none for `sample_type="microgrid"`), neighbour graph, Leiden (`resolution=0.5`), and adjacent clusters that no density valley separates are merged. See [MSI methods §10](../scientific/msi_methods.md#10-per-sample-clustering). Only `.obs['cluster']` is persisted.

11. **Format as AnnData**: the binned matrices, spatial coordinates and metadata are written per sample as a gzip-compressed `.h5ad`, and all samples are concatenated into a merged dataset (inner join on m/z features).

---

## Processing Parameters

| Name | Type | Default | Description | Allowed values |
|------|------|---------|-------------|----------------|
| `mass_tolerance` | `float` | `10` | Mass tolerance in ppm: feature detection, backbone merge, binning reach and annotation window | Positive number |
| `sample_type` | `str` | `"tissue"` | Experiment type; calibrates foreground detection and its cleanup | `"tissue"` (clean background), `"tissue_noisy"` (noisy background), `"cell_culture"` (gradient domains), `"microgrid"` (isolated single cells) |
| `intensity_normalization` | `str` | `"none"` | Row normalisation, applied per sample and ion mode at a fixed scale | `"none"`, `"tic"`, `"median"`, `"rms"`, `"internal_standard"` |
| `resolution` | `str` | `"standard"` | Clustering preset for the alignment colours: `"standard"` for multicellular pixels, `"sub_cellular"` for weak high-resolution pixels (wider spatial neighbourhood). See [MSI methods §10](../scientific/msi_methods.md#10-per-sample-clustering) | `"standard"`, `"sub_cellular"` |
| `recalibration_reference` | `dict`, `str` or `None` | `None` | Per-ion-mode reference m/z values; selected from the dataset when `None` | `None`, `{"pos": [760.585, ...], "neg": [...]}` or `"pos: 760.585, 798.541; neg: 885.550"` |
| `internal_standard_mz` | `dict`, `str` or `None` | `None` | m/z of internal standards sprayed over the sections, per ion mode. Always used as recalibration references (anchoring the mass scale) and required by `internal_standard` normalisation | Same forms as `recalibration_reference` |
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database (columns: `db_name`, `ionized_mass`, `ion_mode`) | File path or `None` |
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist | `True`, `False` |

!!! note "Removed settings"
    Configuration validation rejects the settings removed in `msi-13`, with the reason: `detect_background` (foreground detection always runs and `sample_type` calibrates it), `frequency_threshold` (fixed; noise is removed by the recurrence test and the S/N filter) and `min_intensity_threshold` (replaced by the S/N filter). The normalizations `log` and `clr` (use `none` and transform downstream) and `tic_mean_scaled` (use `tic`) were removed.

---

## Registration

!!! warning "Spot-based registration only"
    MSI is a spot-based modality, so it supports `spot_interpolation` and `spot_aggregation`. `feature_extraction` is **not** compatible.

`spot_interpolation` performs Gaussian-weighted interpolation: for each anchor spot (from the reference modality), all MSI spots whose centres lie within the anchor's footprint are averaged, weighting each by `exp(−d²/2σ²)` of its distance `d` to the anchor centre, with `σ = √(sx·sy)/2` from the anchor's `spot_size`. Footprint and distance are evaluated in the anchor's own frame, through the alignment transform. The result is a new feature vector at each anchor position.

```yaml
registration_type: spot_interpolation
```

`spot_aggregation` uses the same footprint but **sums** the MSI spots inside it instead of averaging them: equal weight, no Gaussian kernel, and no normalization. Use it for subcellular-resolution acquisitions, where one anchor spot covers many MSI spots. See [Registration](../pipeline/registration.md#spot_aggregation).

```yaml
registration_type: spot_aggregation
```

No additional registration settings are required for MSI.

---

## Output

### Per-sample AnnData

Path: `<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.h5ad`

| Slot | Content |
|------|---------|
| `.X` | Normalised binned intensity matrix `(N_spots, N_mz)`, sparse CSR `float32` |
| `.layers['raw']` | Binned intensities before normalisation, sparse CSR `float32` |
| `.var['mz']` | Consensus m/z values (float32) |
| `.var['mz_sigma_ppm']` | Gaussian width of each feature in ppm, used by the binning (float32) |
| `.var['mz_mode']` | Ion mode for each m/z (`"pos"` or `"neg"`) |
| `.var['lipid_annotation']` | Lipid annotation string: `"; "`-separated `db_name` hits, or `"Unannotated"` (categorical) |
| `.obsm['spatial']` | Physical spot coordinates in µm, shape `(N_spots, 2)`, `float32` |
| `.obsm['raster_coordinates']` | Raster cell bounding-box corners in µm, shape `(N_spots, 2, 2)` as `[[x1, y1], [x2, y2]]` |
| `.obs['sample_id']` | Sample identifier (categorical) |
| `.obs['foreground']` | Categorical boolean mask identifying foreground (`True`) vs. background (`False`) spots. **Always present**; all `True` when no background was separated |
| `.obs['foreground_score']` | Foreground detection score (`float32`): log(1 + recurrent ions above noise), robust z for microgrids |
| `.obs['cluster']` | Per-sample cluster labels for alignment colouring (categorical strings) |
| `.obs['recalibration_ppm_<mode>']` | Applied m/z correction in ppm at the mean reference m/z, per ion mode (`float32`; NaN for a sample without that mode) |
| `.obs['recalibration_matches_<mode>']` | Number of references matched in the spot's spectrum at pixel level (`int16`) |
| `.uns['recalibration_reference']` | Reference m/z per ion mode used to recalibrate the sample, on the common mass scale (merged file: per sample, then per ion mode) |
| `.uns['recalibration_pool']` | Calibrant pool and mass-scale shift per ion mode, used when an interrupted run resumes (merged file: per sample) |
| `.uns['focus_preprocessing_fingerprint']` | Fingerprint of inputs, settings and method version; a change rebuilds the dataset and re-runs alignment and registration of the modality |
| `.uns['spot_size']` | Raster pixel size `[x_µm, y_µm]`. **Read from the `.imzML` pixel size**, inferred from the stage positions when the file declares none; without either, preprocessing stops with an error. In dual ion mode the size is taken from the positive mode (both modes are expected to share the same raster size). In the merged file this becomes a dict keyed by `sample_id`. When MSI is the reference modality, it is the registration footprint of every MSI spot. |

### Merged dataset

Path: `merged/preprocessing/<modality_name>_merged_processed.h5ad`

Contains concatenated data from all samples with the same slot structure.

---

## Config Example

```yaml
modalities:
  - name: lipidomics
    type: msi
    processing_settings:
      lipid_annotation_db: resources/lipid_db.csv
      mass_tolerance: 10
      intensity_normalization: tic
      sample_type: tissue
    registration_type: spot_interpolation
```

=== "Dual ion mode"

    ```yaml
    modalities:
      - name: lipidomics
        type: msi
        processing_settings:
          lipid_annotation_db: resources/lipid_db.csv
          mass_tolerance: 10
          intensity_normalization: tic
          sample_type: tissue
        registration_type: spot_interpolation
    ```

    The processing settings are identical. There is no ion mode switch in the configuration. Provide a complete `.imzML` + `.ibd` pair in both the `pos/` and `neg/` subdirectory of each sample; FOCUS detects the dual-mode layout from those files.
