# Preprocessing API

All preprocessing classes live in `focus.preprocessing`. The top-level dispatch function `preprocess_modality` is the recommended programmatic entry point when you want to preprocess a single modality without writing a full config file.

---

## `preprocess_modality()`

Top-level dispatch function. Discovers all samples under `path`, instantiates the correct dataset class for the given modality type, and runs `process_dataset`.

```python
def preprocess_modality(
    path: str,
    modality_name: str,
    modality_type: str,
    preprocessing_settings: dict,
    step_reporter=None,
) -> dict[str, str]:
    ...
```

**Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `str` | Root path of the dataset directory. Must contain per-sample subdirectories. |
| `modality_name` | `str` | Name of the modality to process. Must match the subdirectory name inside each sample folder. |
| `modality_type` | `str` | One of `"microscopy_image"`, `"msi"`, `"raman"`, `"st"`. |
| `preprocessing_settings` | `dict` | Modality-specific settings dictionary. Keys mirror the config `processing_settings` block. |
| `step_reporter` | `StepReporter` or `None` | Optional progress reporter for GUI integration. |

**Returns**

`dict[str, str]`, mapping each `sample_id` to its output file path. Also includes a `"merged"` key for modalities that produce a concatenated multi-sample file.

**Example**

```python
from focus.preprocessing import preprocess_modality

results = preprocess_modality(
    path="/data/experiment_01",
    modality_name="microscopy_image",
    modality_type="microscopy_image",
    preprocessing_settings={
        "image_type": "brightfield",
        "z_projection": "auto",
        "crop_to_tissue": False,
        "remove_background": False,
        "force_recomputing": False,
    },
)
# results == {"sample_001": "/data/.../microscopy_image_sample_001_processed.ome.tiff", ...}
```

---

## `SampleSource`

```python
class SampleSource:
    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None: ...
```

Shared path initialization for modality-specific sample processors. Handles path validation and output directory creation. Subclasses implement the modality-specific processing logic.

See [API index](index.md#samplesource) for attribute descriptions.

---

## `DatasetState`

```python
class DatasetState(Generic[SampleT]):
    def __init__(self, path: str, samples: list[SampleT]) -> None: ...

    @staticmethod
    def _check_cache(output_path: str, force_recomputing: bool) -> bool: ...
```

Typed path and sample collection for modality-specific dataset processors. Concrete subclasses iterate over `self.samples`, call per-sample methods, then merge outputs.

---

## `MicroscopyImage` / `MicroscopyImageDataset`

### `MicroscopyImage`

Writes one microscopy acquisition as a lossless, tiled OME-TIFF pyramid: the native pixels (sample type and every channel), the acquisition metadata, the tissue outline and the display windows. See [Microscopy Images](../modalities/microscopy.md) and [Microscopy Preprocessing Methods](../scientific/microscopy_methods.md).

```python
class MicroscopyImage(SampleSource):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
    ) -> None: ...
```

On construction, `MicroscopyImage` resolves the single supported image file of `<source_path>/<sample_id>/<modality_name>/` (`.ome.tiff`, `.ome.tif`, `.ome.btf`, `.qptiff`, `.tiff`, `.tif`, `.btf`, `.svs`, `.ndpi`, `.scn`, `.bif`, `.czi`, `.nd2`, matched case-insensitively; hidden files are ignored). With no match it raises `FileNotFoundError`, and with several matches `ValueError`, at construction time, before any sample is processed.

**`process_image()`**

```python
def process_image(
    self,
    settings: ImageSettings,
    force_recomputing: bool = False,
) -> str:
    ...
```

`settings` is a `focus.preprocessing.microscopy.parameters.ImageSettings(image_type, z_projection, crop_to_tissue, remove_background, pixel_size)` (see the `process_dataset()` parameters below). Steps: reading image, resolving image type, detecting tissue, cropping to tissue or removing background (only when either is enabled), writing OME-TIFF.

Returns `str`, the path to the output OME-TIFF. When the output already carries the current fingerprint and `force_recomputing=False`, it is returned without reading the source pixels; the source SHA-256 stored in the output is reused while the source path, size and modification time are unchanged.

---

### `MicroscopyImageDataset`

```python
class MicroscopyImageDataset(DatasetState):
    def __init__(self, path: str, samples: list[MicroscopyImage]) -> None: ...

    def process_dataset(
        self,
        image_type: str = "auto",
        z_projection: str = "auto",
        crop_to_tissue: bool = False,
        remove_background: bool = False,
        pixel_size: float | None = None,
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image_type` | `str` | `"auto"` | `"auto"` (from the metadata), `"brightfield"` or `"fluorescence"`; selects the tissue detection, the `auto` Z projection, the fill, the display rendering and the codec. |
| `z_projection` | `str` | `"auto"` | `"auto"`, `"max"`, `"best_focus"` or `"middle"`. |
| `crop_to_tissue` | `bool` | `False` | Crop to the tissue bounding box plus 100 µm. |
| `remove_background` | `bool` | `False` | Set pixels outside the tissue to the fill of the image type (white for brightfield, 0 for fluorescence). |
| `pixel_size` | `float` or `None` | `None` | µm per pixel, used only when the file metadata has none. |
| `force_recomputing` | `bool` | `False` | Re-run even if the output carries the current fingerprint. |

The same `ImageSettings` is passed to each sample's `process_image()`; `step_reporter` is attached to each sample (the running pipeline's reporter, `get_reporter()`, is used when omitted). The constructor rejects any element of `samples` that is not a `MicroscopyImage` with `ValueError`.

Exceptions raised while processing a sample are caught: the warning `Sample skipped: <error>` is reported, that sample is omitted from the result, and the loop continues. The call itself does not raise.

The values are not validated here: configuration validation (`validate_microscopy_settings`) rejects unknown `image_type` or `z_projection` values, a non-positive `pixel_size`, the settings removed in `microscopy-3` and `image_type="fluorescence"` with `feature_extraction`. `preprocess_modality()` reads only the current keys, so a removed key passed to it directly is ignored.

Returns a `dict[str, str]` mapping sample IDs to output OME-TIFF paths. No merged output is produced for image modalities.

**Example**

```python
from focus.preprocessing import MicroscopyImage, MicroscopyImageDataset

sample_a = MicroscopyImage(
    source_path="/data/experiment_01",
    sample_id="sample_001",
    modality_name="microscopy_image",
)
sample_b = MicroscopyImage(
    source_path="/data/experiment_01",
    sample_id="sample_002",
    modality_name="microscopy_image",
)

dataset = MicroscopyImageDataset(
    path="/data/experiment_01",
    samples=[sample_a, sample_b],
)

results = dataset.process_dataset(
    image_type="fluorescence",
    z_projection="max",
    crop_to_tissue=True,
)
# results == {
#   "sample_001": ".../microscopy_image_sample_001_processed.ome.tiff",
#   "sample_002": ".../microscopy_image_sample_002_processed.ome.tiff",
# }
```

---

## `MsiSample` / `MsiDataset`

### `MsiSample`

Processes a single Mass Spectrometry Imaging sample from paired `.imzML` / `.ibd` files.

```python
class MsiSample(SampleSource):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
        double_ion_mode: bool = False,
        ion_mode: MsiIonMode | None = None,
    ) -> None: ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_path` | `str` | Root dataset path. |
| `sample_id` | `str` | Sample identifier. |
| `modality_name` | `str` | Modality name. |
| `double_ion_mode` | `bool` | If `True`, both `pos/` and `neg/` must each hold a complete `.imzML` + `.ibd` pair. |
| `ion_mode` | `MsiIonMode` or `None` | Required when `double_ion_mode=False`. One of `"pos"` or `"neg"`. |

When samples are built by the pipeline rather than constructed directly, these two arguments are inferred per sample: an ion mode counts as acquired when its subdirectory holds a complete `.imzML` + `.ibd` pair, and a subdirectory holding neither file is ignored. Constructing `MsiSample` yourself with `double_ion_mode=True` is stricter. It asserts both ion modes are present, so an empty subdirectory raises `FileNotFoundError`.

**Key properties**

- `ion_modes`: list of `MsiIonMode` values available in this sample
- `foreground_mask`: boolean array indicating tissue vs. background spots
- `recalibration_reference`: reference m/z per ion mode for mass recalibration
- `recalibration_model`: per-ion-mode `MsiRecalibration` (per-spectrum ppm offset and slope), set on the first `load_payload` with references

---

### `MsiDataset`

```python
class MsiDataset(DatasetState):
    def __init__(
        self,
        path: str,
        samples: list[MsiSample],
        lipid_annotation_db: str | None = None,
    ) -> None: ...

    def process_dataset(
        self,
        mass_tolerance: float = 10,
        intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.NONE,
        recalibration_reference: dict | str | None = None,
        internal_standard_mz: dict | str | None = None,
        sample_type: str = MsiSampleType.TISSUE,  # "tissue"
        resolution: str = SpotResolution.STANDARD,  # "standard"
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database. Required columns: `db_name`, `ionized_mass`, `ion_mode`. |
| `mass_tolerance` | `float` | `10` | Mass tolerance in ppm (int or float): feature detection, backbone merge span, binning reach and annotation window. |
| `intensity_normalization` | `MsiIntensityNormalization` | `"none"` | Row normalization at a fixed scale, per sample and ion mode: `"none"`, `"tic"` (rows sum to the number of features), `"median"` (median of the nonzero features is 1), `"rms"` (root mean square is 1) or `"internal_standard"` (standard intensity is 1). |
| `recalibration_reference` | `dict`, `str` or `None` | `None` | Per-ion-mode reference m/z values for recalibration (`{"pos": [...]}`, arrays accepted, or `"pos: 760.585; neg: 885.550"`). Selected from the dataset when `None`. |
| `internal_standard_mz` | `dict`, `str` or `None` | `None` | m/z of sprayed internal standards per ion mode (same forms). Always used as recalibration references; required by `"internal_standard"`. |
| `sample_type` | `str` | `"tissue"` | `"tissue"`, `"tissue_noisy"`, `"cell_culture"` or `"microgrid"`. Calibrates foreground detection, which always runs ([MSI methods §5](../scientific/msi_methods.md#5-foreground-detection)). |
| `resolution` | `str` | `"standard"` | Clustering preset for the alignment colours: `"standard"` or `"sub_cellular"`. |
| `force_recomputing` | `bool` | `False` | Re-run even if output with the current fingerprint (inputs, settings, method version) already exists. |

Returns `dict[str, str]` with keys for each sample ID plus `"merged"`.

**Example**

```python
from focus.preprocessing import MsiSample, MsiDataset
from focus.config.options import MsiIonMode

samples = [
    MsiSample(
        source_path="/data/experiment_01",
        sample_id="sample_001",
        modality_name="msi",
        double_ion_mode=True,
    ),
]

dataset = MsiDataset(
    path="/data/experiment_01",
    samples=samples,
    lipid_annotation_db="/data/lipids.csv",
)

results = dataset.process_dataset(
    mass_tolerance=10,
    intensity_normalization="tic",
    sample_type="tissue",
    force_recomputing=False,
)
```

---

## `RamanImage` / `RamanDataset`

`RamanImage` owns source data and exposes three narrow components. Call illumination and background operations through `sample.correction`, spectral processing through `sample.tiles`, and stitching through `sample.stitcher`. The signatures below describe those component methods.

### `RamanImage`

Processes a single Raman Spectroscopy Imaging sample from a Leica `.lif` file.

```python
class RamanImage(SampleSource):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
        max_workers: int = 8,
    ) -> None: ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_path` | `str` | Root dataset path. |
| `sample_id` | `str` | Sample identifier. |
| `modality_name` | `str` | Modality name. |
| `max_workers` | `int` | Maximum parallel workers for BaSiC correction and spectral cleaning. Default `8`. |

Input files are read from `<source_path>/<sample_id>/<modality_name>/`; outputs and intermediate caches are written to `<source_path>/<sample_id>/preprocessing/<modality_name>/`, which the constructor creates.

**Pipeline methods**, called in this order by `RamanDataset.process_dataset()`:

```python
def load_source(self) -> None: ...
def basic_correct(self, force_recomputing: bool = False) -> None: ...
def remove_background(
    self,
    force_recomputing: bool = False,
    bg_min_area_fraction: float = 0.05,
    otsu_threshold_factor: float = 0.7,
    min_object_size: int = 500,
) -> None: ...
def process_raw_tiles(
    self,
    wavenumbers: np.ndarray = None,
    parallel: bool = True,
    force_recomputing: bool = False,
    savgol_window: int = 7,
    savgol_polyorder: int = 3,
) -> None: ...
def ashlar_stitch(self, force_recomputing: bool = False) -> str: ...
```

1. `load_source()` loads the first `.lif` file in the input directory (`FileNotFoundError` if there is none): tile pixel data, tile stage coordinates, pixel size, spectral axis, and the per-scan channel ranges. Not cached.
2. `sample.correction.basic_correct()`: BaSiC illumination correction per spectral channel, in the `FOCUS_BaSiCpy` conda environment, followed by a global min-max normalization to `[0, 1]`. Cached as `basic_corrected_tiles.npy`. Raises `RuntimeError` when `conda` or the environment is missing.
3. `sample.correction.remove_background()`: Otsu segmentation on a quick-stitched PCA mosaic, back-projected to zero background pixels in the tiles. Cached as `segmented_tiles.npy`. Raises `RuntimeError` when `basic_correct()` has not run in this session, even if the cache exists.
4. `sample.tiles.process_raw_tiles()`: RamanSPy pipeline (Whitaker-Hayes despike, Savitzky-Golay denoise, IASLS baseline, per-spectrum min-max) over one work unit per tile and per spectral scan. `wavenumbers=None` uses the axis read in step 1; `parallel=False` runs the same per-unit function sequentially. Cached as `raman_corrected_tiles.npy`.
5. `sample.stitcher.ashlar_stitch()` writes one ASHLAR cycle file per spectral scan, runs `tools/ASHLAR/main.py` in the `FOCUS_ASHLAR` conda environment, and renames the result to the final output path, which it returns. Reuses an existing output file unless `force_recomputing=True`.

Each cached step loads its `.npy` instead of recomputing when the file exists and `force_recomputing` is `False`.

**Key properties**

- `raw`: tiles as loaded and intensity-scaled, `(T, C, Y, X)` float32
- `corrected`: spectrally cleaned tiles, same shape; `None` until `process_raw_tiles()` has run
- `mosaic`: final stitched mosaic `(C, Y, X)`, read back from the output OME-TIFF
- `metadata`: `RamanMetadata` describing the merged stack
- `wavenumbers`: wavenumber array `(C,)` float32, in cm⁻¹
- `tiles_coordinates`: tile stage coordinates in µm, `(T, N_scans, 2)` float32

---

### `RamanMetadata`

Container for the metadata of one LIF image element (scan dimensions, wavelength range, pixel size, tile coordinates). Every field is a property that validates on assignment and raises `TypeError` on a wrong type or `ValueError` on an out-of-range value; all start as `None`.

```python
class RamanMetadata:
    name: str
    index: int
    lambda_steps: int
    lambda_begin: float
    lambda_end: float
    scan_height: int
    scan_width: int
    laser_type: str
    lambda_stokes: float
    tile_number: int
    tiles_coordinates: np.ndarray  # shape (N, 2), float32
    pixel_size: np.ndarray         # shape (2,), float32
```

`RamanImage.metadata` is not one of the parsed per-element objects: it is a summary built after merging. Its `tile_number` and `lambda_steps` are the merged tile array's first two axes, and its `scan_height` and `scan_width` are its last two. These two are swapped relative to the per-element metadata, because the per-element arrays are allocated as `(tiles, channels, scan_width, scan_height)`. Its `pixel_size` is the mean over the merged scans.

---

### `RamanDataset`

```python
class RamanDataset(DatasetState):
    def __init__(self, path: str, samples: list[RamanImage]) -> None: ...

    def process_dataset(
        self,
        force_recomputing: bool = False,
        max_workers: int = 8,
        savgol_window: int = 7,
        savgol_polyorder: int = 3,
        bg_min_area_fraction: float = 0.05,
        otsu_threshold_factor: float = 0.7,
        min_object_size: int = 500,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force_recomputing` | `bool` | `False` | Re-run every step even if the output or an intermediate cache already exists. |
| `max_workers` | `int` | `8` | Threads for BaSiC channels; `joblib` workers for spectral cleaning. Assigned onto every sample before it is processed, overriding the value passed to `RamanImage`. |
| `savgol_window` | `int` | `7` | Savitzky-Golay filter window length. |
| `savgol_polyorder` | `int` | `3` | Savitzky-Golay filter polynomial order. |
| `bg_min_area_fraction` | `float` | `0.05` | Minimum contour area as fraction of total image area for background removal. |
| `otsu_threshold_factor` | `float` | `0.7` | Multiplicative factor applied to the Otsu threshold. |
| `min_object_size` | `int` | `500` | Connected components of this many pixels or fewer are removed from the tissue mask. |
| `step_reporter` | `StepReporter` or `None` | `None` | Progress sink; the running pipeline's reporter (`get_reporter()`) is used when omitted. |

Samples are processed one at a time. A sample whose output OME-TIFF already exists is skipped entirely (unless `force_recomputing=True`) and its existing path is returned. After a sample finishes, its three `.npy` caches are deleted.

Exceptions raised while processing a sample are caught: `Error processing sample <sample_id>: <error>` is printed to the console, that sample is omitted from the result, and the loop continues. The call itself does not raise.

Returns `dict[str, str]` mapping sample IDs to output OME-TIFF paths. No merged file is produced for Raman, so the result has no `"merged"` key.

**Example**

```python
from focus.preprocessing import RamanImage, RamanDataset

samples = [
    RamanImage(
        source_path="/data/experiment_01",
        sample_id="sample_001",
        modality_name="raman",
        max_workers=8,
    ),
]

dataset = RamanDataset(path="/data/experiment_01", samples=samples)
results = dataset.process_dataset(
    savgol_window=7,
    savgol_polyorder=3,
    force_recomputing=False,
)
```

---

## `SpatialTranscriptomic` / `SpatialTranscriptomicDataset`

### `SpatialTranscriptomic`

Preprocesses a single spatial transcriptomics sample. Accepts any technology (Visium, Xenium, MERFISH, Slide-seq, etc.) as long as the input is an `.h5ad` file with raw gene counts in `.X` and spatial coordinates in `.obsm["spatial"]`.

```python
class SpatialTranscriptomic(SampleSource):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
    ) -> None: ...
```

**`load_data()`**

```python
def load_data(self, reporter=None) -> anndata.AnnData:
    ...
```

Loads the single `.h5ad` file of the sample directory (several raise `ValueError`). Requires finite `.obsm["spatial"]` of shape `(N, 2)` and `.uns["spot_size"]` (normalized to a float32 `(2,)` array; missing raises `ValueError`). The raw counts are the first non-negative integer matrix among `.X`, `.layers["counts"]`, `.layers["raw"]` and `.raw.X`; every other input matrix is dropped. Duplicated gene names are made unique. Returns the `AnnData` with raw counts in a CSR `.X`.

**`preprocess_data()`**

```python
def preprocess_data(
    self,
    capture: str = "sequencing",
    unit: str = "spot",
    quality_control: bool = True,
    remove_controls_and_empty: bool = True,
    remove_mitochondrial_genes: bool = False,
    force_recomputing: bool = False,
    step_reporter=None,
) -> str:
    ...
```

Reuses a cached file only when its fingerprint (input SHA-256, settings, technology constants, method and software versions) matches and `force_recomputing` is false. The input SHA-256 is reused while the input file's path, size and modification time are unchanged.

Pipeline: load → control features recorded and removed (`remove_controls_and_empty`) → flag mito genes in `.var["mt"]` → quality control (out-of-tissue spots; zero-count spots when `remove_controls_and_empty`; the unit's outlier rule when `quality_control`) → QC metrics → `obs["focus_area"]` → optional mito-gene removal → prefix `.obs_names` with `<sample_id>_` → cluster labels → atomic gzip-compressed `.h5ad` with raw counts.

Output AnnData structure:

- `.X`: raw counts, sparse CSR (no layers)
- `.obs_names`: spot names prefixed `<sample_id>_`
- `.var["mt"]`: boolean mitochondrial flag
- `.obs["sample_id"]`: categorical sample identifier
- `.obs["cluster"]`: categorical per-spot labels (each spot carries its clustering unit's label), including `unassigned`
- `.obs['clustering_eligible']`: clustering-only eligibility, with no observation deletion
- `.obs["focus_area"]`: area used by area normalization
- `.obs` QC: `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt`, `total_counts_control`, `pct_counts_control` and `log1p_` variants
- `.var` QC: `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants
- `.uns['focus_qc']`: outlier rule and spots removed per reason
- `.uns['focus_clustering']`: method/settings/software, clustering units (`units`), selected genes, cluster sizes and PCA/count-depth diagnostics
- `.uns['cluster_colors']`: colors in categorical cluster order
- `.uns['focus_preprocessing_fingerprint']`, `.uns['focus_input_signature']`: cache stamps
- `.obsm["spatial"]`: float32 spatial coordinates
- `.uns["spot_size"]`: float32 array of shape `(2,)`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `capture` | `str` | `"sequencing"` | `"sequencing"` or `"imaging"` (`StCapture`) |
| `unit` | `str` | `"spot"` | `"spot"`, `"bin"` or `"cell"` (`StUnit`); selects the outlier rule and the clustering preset |
| `quality_control` | `bool` | `True` | Remove the outlier spots of the unit's rule |
| `remove_controls_and_empty` | `bool` | `True` | Remove the control features and the zero-count spots; `False` keeps both |
| `remove_mitochondrial_genes` | `bool` | `False` | Drop mitochondrial genes (`MT-`/`MT.` prefix) from the feature set |
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists |

---

### `SpatialTranscriptomicDataset`

```python
class SpatialTranscriptomicDataset(DatasetState):
    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None: ...

    def process_dataset(
        self,
        capture: str = "sequencing",
        unit: str = "spot",
        normalization: str = "auto",
        quality_control: bool = True,
        remove_controls_and_empty: bool = True,
        remove_mitochondrial_genes: bool = False,
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

Dataset-level pipeline:

1. Preprocess each sample via `preprocess_data()`
2. Return the cached merged file when `force_recomputing` is `False`, no sample was rebuilt in this run, and its fingerprint (sample fingerprints, normalization, gene rule) matches
3. Read each per-sample `spot_size` and `var["n_cells_by_counts"]` with a backed read
4. Concatenate on disk (`anndata.concat_on_disk`, `join="outer"`, `fill_value=0`) and keep the genes detected in at least 20 (`sequencing`) or 1 (`imaging`) spots of at least one sample
5. Recompute library-size and gene QC on the final panel
6. Normalize once with `normalization` (`auto` resolves by `capture`), counts in `.layers["raw"]`, float32 `.X`, method and scale in `.uns["focus_normalization"]`
7. Keep the per-sample `.obs["cluster"]` labels; build `.uns["spot_size"]` as `{sample_id: [float, float]}`
8. Save atomically as gzip-compressed sparse CSR `.h5ad`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `normalization` | `str` | `"auto"` | `"auto"`, `"none"`, `"log_normalize"`, `"cp10k_log"`, `"size_factors"` or `"area_log_normalize"` (`StNormalization`) |

The other parameters are those of `preprocess_data()`. Returns `dict[str, str]` with sample IDs and a `"merged"` key.

**Example**

```python
from focus.preprocessing import SpatialTranscriptomic, SpatialTranscriptomicDataset

samples = [
    SpatialTranscriptomic(
        source_path="/data/experiment_01",
        sample_id="sample_001",
        modality_name="st",
    ),
    SpatialTranscriptomic(
        source_path="/data/experiment_01",
        sample_id="sample_002",
        modality_name="st",
    ),
]

dataset = SpatialTranscriptomicDataset(path="/data/experiment_01", samples=samples)

results = dataset.process_dataset(capture="sequencing", unit="bin", normalization="auto")
# results == {
#   "sample_001": ".../st_sample_001_processed.h5ad",
#   "sample_002": ".../st_sample_002_processed.h5ad",
#   "merged":     ".../merged/preprocessing/st_merged_processed.h5ad",
# }
```

See [Spatial Transcriptomics](../modalities/transcriptomics.md#technology-calibration) for the technology calibration. Clustering uses at most 2,000 highly variable features (`N_TOP_FEATURES`) on an analytical copy and never filters exported expression features.
