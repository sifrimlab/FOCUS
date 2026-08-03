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
        "color_enhancement": True,
        "gamma": 0.45,
        "remove_background": True,
        "crop_to_tissue": True,
        "force_recomputing": False,
    },
)
# results == {"sample_001": "/data/.../microscopy_image_sample_001_processed.ome.tiff", ...}
```

---

## `BaseSample`

```python
class BaseSample(ABC):
    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None: ...
```

Abstract base class for all modality-specific sample processors. Handles path validation and output directory creation. Subclasses implement the modality-specific processing logic.

See [API index](index.md#basesample) for attribute descriptions.

---

## `BaseDataset`

```python
class BaseDataset(ABC):
    def __init__(self, path: str, samples: list) -> None: ...

    @abstractmethod
    def process_dataset(self, **kwargs) -> dict[str, str]: ...

    @staticmethod
    def _check_cache(output_path: str, force_recomputing: bool) -> bool: ...
```

Abstract base class for all modality-specific dataset processors. Concrete subclasses iterate over `self.samples`, call per-sample methods, then merge outputs.

---

## `MicroscopyImage` / `MicroscopyImageDataset`

### `MicroscopyImage`

Processes a single microscopy image to a uniform multi-resolution OME-TIFF ready for alignment.

```python
class MicroscopyImage(BaseSample):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
    ) -> None: ...
```

On construction, `MicroscopyImage` scans `<source_path>/<sample_id>/<modality_name>/` for a supported image file and keeps the first match for the highest-priority extension present (`.ome.tiff`, `.ome.tif`, `.qptiff`, `.tiff`, `.tif`, `.czi`, matched case-insensitively). With no match it raises `FileNotFoundError` at construction time, before any sample is processed.

**`process_image()`**

```python
def process_image(
    self,
    color_enhancement: bool = True,
    remove_background: bool = True,
    crop_to_tissue: bool = True,
    background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
    min_object_coverage: float = 0.01,
    force_recomputing: bool = False,
    clip_percentile: int = 99,
    crop_margin: int = 250,
    gamma: float = 0.45,
    contrast_saturation: float = 0.35,
) -> str:
    ...
```

Pipeline: load → gamma correction + contrast stretching → background removal (Otsu) → tissue crop → save as multi-resolution OME-TIFF.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `color_enhancement` | `bool` | `True` | Apply gamma correction and contrast stretching. |
| `remove_background` | `bool` | `True` | Fill non-tissue pixels, using an Otsu mask detected on a downsampled proxy. |
| `crop_to_tissue` | `bool` | `True` | Crop the image to the tissue bounding box. Uses the same mask, so it triggers detection even with `remove_background=False`. |
| `background_color` | `SegmentationBackgroundColor` | `WHITE` | Fill color for background regions (`"white"` or `"black"`; anything else raises `ValueError`). |
| `min_object_coverage` | `float` | `0.01` | Minimum tissue contour area, as a fraction of the detection-proxy area. |
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists. |
| `clip_percentile` | `int` | `99` | Percentile at which the inverted grayscale is clipped before the blur and Otsu threshold. |
| `crop_margin` | `int` | `250` | Pixel margin around the tissue bounding box. |
| `gamma` | `float` | `0.45` | Gamma value for correction (< 1 brightens, > 1 darkens). |
| `contrast_saturation` | `float` | `0.35` | Percentage of non-zero pixels saturated at each end of the histogram. |

The number of OME-TIFF pyramid levels is not a parameter. It is computed automatically from the final image dimensions (smallest level kept within 3,000 × 3,000 px). The Gaussian blur kernel size (25 px) and the speck-removal size (50 px) used during background detection are also not parameters: detection always runs on a downsampled proxy capped at 9 megapixels, so these are fixed internal constants expressed in proxy pixels rather than in the source image's native resolution.

Detection converts the proxy with `cv2.COLOR_RGB2GRAY`, so an image with fewer than 3 channels is promoted first: a single channel is replicated, and two channels gain a zero third channel. The promotion applies to the mask computation only, never to the stored pixel data. For those promoted images the background polarity is also probed (border median vs global median) and the grayscale inversion is skipped when the background is the darker class; 3-channel input always takes the bright-background path. `background_color` is independent of that probe, so a dark-background acquisition wants `background_color="black"`.

Returns `str`, the path to the output OME-TIFF. When the output already exists and `force_recomputing=False`, it is returned without reloading or reprocessing the source image.

**`preview_image()`**

```python
def preview_image(self) -> np.ndarray:
    ...
```

Loads and returns the image as a float32 `(H, W, C)` array in `[0, 1]`, normalised as in step 1 (channel axis moved last, at most 3 channels) but with no enhancement, background removal or cropping applied.

---

### `MicroscopyImageDataset`

```python
class MicroscopyImageDataset(BaseDataset):
    def __init__(self, path: str, samples: list[MicroscopyImage]) -> None: ...

    def process_dataset(
        self,
        color_enhancement: bool = True,
        remove_background: bool = True,
        crop_to_tissue: bool = True,
        background_color: SegmentationBackgroundColor = SegmentationBackgroundColor.WHITE,
        min_object_coverage: float = 0.01,
        force_recomputing: bool = False,
        clip_percentile: int = 99,
        crop_margin: int = 250,
        gamma: float = 0.45,
        contrast_saturation: float = 0.35,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

All parameters are forwarded verbatim to each sample's `process_image()` call; `step_reporter` is attached to each sample instead (a default `StepReporter` is created when omitted). The constructor rejects any element of `samples` that is not a `MicroscopyImage` with `ValueError`.

Exceptions raised while processing a sample are caught: `Error processing sample <sample_id>: <error>` is printed to the console, that sample is omitted from the result, and the loop continues. The call itself does not raise.

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
    color_enhancement=True,
    gamma=0.45,
    remove_background=True,
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
class MsiSample(BaseSample):
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
- `recalibration_reference`: reference m/z vector(s) for mass recalibration
- `min_intensity_threshold`: intensity threshold for recalibration

---

### `MsiDataset`

```python
class MsiDataset(BaseDataset):
    def __init__(
        self,
        path: str,
        samples: list[MsiSample],
        lipid_annotation_db: str | None = None,
    ) -> None: ...

    def process_dataset(
        self,
        mass_tolerance: int = 10,
        frequency_threshold: float = 0.01,
        intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.NONE,
        recalibration_reference: dict | None = None,
        min_intensity_threshold: float = 10000.0,
        detect_background: bool = False,
        sample_type: str = MsiSampleType.TISSUE,  # "tissue"
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database. Required columns: `db_name`, `ionized_mass`, `ion_mode`. |
| `mass_tolerance` | `int` | `10` | Adaptive mass tolerance in ppm for m/z consensus clustering. Must be a Python `int`. A float raises `ValueError`. |
| `frequency_threshold` | `float` | `0.01` | Minimum fraction of spectra in which an m/z must appear to be included in the reference grid. |
| `intensity_normalization` | `MsiIntensityNormalization` | `"none"` | Normalization method (applied per sample and per ion mode): `"tic"`, `"log"`, `"clr"`, `"tic_mean_scaled"`, or `"none"`. `"tic"` makes each spectrum sum to 1; `"tic_mean_scaled"` rescales each spectrum to the mean total ion current over that sample's spots for that ion mode, preserving absolute scale (not comparable across samples). |
| `recalibration_reference` | `dict` or `None` | `None` | Per-ion-mode reference m/z vectors for recalibration. |
| `min_intensity_threshold` | `float` | `10000.0` | Minimum peak intensity for recalibration peak selection. |
| `detect_background` | `bool` | `False` | If `True` **and** `lipid_annotation_db` is set, GMM-based (or Otsu, for `microgrid`) tissue/background classification is run. Without a database the step is silently skipped and all spots are marked foreground. |
| `sample_type` | `str` | `"tissue"` | `"tissue"` or `"microgrid"`. Affects background detection strategy. |
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists. |

Returns `dict[str, str]` with keys for each sample ID plus `"merged"`.

**Example**

```python
from focus.preprocessing import MsiSample, MsiDataset
from focus.constants import MsiIonMode

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
    frequency_threshold=0.01,
    intensity_normalization="tic",
    detect_background=True,
    force_recomputing=False,
)
```

---

## `RamanImage` / `RamanDataset`

### `RamanImage`

Processes a single Raman Spectroscopy Imaging sample from a Leica `.lif` file.

```python
class RamanImage(BaseSample):
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
2. `basic_correct()`: BaSiC illumination correction per spectral channel, in the `FOCUS_BaSiCpy` conda environment, followed by a global min-max normalization to `[0, 1]`. Cached as `basic_corrected_tiles.npy`. Raises `RuntimeError` when `conda` or the environment is missing.
3. `remove_background()`: Otsu segmentation on a quick-stitched PCA mosaic, back-projected to zero background pixels in the tiles. Cached as `segmented_tiles.npy`. Raises `RuntimeError` when `basic_correct()` has not run in this session, even if the cache exists.
4. `process_raw_tiles()`: RamanSPy pipeline (Whitaker-Hayes despike, Savitzky-Golay denoise, IASLS baseline, per-spectrum min-max) over one work unit per tile and per spectral scan. `wavenumbers=None` uses the axis read in step 1; `parallel=False` runs the same per-unit function sequentially. Cached as `raman_corrected_tiles.npy`.
5. `ashlar_stitch()` writes one ASHLAR cycle file per spectral scan, runs `tools/ASHLAR/main.py` in the `FOCUS_ASHLAR` conda environment, and renames the result to the final output path, which it returns. Reuses an existing output file unless `force_recomputing=True`.

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
class RamanDataset(BaseDataset):
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
| `step_reporter` | `StepReporter` or `None` | `None` | Progress sink; a default `StepReporter` is created when omitted. |

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
class SpatialTranscriptomic(BaseSample):
    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str,
    ) -> None: ...
```

**`load_data()`**

```python
def load_data(self) -> anndata.AnnData:
    ...
```

Loads the first `.h5ad` file found in the sample directory. Validates that `.obsm["spatial"]` is present and normalizes `.uns["spot_size"]` to a float32 `(2,)` array. Returns the loaded `AnnData`.

**`preprocess_data()`**

```python
def preprocess_data(
    self,
    min_count_per_spot: int | None = None,
    max_count_per_spot: int | None = None,
    min_genes_per_spot: int | None = None,
    max_genes_per_spot: int | None = None,
    remove_mitochondrial_genes: bool = False,
    total_counts_normalize: bool = False,
    log1p_transform: bool = False,
    force_recomputing: bool = False,
    step_reporter=None,
) -> str:
    ...
```

Returns the output path immediately if that file exists and `force_recomputing` is `False`.

Pipeline: load → flag mito genes in `.var["mt"]` → spot filtering → QC metrics → optional mito-gene removal → prefix `.obs_names` with `<sample_id>_` → cluster labels → store `.layers["raw"]` (only if normalizing) → optional normalize/log1p → save as gzip-compressed `.h5ad`.

Output AnnData structure:

- `.X`: sparse CSR; raw counts unless `total_counts_normalize`/`log1p_transform` are set
- `.layers["raw"]`: raw counts before normalization; **present only when `.X` was normalized**
- `.obs_names`: spot names prefixed `<sample_id>_`
- `.var["mt"]`: boolean mitochondrial flag
- `.obs["sample_id"]`: categorical sample identifier
- `.obs["cluster"]`: categorical per-sample cluster labels (the coarsened matrix, PCA embedding and neighbour graph are not persisted)
- `.obs` QC: `total_counts`, `n_genes_by_counts`, `total_counts_mt`, `pct_counts_mt` and their `log1p_` variants
- `.var` QC: `n_cells_by_counts`, `mean_counts`, `total_counts`, `pct_dropout_by_counts` and their `log1p_` variants
- `.obsm["spatial"]`: float32 spatial coordinates
- `.uns["spot_size"]`: float32 array of shape `(2,)`

Cluster labels are computed on `.X` before normalization, so they always read raw counts. `calculate_qc_metrics` runs before the optional mito-gene removal, so `pct_counts_mt` describes the matrix prior to removal.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_count_per_spot` | `int` or `None` | `None` | Minimum total counts per spot to retain. |
| `max_count_per_spot` | `int` or `None` | `None` | Maximum total counts per spot to retain. |
| `min_genes_per_spot` | `int` or `None` | `None` | Minimum genes detected per spot to retain. |
| `max_genes_per_spot` | `int` or `None` | `None` | Maximum genes detected per spot to retain. |
| `remove_mitochondrial_genes` | `bool` | `False` | Drop mitochondrial genes (`MT-`/`MT.` prefix) from the feature set. |
| `total_counts_normalize` | `bool` | `False` | Normalize counts to 10,000 per spot. |
| `log1p_transform` | `bool` | `False` | Apply log1p transformation after normalization. |
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists. |

All filtering/normalization steps are opt-in; the method-signature defaults match the
config extractor defaults, so direct calls and config-driven runs behave identically.

---

### `SpatialTranscriptomicDataset`

```python
class SpatialTranscriptomicDataset(BaseDataset):
    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None: ...

    def process_dataset(
        self,
        min_count_per_spot: int | None = None,
        max_count_per_spot: int | None = None,
        min_genes_per_spot: int | None = None,
        max_genes_per_spot: int | None = None,
        min_spots_per_gene: float | None = None,
        min_count_spots_ratio_per_gene: float | None = None,
        remove_mitochondrial_genes: bool = False,
        total_counts_normalize: bool = False,
        log1p_transform: bool = False,
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

Dataset-level pipeline:

1. Preprocess each sample individually via `preprocess_data()` (the two gene-level thresholds are **not** forwarded; they are dataset-level only)
2. Return the cached merged file if it exists, `force_recomputing` is `False`, and its `.obs["sample_id"]` set equals the active sample set
3. Read each per-sample `.uns["spot_size"]` with a backed read (`.X` never materialized)
4. Concatenate on disk (`anndata.concat_on_disk`, `axis=0`, `join="outer"`, `fill_value=0`); `.uns` is dropped by the concat
5. Recover raw counts: `.X = .layers.pop("raw", .X)`, so the layer is used when the per-sample files were normalized and `.X` otherwise
6. Cross-sample gene filtering, skipped entirely when both thresholds are `None`
7. Recompute `.var["mt"]` and QC metrics on the merged raw matrix
8. Store `.layers["raw"]` (only if normalizing), then optionally normalize / log1p-transform
9. Keep the per-sample `.obs["cluster"]` labels carried through the concat
10. Build `.uns["spot_size"]` as `{sample_id: [float, float]}`
11. Save as gzip-compressed sparse CSR `.h5ad`

Additional parameters beyond per-sample ones:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_spots_per_gene` | `float` or `None` | `None` | Minimum fraction of a sample's spots expressing a gene for that sample to count as passing. Must satisfy `0 < value < 1`. |
| `min_count_spots_ratio_per_gene` | `float` or `None` | `None` | Minimum ratio of a gene's total counts to the number of spots expressing it, per sample. Must be `> 0`. |

Both are evaluated per sample; a gene is retained when it passes in **at least one** sample, and when both are set it must satisfy each in at least one sample (not necessarily the same one). For the ratio criterion, samples where the gene is unexpressed count as neither pass nor fail.

(`remove_mitochondrial_genes` is shared with the per-sample method and applied per sample, before merging.)

Returns `dict[str, str]` with sample IDs and a `"merged"` key. The merged file differs from the per-sample files in that `.uns["spot_size"]` is a dict keyed by `sample_id`.

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

results = dataset.process_dataset(
    min_count_per_spot=200,
    min_genes_per_spot=100,
    min_spots_per_gene=0.05,
    total_counts_normalize=True,
    log1p_transform=True,
    force_recomputing=False,
)
# results == {
#   "sample_001": ".../st_sample_001_processed.h5ad",
#   "sample_002": ".../st_sample_002_processed.h5ad",
#   "merged":     ".../merged/preprocessing/st_merged_processed.h5ad",
# }
```
