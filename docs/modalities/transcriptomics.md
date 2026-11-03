# Spatial Transcriptomics

## Overview

FOCUS accepts spatially resolved gene expression from any technology, provided as one AnnData `.h5ad` file per sample with raw counts, `.obsm['spatial']` and `.uns['spot_size']`. Two settings describe the technology: `capture` (sequencing or imaging) and `unit` (multicellular spots, sub-cellular bins or segmented cells). They select the quality control, the gene rule, the default normalization and the clustering preset, so no numeric threshold is configured.

Each sample is validated, cleaned of control features and low-quality spots, given QC metrics and alignment cluster labels, and written with raw counts. The samples are then merged on the union of their genes, restricted to the genes the technology rule keeps, and normalized once with one global scale. The merged file is the dataset used by registration and compilation.

---

## Input Requirements

| Field | Required | Description |
|-------|----------|-------------|
| Raw counts | Yes | Non-negative integer counts in `.X`, `.layers['counts']`, `.layers['raw']` or `.raw.X`; the first of these that holds counts is used, and every other matrix of the input is dropped |
| `.obsm['spatial']` | Yes | Spot or cell centres in µm, finite, shape `(N, 2)` |
| `.uns['spot_size']` | Yes | Spot, bin or cell diameter `[x, y]` in µm. A scalar or 1-element array is broadcast to `[v, v]`; more than 2 values, a non-positive value or a missing field raise `ValueError` |
| `.var_names` | Yes | Gene names. Duplicated names are made unique with a numeric suffix and reported |
| `.var['feature_types']` | No | When present, features other than `Gene Expression` are removed as controls |
| `.obs['in_tissue']` | No | When present, spots with `in_tissue == 0` are removed |
| `.obs` cell area | No | For `unit: cell`, the first of `cell_area`, `area_um2`, `area`, `Area`, `volume` is the area used by area normalization |

!!! tip "spot_size"
    When ST is the reference modality, `spot_size` sets the footprint of every reference spot in all registration modes (`spot_interpolation`, `spot_aggregation`, `raman_pixel_interpolation`, `feature_extraction`), so it must match the technology (see the table below). As a registration target, ST contributes its spot centres; the footprint is the reference's.

---

## Typical Spot Sizes by Technology

Approximate values; check the specification of the data.

| Technology | `capture` | `unit` | Typical `spot_size` (µm) |
|-----------|-----------|--------|--------------------------|
| Visium | `sequencing` | `spot` | 55 |
| Visium HD (2, 8 or 16 µm bins) | `sequencing` | `bin` | bin size |
| Visium HD segmented cells | `sequencing` | `cell` | median cell diameter |
| Stereo-seq (binned) | `sequencing` | `bin` | bin size (0.5 µm per DNB times the bin width) |
| Slide-seq V2 | `sequencing` | `bin` | 10 |
| Xenium, MERFISH, CosMx (segmented cells) | `imaging` | `cell` | median cell diameter |
| Xenium, CosMx (transcripts binned into squares) | `imaging` | `bin` | bin size |

---

## Directory Layout

Place exactly one `.h5ad` file per sample inside `<sample_id>/<modality_name>/`; a folder with several `.h5ad` files raises `ValueError`.

```
dataset_root/
├── sample_A/
│   └── visium/
│       └── sample_A.h5ad
├── sample_B/
│   └── visium/
│       └── sample_B.h5ad
```

---

## Preprocessing Steps

### Per-sample steps

Run by `SpatialTranscriptomic.preprocess_data()`. A cached file is reused only when its fingerprint (input SHA-256, settings, technology constants, method and software versions) matches; `force_recomputing=True` always rebuilds. The input SHA-256 is stored in the output and reused while the input's path, size and modification time are unchanged, so an unchanged input is not re-hashed.

1. **Load and validate**: see [Input Requirements](#input-requirements). `.X` becomes the raw counts in sparse CSR.
2. **Control features**: features matched by `var['feature_types']` or by a control name prefix (`NegControlProbe_`, `NegControlCodeword_`, `NegPrb`, `BLANK_`, `Blank-`, `UnassignedCodeword_`, `DeprecatedCodeword_`, `Intergenic_`, `SystemControl`) are counted into `obs['total_counts_control']` and `obs['pct_counts_control']`, then removed.
3. **Mitochondrial flag**: `.var['mt']` from a case-insensitive `MT-`/`MT.` name prefix. Ensembl-keyed panels are not flagged.
4. **Quality control**: spots outside the tissue (`in_tissue == 0`) and spots without counts are always removed. With `quality_control: true`, the outlier rule of the unit follows (see [Technology calibration](#technology-calibration)). The retained spots and the number removed per reason are reported, and the counts are stored in `.uns['focus_qc']`.
5. **QC metrics**: `scanpy.pp.calculate_qc_metrics(qc_vars=['mt'], percent_top=None)` with mitochondrial genes present: `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt` and `log1p_` variants in `.obs`, gene metrics in `.var`.
6. **Area**: `obs['focus_area']` holds the area used by area normalization: the `spot_size` area for spots and bins, the cell area column for cells.
7. **Mitochondrial gene removal (optional)**: with `remove_mitochondrial_genes: true`, the flagged genes are dropped. The QC metrics of step 5 still describe the matrix before removal.
8. **Observation names**: prefixed with `<sample_id>_`, unless every name already carries that prefix.
9. **Cluster labels**: alignment-only labels with the clustering preset of the unit (`spot`: standard; `bin`, `cell`: sub-cellular). See [the scientific methods](../scientific/st_methods.md#36-per-sample-clustering).
10. **Write**: raw counts in `.X` (no layer), sparse CSR, gzip, written to a temporary file and renamed, so an interrupted run never leaves a truncated cache.

### Dataset-level steps

Run by `SpatialTranscriptomicDataset.process_dataset()`. The cached merged file is reused when no sample was rebuilt, `force_recomputing` is `False`, and its fingerprint (sample fingerprints, normalization, gene rule) matches.

1. **Gene rule**: from each sample's `var['n_cells_by_counts']` (spots in which the gene is detected), a gene is kept when it reaches the technology's minimum in at least one sample.
2. **Concatenate on disk**: `anndata.concat_on_disk` with an outer join and `fill_value=0` builds the union panel; the genes that fail the rule are then removed. Spots are never removed at this level, so the merged rows match the per-sample files.
3. **QC**: `total_counts`, `n_genes_by_counts` and the gene metrics are recomputed on the final panel. `total_counts_mt`, `pct_counts_mt` and the control metrics keep the per-sample values.
4. **Normalization**: the selected method runs once on the merged counts (see [Normalization](#normalization)). The counts move to `.layers['raw']` and `.X` is `float32`.
5. **Write**: `.obs['sample_id']` and `.obs['cluster']` as categoricals, `.uns['spot_size']` as `{sample_id: [x, y]}`, `.uns['focus_normalization']` with the method and its scale, sparse CSR, gzip, atomic write.

---

## Processing Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `capture` | `str` | `"sequencing"` | `"sequencing"` (Visium, Visium HD, Stereo-seq, Slide-seq) or `"imaging"` (Xenium, MERFISH, CosMx) |
| `unit` | `str` | `"spot"` | `"spot"` (multicellular capture spots), `"bin"` (sub-cellular square bins or beads) or `"cell"` (segmented cells) |
| `normalization` | `str` | `"auto"` | Method applied to the merged dataset (see [Normalization](#normalization)) |
| `quality_control` | `bool` | `True` | Remove the outlier spots of the unit's rule. Out-of-tissue and zero-count spots are removed in every case |
| `remove_mitochondrial_genes` | `bool` | `False` | Drop the genes flagged in `.var['mt']`, per sample |
| `force_recomputing` | `bool` | `False` | Reprocess even if cached output files already exist |

!!! warning "Removed settings"
    `resolution`, `min_count_per_spot`, `max_count_per_spot`, `min_genes_per_spot`, `max_genes_per_spot`, `min_spots_per_gene`, `min_count_spots_ratio_per_gene`, `total_counts_normalize` and `log1p_transform` are no longer ST settings. A configuration that sets one raises `ValueError` naming its replacement. The old `total_counts_normalize: true` with `log1p_transform: true` corresponds to `normalization: cp10k_log`.

### Technology calibration

| | `spot` | `bin` | `cell` |
|---|---|---|---|
| Outlier rule (`quality_control: true`) | Local outliers against the 36 nearest spots: low library size, low detected genes, high mitochondrial percent | None | Global low-tail outliers of library size and detected genes (5 MADs) |
| Clustering preset | standard | sub-cellular | sub-cellular |

| | `sequencing` | `imaging` |
|---|---|---|
| Gene rule | Detected in at least 20 spots of at least one sample | Every detected gene (designed panels) |
| `normalization: auto` | `log_normalize` | `area_log_normalize` (`log_normalize`, with a warning, when a cell has no area) |

The rules and their sources are described in [the scientific methods](../scientific/st_methods.md#3-per-sample-preprocessing-pipeline).

### Normalization

| Value | Result in `.X` |
|-------|----------------|
| `auto` | The technology's default (table above) |
| `none` | Raw counts; no `raw` layer |
| `log_normalize` | `log1p(counts / s)`, `s` = library size divided by the median library size of all spots of all samples |
| `cp10k_log` | `log1p(10,000 × counts / library size)` (Seurat LogNormalize) |
| `size_factors` | `counts / s`, linear |
| `area_log_normalize` | `log1p(counts / a)`, `a` = area divided by the median area of all spots of all samples |

---

## Using ST as Reference vs Non-Reference

### ST as the reference modality

When spatial transcriptomics defines the coordinate system, all other modalities are registered onto ST spots. Set `reference_modality` to the ST modality name in the top-level config. The ST spots become the output grid; every other modality produces a feature vector interpolated to each ST spot location.

```yaml
reference_modality: visium
```

### ST as a non-reference modality

When another spot-based modality (MSI or another ST instance) is the reference, ST is registered onto it using `spot_interpolation` or `spot_aggregation`. FOCUS maps the ST spot centres into the reference's coordinate frame through the alignment transform and averages (or sums) the ST expression profiles whose centres lie within each anchor footprint.

!!! note "Image-based reference"
    An image-based modality (`microscopy_image`, `raman`) may be set as the reference with ST as a target: preprocessing, alignment and registration all run. The final MuData compilation is skipped, because it requires the reference to be spot-based (`msi` or `st`). Make ST or MSI the reference if you need the compiled multimodal dataset.

---

## Choosing the Reference Modality

| Scenario | Recommended reference |
|----------|-----------------------|
| Spot-based modalities only (ST + MSI) | The spot modality with the lowest spatial resolution (coarser grid); the other registers via `spot_interpolation`. |
| Visium-centric analysis (no high-res image) | Spatial transcriptomics. Other spot modalities register via `spot_interpolation`; use `none` if ST stands alone. |
| Dataset also includes Raman or microscopy | A spot-based modality (ST or MSI). Image-based modalities then register as **targets** (`raman_pixel_interpolation` for Raman; `feature_extraction` for microscopy, but only when it is an H&E brightfield section, otherwise `none`). An image-based reference is accepted and will align and register, but MuData compilation is skipped. |

The reference modality defines the output coordinate space and the number of observation slots in the final multimodal dataset.

---

## Registration

| Registration type | Supported | Notes |
|-------------------|-----------|-------|
| `spot_interpolation` | Yes | ST registers onto a reference via Gaussian-weighted average of the ST spots in each anchor footprint |
| `spot_aggregation` | Yes | ST registers onto a reference by **summing** (not averaging) the ST spots in each anchor footprint. Used for subcellular-resolution data (e.g. Visium HD). When the merged ST file is normalized, the raw counts are summed and the merged registered file is normalized once with the same method |
| `none` | Yes | No registration; ST coordinates are used as-is (valid when ST is the reference) |
| `feature_extraction` | No | Not compatible with spot-based modalities |

```yaml
# ST as non-reference, registering onto another spot modality (MSI)
registration_type: spot_interpolation

# ST as reference (no registration needed)
registration_type: none
```

---

## Output

### Per-sample AnnData

Path: `<sample_id>/preprocessing/<modality_name>/<modality_name>_<sample_id>_processed.h5ad`

| Slot | Content |
|------|---------|
| `.X` | Raw counts of the retained spots and genes, sparse CSR |
| `.obs_names` | Spot names, prefixed `<sample_id>_` |
| `.var_names` | Gene names (unique), controls removed |
| `.var['mt']` | Boolean mitochondrial flag |
| `.obsm['spatial']` | Spot coordinates in µm, `float32 (N_spots, 2)` |
| `.obs['sample_id']` | Sample identifier (categorical) |
| `.obs['cluster']` | Per-spot labels (each spot carries its clustering unit's label), including `unassigned` |
| `.obs['clustering_eligible']` | Eligibility for the clustering analysis |
| `.obs['focus_area']` | Area used by area normalization (absent for cells without an area column) |
| `.obs` QC | `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt`, `total_counts_control`, `pct_counts_control` and `log1p_` variants |
| `.var` QC | `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and `log1p_` variants |
| `.uns['focus_qc']` | Outlier rule and spots removed per reason |
| `.uns['focus_clustering']` | Clustering parameters, units, selected features and diagnostics |
| `.uns['cluster_colors']` | Colors in categorical cluster order |
| `.uns['focus_preprocessing_fingerprint']` | Cache fingerprint |
| `.uns['focus_input_signature']` | Input stat signature and SHA-256 (JSON) |
| `.uns['spot_size']` | `float32` array `[x_µm, y_µm]` |

### Merged dataset

Path: `merged/preprocessing/<modality_name>_merged_processed.h5ad`

Same slots as above, with these differences:

- `.var_names` is the union panel restricted to the technology's gene rule.
- `.X` is normalized (`float32`) and `.layers['raw']` holds the counts, unless `normalization: none`.
- `.uns['focus_normalization']` records the method and its scale (`median_library_size`, `median_area` or `target_sum`).
- `.uns['spot_size']` is a dictionary `{sample_id: [x_µm, y_µm]}`.

---

## Config Example

=== "Visium (reference)"

    ```yaml
    reference_modality: visium

    modalities:
      - name: visium
        type: st
        processing_settings:
          capture: sequencing
          unit: spot
        registration_type: none
    ```

=== "Visium HD bins (non-reference)"

    ```yaml
    reference_modality: lipidomics

    modalities:
      - name: lipidomics
        type: msi
        processing_settings:
          mass_tolerance: 10
          intensity_normalization: tic
        registration_type: none

      - name: visium_hd
        type: st
        processing_settings:
          capture: sequencing
          unit: bin
        registration_type: spot_aggregation
    ```

=== "Xenium cells"

    ```yaml
    modalities:
      - name: xenium
        type: st
        processing_settings:
          capture: imaging
          unit: cell
          normalization: auto
        registration_type: spot_interpolation
    ```
