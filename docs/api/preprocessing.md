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

`dict[str, str]` — maps each `sample_id` to its output file path. Also includes a `"merged"` key for modalities that produce a concatenated multi-sample file.

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

On construction, `MicroscopyImage` scans `<source_path>/<sample_id>/<modality_name>/` for a supported image file. Supported extensions (in priority order): `.ome.tiff`, `.ome.tif`, `.tiff`, `.tif`, `.czi`.

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
    gaussian_blur_kernel_size: int = 251,
    min_object_size: int = 500,
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
| `remove_background` | `bool` | `True` | Remove background using Otsu thresholding on a blurred grayscale image. |
| `crop_to_tissue` | `bool` | `True` | Crop the image to the tissue bounding box. |
| `background_color` | `SegmentationBackgroundColor` | `WHITE` | Fill color for background regions (`"white"` or `"black"`). |
| `min_object_coverage` | `float` | `0.01` | Minimum tissue area as fraction of total image area (0–1). |
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists. |
| `gaussian_blur_kernel_size` | `int` | `251` | Kernel size for Gaussian blur used in background detection. Must be odd. |
| `min_object_size` | `int` | `500` | Minimum connected component size in pixels to retain. |
| `clip_percentile` | `int` | `99` | Percentile for intensity clipping before thresholding. |
| `crop_margin` | `int` | `250` | Pixel margin around the tissue bounding box. |
| `gamma` | `float` | `0.45` | Gamma value for correction (< 1 brightens, > 1 darkens). |
| `contrast_saturation` | `float` | `0.35` | Percentage of pixels to saturate during contrast stretching. |

The number of OME-TIFF pyramid levels is not a parameter — it is computed automatically from the image dimensions (smallest level kept within 3,000 × 3,000 px).

Returns `str` — path to the output OME-TIFF.

**`preview_image()`**

```python
def preview_image(self) -> np.ndarray:
    ...
```

Loads and returns the raw image as a float32 `(H, W, C)` array for inspection without running preprocessing.

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
        gaussian_blur_kernel_size: int = 251,
        min_object_size: int = 500,
        clip_percentile: int = 99,
        crop_margin: int = 250,
        gamma: float = 0.45,
        contrast_saturation: float = 0.35,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

All parameters are forwarded verbatim to each sample's `process_image()` call. Returns a `dict[str, str]` mapping sample IDs to output OME-TIFF paths. No merged output is produced for image modalities.

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
| `double_ion_mode` | `bool` | If `True`, both positive and negative ion modes are expected in `pos/` and `neg/` subdirectories. |
| `ion_mode` | `MsiIonMode` or `None` | Required when `double_ion_mode=False`. One of `"pos"` or `"neg"`. |

**Key properties**

- `ion_modes` — list of `MsiIonMode` values available in this sample
- `foreground_mask` — boolean array indicating tissue vs. background spots
- `recalibration_reference` — reference m/z vector(s) for mass recalibration
- `min_intensity_threshold` — intensity threshold for recalibration

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
        intensity_normalization: MsiIntensityNormalization = MsiIntensityNormalization.TIC,
        recalibration_reference: dict | None = None,
        min_intensity_threshold: float = 10000.0,
        detect_background: bool = True,
        sample_type: str = MsiSampleType.TISSUE,  # "tissue"
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lipid_annotation_db` | `str` or `None` | `None` | Path to a CSV or JSON lipid annotation database. Required columns: `db_name`, `ionized_mass`, `ion_mode`. |
| `mass_tolerance` | `int` | `10` | Adaptive mass tolerance in ppm for m/z consensus clustering. |
| `frequency_threshold` | `float` | `0.01` | Minimum fraction of spectra in which an m/z must appear to be included in the reference grid. |
| `intensity_normalization` | `MsiIntensityNormalization` | `"tic"` | Normalization method (applied per ion mode): `"tic"`, `"log"`, `"clr"`, `"global_scaling"`, or `"none"`. `"tic"` makes each spectrum sum to 1; `"global_scaling"` rescales each spectrum to the mean total ion current, preserving absolute scale. |
| `recalibration_reference` | `dict` or `None` | `None` | Per-ion-mode reference m/z vectors for recalibration. |
| `min_intensity_threshold` | `float` | `10000.0` | Minimum peak intensity for recalibration peak selection. |
| `detect_background` | `bool` | `True` | If `True`, GMM-based tissue/background classification is run. |
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

The pipeline executed by `RamanDataset.process_dataset()` calls these methods in order:

1. `load_source()` — parse LIF file, extract tiles and metadata
2. `basic_correct()` — BaSiC illumination correction (via `FOCUS_BaSiCpy` conda environment)
3. `remove_background()` — Otsu segmentation on a quick-stitched PCA mosaic
4. `process_raw_tiles()` — despike, Savitzky-Golay denoise, IASLS baseline, min-max normalize (RamanSPy pipeline, parallelized)
5. `ashlar_stitch()` — tile stitching into final mosaic (via `FOCUS_ASHLAR` conda environment)

**Key properties**

- `raw` — raw tiles `(T, C, Y, X)` float32
- `corrected` — BaSiC + background + spectrally cleaned tiles
- `mosaic` — final stitched mosaic `(C, Y, X)`
- `metadata` — `RamanMetadata` object
- `wavenumbers` — wavenumber array `(W,)` float32

---

### `RamanMetadata`

Stores metadata extracted from a Leica LIF file (scan dimensions, wavelength range, pixel size, tile coordinates). All fields are validated on assignment.

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
| `force_recomputing` | `bool` | `False` | Re-run even if output already exists. |
| `max_workers` | `int` | `8` | Parallel workers for BaSiC and spectral cleaning. |
| `savgol_window` | `int` | `7` | Savitzky-Golay filter window length. |
| `savgol_polyorder` | `int` | `3` | Savitzky-Golay filter polynomial order. |
| `bg_min_area_fraction` | `float` | `0.05` | Minimum contour area as fraction of total image area for background removal. |
| `otsu_threshold_factor` | `float` | `0.7` | Multiplicative factor applied to the Otsu threshold. |
| `min_object_size` | `int` | `500` | Minimum connected component size in pixels for morphological cleanup. |

Returns `dict[str, str]` mapping sample IDs to output OME-TIFF paths. No merged file is produced for Raman.

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
) -> str:
    ...
```

Pipeline: load → flag mito genes → spot filtering → QC metrics → optional mito-gene removal → store raw counts in `.layers["raw"]` → optional normalize/log1p → Leiden clustering (on an internal normalized copy) → save as gzip-compressed `.h5ad`.

Output AnnData structure:

- `.X` — counts (sparse CSR); raw unless `total_counts_normalize`/`log1p_transform` are set
- `.layers["raw"]` — filtered, post-feature-selection raw counts
- `.obs["sample_id"]` — categorical sample identifier
- `.obs["leiden"]` — per-sample Leiden cluster labels (PCA/neighbour intermediates are not persisted)
- `.obs` / `.var` QC metrics from `calculate_qc_metrics` (`pct_counts_mt`, `n_genes_by_counts`, `n_cells_by_counts`, ...)
- `.obsm["spatial"]` — float32 spatial coordinates
- `.uns["spot_size"]` — float32 array of shape `(2,)`

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

1. Preprocess each sample individually (spot filtering, optional mito-gene removal, per-sample Leiden)
2. Concatenate using raw counts from `.layers["raw"]` (outer join, missing genes filled with 0)
3. Cross-sample gene filtering (`min_spots_per_gene`, `min_count_spots_ratio_per_gene`)
4. Recompute QC metrics on the merged matrix
5. Optionally normalize / log1p-transform the merged dataset (opt-in)
6. Preserve per-sample Leiden labels
7. Build `.uns["spot_size"]` as `{sample_id: [float, float]}`
8. Save as gzip-compressed `.h5ad`

Additional parameters beyond per-sample ones:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_spots_per_gene` | `float` or `None` | `None` | Minimum fraction of spots per sample expressing a gene (0–1). Genes failing in most samples are removed. |
| `min_count_spots_ratio_per_gene` | `float` or `None` | `None` | Minimum ratio of total counts to expressed spots per gene. |

(`remove_mitochondrial_genes` is shared with the per-sample method and applied per sample.)

Returns `dict[str, str]` with sample IDs and a `"merged"` key.

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
