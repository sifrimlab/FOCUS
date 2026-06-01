import os
import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

from focus.constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED, STPreprocessingParams
from focus.utils import write_h5ad_compat, read_merged_sample_ids
from focus.preprocessing._utils import StepReporter
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality


class SpatialTranscriptomic(BaseSample):
    '''
    Class for handling spot-based spatial transcriptomic data.
    Supports any technology (Visium, Xenium, MERFISH, etc.) as long as
    the input is an AnnData file with raw gene counts in .X and
    spatial coordinates in .obsm["spatial"].
    '''

    _LEIDEN_RESOLUTION = 0.5
    _NORMALIZE_TARGET_SUM = 1e4
    _DEFAULT_SPOT_SIZE = np.array([1.0, 1.0], dtype=np.float32)
    _H5AD_COMPRESSION = "gzip"

    def __init__(
        self,
        source_path: str,
        sample_id: str,
        modality_name: str
    ) -> None:
        super().__init__(source_path, sample_id, modality_name)
        self.input_path = os.path.join(source_path, sample_id, modality_name)

    def load_data(self) -> ad.AnnData:
        '''
        Load spatial transcriptomic data from the source path.
        The first .h5ad file found in the sample directory is loaded.
        Validates that .obsm["spatial"] exists and normalizes .uns["spot_size"].

        Returns
        -------
        adata : ad.AnnData
            The loaded and validated AnnData object.
        '''

        adata = None
        for file in os.listdir(self.input_path):
            if file.endswith('.h5ad'):
                adata = sc.read_h5ad(os.path.join(self.input_path, file))
                break

        if adata is None:
            raise FileNotFoundError(f"No .h5ad file found in {self.input_path}")

        # Validate spatial coordinates
        if "spatial" not in adata.obsm:
            raise ValueError(
                f"Sample {self.sample_id}: AnnData is missing .obsm['spatial']. "
                "Spatial coordinates are required for spatial transcriptomics preprocessing."
            )
        adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)

        # Normalize spot_size to a float32 array of shape (2,)
        adata.uns["spot_size"] = self._normalize_spot_size(adata.uns.get("spot_size", None))

        # Ensure .X is sparse CSR for memory efficiency
        if not sp.issparse(adata.X):
            adata.X = sp.csr_matrix(adata.X)

        return adata

    @staticmethod
    def _normalize_spot_size(spot_size) -> np.ndarray:
        '''
        Normalize spot_size to a float32 array of shape (2,).
        - None → [1.0, 1.0]
        - scalar → [val, val]
        - array of 1 → [val, val]
        - array of 2 → as-is
        '''
        if spot_size is None:
            return np.array([1.0, 1.0], dtype=np.float32)

        if isinstance(spot_size, (int, float)):
            return np.array([float(spot_size), float(spot_size)], dtype=np.float32)

        arr = np.asarray(spot_size, dtype=np.float32).flatten()
        if arr.size == 1:
            return np.array([float(arr[0]), float(arr[0])], dtype=np.float32)
        elif arr.size == 2:
            return arr.astype(np.float32)
        else:
            raise ValueError(
                f"spot_size must be a scalar or array of 1-2 values, got shape {arr.shape}"
            )

    def preprocess_data(self,
        min_count_per_spot: int | None = None,
        max_count_per_spot: int | None = None,
        min_genes_per_spot: int | None = None,
        max_genes_per_spot: int | None = None,
        remove_mitochondrial_genes: bool = False,
        total_counts_normalize: bool = False,
        log1p_transform: bool = False,
        force_recomputing: bool = False
        ) -> str:
        '''
        Preprocess a single spatial transcriptomic sample.

        Pipeline:
        1. Load and validate data
        2. Flag mitochondrial genes (.var["mt"])
        3. Filter spots by count/gene thresholds (opt-in)
        4. Compute QC metrics (scanpy.pp.calculate_qc_metrics) on the retained spots
        5. Optionally remove mitochondrial genes (opt-in, uses the .var["mt"] QC flag)
        6. Store filtered raw counts in .layers["raw"]
        7. Normalize .X (total counts + log1p, both opt-in)
        8. Leiden clustering on an internal normalized representation → .obs["leiden"]
           (clustering intermediates such as PCA / neighbour graph are not persisted)
        9. Save (gzip-compressed)

        Output AnnData structure:
        - .X: counts (raw unless total_counts_normalize / log1p_transform are set; sparse CSR)
        - .layers["raw"]: filtered, post-feature-selection raw counts (sparse CSR)
        - .obs["sample_id"]: categorical sample identifier
        - .obs["leiden"]: categorical cluster labels (used for alignment colouring)
        - .obs QC metrics (total_counts, n_genes_by_counts, pct_counts_mt, ...) from
          scanpy.pp.calculate_qc_metrics
        - .obsm["spatial"]: float32 spatial coordinates
        - .uns["spot_size"]: float32 array of shape (2,)

        Parameters
        ----------
        min_count_per_spot : int | None
            Minimum total counts per spot to retain.
        max_count_per_spot : int | None
            Maximum total counts per spot to retain.
        min_genes_per_spot : int | None
            Minimum genes detected per spot to retain.
        max_genes_per_spot : int | None
            Maximum genes detected per spot to retain.
        remove_mitochondrial_genes : bool
            When True, drop mitochondrial genes (flagged in .var["mt"] by an
            ``MT-``/``MT.`` name prefix, case-insensitive) from the feature set.
            Off by default: in spatial data a high mitochondrial fraction can be a
            genuine biological signal rather than a low-quality artefact.
        total_counts_normalize : bool
            Whether to normalize total counts per spot.
        log1p_transform : bool
            Whether to apply log1p transformation.
        force_recomputing : bool
            Whether to force recomputing even if output exists.

        Returns
        -------
        output_file : str
            Path to the preprocessed AnnData file.
        '''

        output_file = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, "h5ad")

        if not force_recomputing and os.path.exists(output_file):
            print(f"Sample {self.sample_id} already preprocessed. Using cached results.")
            return output_file

        # Validate filter parameters
        if min_count_per_spot is not None and min_count_per_spot <= 0:
            raise ValueError("min_count_per_spot must be greater than 0.")
        if max_count_per_spot is not None and max_count_per_spot <= 0:
            raise ValueError("max_count_per_spot must be greater than 0.")
        if min_genes_per_spot is not None and min_genes_per_spot <= 0:
            raise ValueError("min_genes_per_spot must be greater than 0.")
        if max_genes_per_spot is not None and max_genes_per_spot <= 0:
            raise ValueError("max_genes_per_spot must be greater than 0.")

        # 1. Load and validate
        adata = self.load_data()

        # 2. Flag mitochondrial genes (case-insensitive 'MT-'/'MT.' prefix).
        # Note: name-based detection; datasets keyed by Ensembl IDs won't be flagged.
        adata.var['mt'] = adata.var_names.str.upper().str.match(r'^MT[-\.]')

        # 3. Filter spots (all thresholds opt-in)
        if min_count_per_spot is not None:
            sc.pp.filter_cells(adata, min_counts=min_count_per_spot)
        if max_count_per_spot is not None:
            sc.pp.filter_cells(adata, max_counts=max_count_per_spot)
        if min_genes_per_spot is not None:
            sc.pp.filter_cells(adata, min_genes=min_genes_per_spot)
        if max_genes_per_spot is not None:
            sc.pp.filter_cells(adata, max_genes=max_genes_per_spot)

        # 4. QC metrics on the retained spots, with mitochondrial genes still present
        # so pct_counts_mt is meaningful. percent_top=None keeps the output lean.
        sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, inplace=True)

        # 5. Optional QC-based gene filter: drop mitochondrial genes (opt-in).
        if remove_mitochondrial_genes:
            adata = adata[:, ~adata.var['mt'].to_numpy()].copy()

        # Make observation names unique across samples
        if not all(obs_name.startswith(f"{self.sample_id}_") for obs_name in adata.obs_names):
            adata.obs_names = [f"{self.sample_id}_{obs_name}" for obs_name in adata.obs_names]

        # 6. Store filtered, post-feature-selection raw counts (FOCUS convention).
        adata.layers['raw'] = adata.X.copy()

        # 7. Per-sample Leiden labels for alignment colouring. Computed on an internal
        # normalized representation so labels are meaningful regardless of the output
        # normalization flags; PCA / neighbour-graph intermediates are not persisted.
        leiden_labels = self._compute_leiden_labels(adata)

        # 8. Normalize the output .X (both steps opt-in; defaults leave .X as raw counts)
        if total_counts_normalize:
            sc.pp.normalize_total(adata, target_sum=self._NORMALIZE_TARGET_SUM, inplace=True)
        if log1p_transform:
            sc.pp.log1p(adata)

        # 9. Set metadata
        adata.obs['sample_id'] = pd.Categorical([self.sample_id] * adata.n_obs)
        adata.obs['leiden'] = pd.Categorical(leiden_labels)
        adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)

        # Save with compression
        write_h5ad_compat(adata, output_file, compression=self._H5AD_COMPRESSION)
        return output_file

    def _compute_leiden_labels(self, adata: ad.AnnData) -> np.ndarray:
        '''
        Compute per-sample Leiden cluster labels for alignment-stage spot colouring.

        Clustering runs on a throwaway, internally normalized + log1p copy of the raw
        counts, so the labels are meaningful even when the output .X is left as raw
        counts (normalization is opt-in). Only the labels are returned — the PCA
        embedding and neighbour graph are discarded and never written to disk.

        Returns a length-n_obs array of string cluster labels (all '0' when the sample
        is too small to cluster).
        '''
        n_obs = adata.n_obs
        n_pcs = min(50, n_obs - 1, adata.n_vars - 1)
        if n_obs < 2 or n_pcs < 2:
            return np.array(['0'] * n_obs, dtype=object)

        counts = adata.layers['raw'] if 'raw' in adata.layers else adata.X
        tmp = ad.AnnData(X=counts.copy())
        sc.pp.normalize_total(tmp, target_sum=self._NORMALIZE_TARGET_SUM, inplace=True)
        sc.pp.log1p(tmp)
        sc.pp.pca(tmp, n_comps=n_pcs)
        sc.pp.neighbors(tmp)
        sc.tl.leiden(tmp, resolution=self._LEIDEN_RESOLUTION, key_added='leiden',
                     flavor='igraph', n_iterations=2, directed=False)
        return tmp.obs['leiden'].to_numpy()


class SpatialTranscriptomicDataset(BaseDataset):
    '''
    Class for handling multiple spatial transcriptomic samples.
    Processes each sample individually, then merges into a combined dataset
    with cross-sample gene filtering and unified normalization.
    '''

    _NUM_SAMPLES_FILTER = 0.05
    _NORMALIZE_TARGET_SUM = 1e4
    _H5AD_COMPRESSION = "gzip"

    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None:
        super().__init__(path, samples)

    def process_dataset(self,
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
        step_reporter=None
    ) -> dict[str, str]:
        '''
        Process and combine multiple spatial transcriptomic samples.

        Pipeline:
        1. Preprocess each sample individually (spot filtering, optional mito-gene
           removal, per-sample Leiden)
        2. Concatenate using raw counts from .layers["raw"]
        3. Cross-sample gene filtering
        4. Recompute QC metrics on the merged matrix (so .obs/.var QC are accurate)
        5. Store .layers["raw"] (post-gene-filter, pre-normalization)
        6. Normalize .X on the merged data (opt-in)
        7. Preserve per-sample Leiden labels (batch-effect-free, for per-sample visualization)
        8. Build .uns["spot_size"] = {sample_id: [float32, float32]}
        9. Save (gzip-compressed)

        Parameters
        ----------
        min_count_per_spot : int | None
            Minimum total counts per spot to retain.
        max_count_per_spot : int | None
            Maximum total counts per spot to retain.
        min_genes_per_spot : int | None
            Minimum genes detected per spot to retain.
        max_genes_per_spot : int | None
            Maximum genes detected per spot to retain.
        min_spots_per_gene : float | None
            Minimum fraction of spots per sample expressing a gene to retain it (0-1).
        min_count_spots_ratio_per_gene : float | None
            Minimum ratio of total counts to expressed spots per gene.
        remove_mitochondrial_genes : bool
            When True, drop mitochondrial genes (flagged by an ``MT-``/``MT.`` name
            prefix) per sample before merging. Off by default.
        total_counts_normalize : bool
            Whether to normalize total counts per spot.
        log1p_transform : bool
            Whether to apply log1p transformation.
        force_recomputing : bool
            Whether to force recomputing even if output exists.

        Returns
        -------
        processed_samples : dict[str, str]
            Maps sample IDs (and "merged") to output file paths.
        '''

        # Validate parameters
        if min_count_per_spot is not None and min_count_per_spot <= 0:
            raise ValueError("min_count_per_spot must be greater than 0.")
        if max_count_per_spot is not None and max_count_per_spot <= 0:
            raise ValueError("max_count_per_spot must be greater than 0.")
        if min_genes_per_spot is not None and min_genes_per_spot <= 0:
            raise ValueError("min_genes_per_spot must be greater than 0.")
        if max_genes_per_spot is not None and max_genes_per_spot <= 0:
            raise ValueError("max_genes_per_spot must be greater than 0.")
        if min_spots_per_gene is not None and not (0 < min_spots_per_gene < 1):
            raise ValueError("min_spots_per_gene must be between 0 and 1.")
        if min_count_spots_ratio_per_gene is not None and min_count_spots_ratio_per_gene <= 0:
            raise ValueError("min_count_spots_ratio_per_gene must be greater than 0.")

        reporter = step_reporter or StepReporter()

        # ---- Step 1: Preprocess each sample (cache-aware) ----
        reporter.step("1/2 - Processing Spatial Transcriptomic Samples")

        processed_samples: dict[str, str] = {}

        total_samples = len(self.samples)
        for i, sample in enumerate(self.samples):
            reporter.set_sample(sample.sample_id, i + 1, total_samples)

            processed_samples[sample.sample_id] = sample.preprocess_data(
                min_count_per_spot=min_count_per_spot,
                max_count_per_spot=max_count_per_spot,
                min_genes_per_spot=min_genes_per_spot,
                max_genes_per_spot=max_genes_per_spot,
                remove_mitochondrial_genes=remove_mitochondrial_genes,
                total_counts_normalize=total_counts_normalize,
                log1p_transform=log1p_transform,
                force_recomputing=force_recomputing
            )

        # ---- Step 2: Merge and cross-sample processing ----
        reporter.step("2/2 - Generating combined Spatial Transcriptomic dataset")

        merged_file = MODALITY_PREPROCESSING_MERGED(self.dataset_source_path, self.samples[0].modality_name, "h5ad")

        # Cache check before loading per-sample files into memory
        # Also validate that the merged file contains exactly the active samples.
        if not force_recomputing and os.path.exists(merged_file):
            active_ids = {s.sample_id for s in self.samples}
            merged_ids = read_merged_sample_ids(merged_file)
            if merged_ids == active_ids:
                reporter.message("Combined dataset already exists. Using cached results.")
                processed_samples["merged"] = merged_file
                return processed_samples

        # Load per-sample files into memory only when the merged file needs to be built
        adata_list: list[ad.AnnData] = []
        spot_sizes: dict[str, np.ndarray] = {}
        for sample in self.samples:
            adata = sc.read_h5ad(processed_samples[sample.sample_id])
            spot_sizes[sample.sample_id] = adata.uns.get("spot_size", SpatialTranscriptomic._DEFAULT_SPOT_SIZE.copy())
            # Revert .X to raw counts for proper cross-sample normalization
            adata.X = adata.layers['raw']
            del adata.layers['raw']
            adata_list.append(adata)

        reporter.message(f"Concatenating {len(self.samples)} samples...")
        # Use outer join to preserve genes when panels differ across samples.
        # Missing genes are filled with 0 counts.
        combined = ad.concat(adata_list, join="outer", fill_value=0, merge='same')
        del adata_list  # Free per-sample objects

        # Ensure .X is sparse CSR for efficient row-based filter operations.
        combined.X = combined.X.tocsr() if sp.issparse(combined.X) else sp.csr_matrix(combined.X)

        # ---- Cross-sample gene filtering ----
        reporter.message(f"{combined.n_vars} genes before cross-sample filtering")

        if min_spots_per_gene is not None:
            reporter.message(f"Filtering genes by expression frequency (min_spots_per_gene={min_spots_per_gene})...")
            combined = self._filter_genes_by_expression_frequency(combined, min_spots_per_gene)
            reporter.message(f"{combined.n_vars} genes after min_spots_per_gene={min_spots_per_gene}")

        if min_count_spots_ratio_per_gene is not None:
            reporter.message(f"Filtering genes by count/spot ratio (min_count_spots_ratio_per_gene={min_count_spots_ratio_per_gene})...")
            combined = self._filter_genes_by_count_ratio(combined, min_count_spots_ratio_per_gene)
            reporter.message(f"{combined.n_vars} genes after min_count_spots_ratio_per_gene={min_count_spots_ratio_per_gene}")

        # Recompute QC metrics on the final merged matrix (raw counts) so .obs/.var QC
        # reflect the retained spots and genes; per-sample QC predates cross-sample filtering.
        combined.var['mt'] = combined.var_names.str.upper().str.match(r'^MT[-\.]')
        sc.pp.calculate_qc_metrics(combined, qc_vars=['mt'], percent_top=None, inplace=True)

        # Store raw counts after gene filtering (preserves sparse format)
        combined.layers['raw'] = combined.X.copy()

        # ---- Normalize merged dataset ----
        if total_counts_normalize:
            reporter.message("Normalizing total counts...")
            sc.pp.normalize_total(combined, target_sum=self._NORMALIZE_TARGET_SUM, inplace=True)
        if log1p_transform:
            reporter.message("Applying log1p transformation...")
            sc.pp.log1p(combined)

        # Per-sample Leiden labels are already in .obs["leiden"] from concatenation
        # (each sample was clustered independently to avoid batch effects)

        # ---- Set output metadata ----
        combined.obs['sample_id'] = pd.Categorical(combined.obs['sample_id'])
        combined.obs['leiden'] = pd.Categorical(combined.obs['leiden'])
        combined.obsm["spatial"] = np.asarray(combined.obsm["spatial"], dtype=np.float32)
        combined.uns["spot_size"] = {sid: ss.tolist() for sid, ss in spot_sizes.items()}

        # Save with compression
        reporter.message("Saving combined dataset...")
        write_h5ad_compat(combined, merged_file, compression=self._H5AD_COMPRESSION)
        processed_samples["merged"] = merged_file
        return processed_samples

    def _filter_genes_by_expression_frequency(self, adata: ad.AnnData, min_spots_per_gene: float) -> ad.AnnData:
        '''
        Filter genes that are not expressed in at least min_spots_per_gene fraction of spots
        in a sufficient number of samples (>= _NUM_SAMPLES_FILTER fraction of total samples).
        Operates on sparse matrices without densification.
        '''
        sample_ids = adata.obs['sample_id'].unique()
        num_samples = len(sample_ids)
        gene_preserved_counts = np.zeros(adata.n_vars, dtype=int)

        for sid in sample_ids:
            sample_mask = (adata.obs['sample_id'] == sid).values
            X_sample = adata.X[sample_mask, :]
            # ceil ensures min_cells >= 1 when min_spots_per_gene > 0,
            # so genes with zero expression always fail this threshold.
            min_cells = max(1, int(np.ceil(X_sample.shape[0] * min_spots_per_gene)))
            expressed_per_gene = np.asarray((X_sample > 0).sum(axis=0)).flatten()
            gene_preserved_counts += (expressed_per_gene >= min_cells).astype(int)

        min_samples_required = np.ceil(self._NUM_SAMPLES_FILTER * num_samples)
        return adata[:, gene_preserved_counts >= min_samples_required].copy()

    def _filter_genes_by_count_ratio(self, adata: ad.AnnData, min_count_spots_ratio: float) -> ad.AnnData:
        '''
        Filter genes where the ratio of total counts to expressed spots is below the threshold,
        in a sufficient number of samples where the gene is actually expressed.
        Genes absent from a sample are not counted (neither for nor against).
        Operates on sparse matrices without densification.
        '''
        sample_ids = adata.obs['sample_id'].unique()
        num_samples = len(sample_ids)
        gene_preserved_counts = np.zeros(adata.n_vars, dtype=int)

        for sid in sample_ids:
            sample_mask = (adata.obs['sample_id'] == sid).values
            X_sample = adata.X[sample_mask, :]
            expressed_counts = np.asarray((X_sample > 0).sum(axis=0)).flatten()
            total_counts = np.asarray(X_sample.sum(axis=0)).flatten()
            # Only evaluate the ratio where the gene is actually expressed in this sample.
            # Unexpressed genes (expressed_counts == 0) are neutral: they neither pass
            # nor fail the ratio test, so they don't inflate gene_preserved_counts.
            expressed_mask = expressed_counts > 0
            ratio_ok = np.zeros(adata.n_vars, dtype=bool)
            ratio_ok[expressed_mask] = (
                total_counts[expressed_mask] >= min_count_spots_ratio * expressed_counts[expressed_mask]
            )
            gene_preserved_counts += ratio_ok.astype(int)

        min_samples_required = np.ceil(self._NUM_SAMPLES_FILTER * num_samples)
        return adata[:, gene_preserved_counts >= min_samples_required].copy()


# --- Modality Registration ---

def _create_st_samples(path, sample_ids, modality_name, settings):
    return [
        SpatialTranscriptomic(source_path=path, sample_id=sid, modality_name=modality_name)
        for sid in sample_ids
    ]

def _create_st_dataset(path, samples, settings):
    return SpatialTranscriptomicDataset(path=path, samples=samples)

def _extract_st_settings(settings):
    return {
        'min_count_per_spot': settings.get(STPreprocessingParams.MIN_COUNT_PER_SPOT, None),
        'max_count_per_spot': settings.get(STPreprocessingParams.MAX_COUNT_PER_SPOT, None),
        'min_genes_per_spot': settings.get(STPreprocessingParams.MIN_GENES_PER_SPOT, None),
        'max_genes_per_spot': settings.get(STPreprocessingParams.MAX_GENES_PER_SPOT, None),
        'min_spots_per_gene': settings.get(STPreprocessingParams.MIN_SPOTS_PER_GENE, None),
        'min_count_spots_ratio_per_gene': settings.get(STPreprocessingParams.MIN_COUNT_SPOTS_RATIO_PER_GENE, None),
        'remove_mitochondrial_genes': settings.get(STPreprocessingParams.REMOVE_MITOCHONDRIAL_GENES, False),
        'total_counts_normalize': settings.get(STPreprocessingParams.TOTAL_COUNTS_NORMALIZE, False),
        'log1p_transform': settings.get(STPreprocessingParams.LOG1P_TRANSFORM, False),
        'force_recomputing': settings.get(STPreprocessingParams.FORCE_RECOMPUTING, False),
    }

register_modality('st', ModalityHandler(
    create_samples=_create_st_samples,
    create_dataset=_create_st_dataset,
    extract_settings=_extract_st_settings,
))
