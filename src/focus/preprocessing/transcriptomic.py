import os
import gc
import anndata as ad
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

from focus.constants import MODALITY_PREPROCESSING, MODALITY_PREPROCESSING_MERGED, STPreprocessingParams
from focus.utils import write_h5ad_compat, read_merged_sample_ids, concat_on_disk_compat
from focus.preprocessing._utils import StepReporter, compute_cluster_labels
from focus.preprocessing.base import BaseSample, BaseDataset
from focus.preprocessing._registry import ModalityHandler, register_modality


def _ensure_sparse_csr(adata: ad.AnnData) -> None:
    '''
    Guarantee .X and every layer are stored as CSR sparse matrices, in place and
    without changing dtype. No-op (no copy) for elements already in CSR format.

    This is the defensive guarantee that preprocessed spatial-transcriptomics output
    is always written as a sparse matrix, regardless of upstream scanpy operations or
    the sparse format (CSC/COO) of the input file.
    '''
    if not sp.issparse(adata.X):
        adata.X = sp.csr_matrix(adata.X)
    elif adata.X.format != "csr":
        adata.X = adata.X.tocsr()

    for key in list(adata.layers.keys()):
        layer = adata.layers[key]
        if not sp.issparse(layer):
            adata.layers[key] = sp.csr_matrix(layer)
        elif layer.format != "csr":
            adata.layers[key] = layer.tocsr()


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

        # Ensure .X is sparse CSR for memory efficiency (preserve dtype; convert CSC/COO too)
        if not sp.issparse(adata.X):
            adata.X = sp.csr_matrix(adata.X)
        elif adata.X.format != "csr":
            adata.X = adata.X.tocsr()

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
        force_recomputing: bool = False,
        step_reporter=None
        ) -> str:
        '''
        Preprocess a single spatial transcriptomic sample.

        Pipeline:
        1. Load and validate data
        2. Flag mitochondrial genes (.var["mt"])
        3. Filter spots by count/gene thresholds (opt-in)
        4. Compute QC metrics (scanpy.pp.calculate_qc_metrics) on the retained spots
        5. Optionally remove mitochondrial genes (opt-in, uses the .var["mt"] QC flag)
        6. Cluster labels (spatial-bin aggregation + Leiden) on an internal normalized
           representation → .obs["cluster"] (the aggregated matrix and clustering intermediates
           such as PCA / neighbour graph are not persisted; large samples are coarsened onto a
           spatial grid — spots in a bin summed into one pseudo-spot — to stay fast)
        7. Store filtered raw counts in .layers["raw"] (only when normalization is applied;
           otherwise .X already holds the raw counts, so the layer is omitted to avoid a duplicate)
        8. Normalize .X (total counts + log1p, both opt-in)
        9. Save (sparse CSR, gzip-compressed)

        Output AnnData structure:
        - .X: counts (raw unless total_counts_normalize / log1p_transform are set; sparse CSR)
        - .layers["raw"]: filtered, post-feature-selection raw counts (sparse CSR); present only
          when .X was normalized (when .X is left raw, it doubles as the raw counts)
        - .obs["sample_id"]: categorical sample identifier
        - .obs["cluster"]: categorical cluster labels (used for alignment colouring)
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
        step_reporter : StepReporter | None
            Unified reporter for console / log-file / GUI output. When None, a default
            StepReporter (no GUI callback) is used so the sample can be preprocessed
            standalone while still logging to the console / log file.

        Returns
        -------
        output_file : str
            Path to the preprocessed AnnData file.
        '''

        reporter = step_reporter or StepReporter()
        output_file = MODALITY_PREPROCESSING(self.source_path, self.sample_id, self.modality_name, "h5ad")

        if not force_recomputing and os.path.exists(output_file):
            reporter.message(f"Sample {self.sample_id} already preprocessed. Using cached results.")
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
        n_spots_total = adata.n_obs

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

        # Report how many spots survived the (opt-in) spot filters, on every interface.
        n_spots_retained = adata.n_obs
        pct_retained = (100.0 * n_spots_retained / n_spots_total) if n_spots_total else 0.0
        reporter.message(
            f"Sample {self.sample_id}: retained {n_spots_retained} / {n_spots_total} "
            f"spots ({pct_retained:.1f}%) after filtering"
        )

        # 4. QC metrics on the retained spots, with mitochondrial genes still present
        # so pct_counts_mt is meaningful. percent_top=None keeps the output lean.
        sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], percent_top=None, inplace=True)

        # 5. Optional QC-based gene filter: drop mitochondrial genes (opt-in).
        if remove_mitochondrial_genes:
            adata = adata[:, ~adata.var['mt'].to_numpy()].copy()

        # Make observation names unique across samples (vectorized; avoids per-spot Python loops)
        prefix = f"{self.sample_id}_"
        if not adata.obs_names.str.startswith(prefix).all():
            adata.obs_names = prefix + adata.obs_names.astype(str)

        # 6. Per-sample cluster labels for alignment colouring (spatial-bin aggregation + Leiden).
        # Computed on an internal normalized representation so labels are meaningful regardless
        # of the output normalization flags; the aggregated/normalized matrix and PCA / neighbour
        # intermediates are throwaway and never persisted. Runs before normalization so it reads
        # raw counts straight from .X. Large samples are coarsened onto a spatial grid (spots in a
        # bin summed into one pseudo-spot) to stay fast and signal-rich (see compute_cluster_labels).
        cluster_labels = compute_cluster_labels(
            adata.X,
            leiden_resolution=self._LEIDEN_RESOLUTION,
            normalize_target_sum=self._NORMALIZE_TARGET_SUM,
            coordinates=adata.obsm.get('spatial'),
        )

        # 7. Store filtered, post-feature-selection raw counts (FOCUS convention) ONLY when
        # normalization will modify .X. With no normalization (the default), .X already holds
        # raw counts, so a separate 'raw' layer would be a wasteful exact duplicate.
        will_normalize = total_counts_normalize or log1p_transform
        if will_normalize:
            adata.layers['raw'] = adata.X.copy()

        # 8. Normalize the output .X (both steps opt-in; defaults leave .X as raw counts)
        if total_counts_normalize:
            sc.pp.normalize_total(adata, target_sum=self._NORMALIZE_TARGET_SUM, inplace=True)
        if log1p_transform:
            sc.pp.log1p(adata)

        # 9. Set metadata
        adata.obs['sample_id'] = pd.Categorical([self.sample_id] * adata.n_obs)
        adata.obs['cluster'] = pd.Categorical(cluster_labels)
        adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)

        # Guarantee sparse CSR storage, then save with gzip compression
        _ensure_sparse_csr(adata)
        write_h5ad_compat(adata, output_file, compression=self._H5AD_COMPRESSION)
        return output_file


class SpatialTranscriptomicDataset(BaseDataset):
    '''
    Class for handling multiple spatial transcriptomic samples.
    Processes each sample individually, then merges into a combined dataset
    with cross-sample gene filtering and unified normalization.
    '''

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
           removal, per-sample cluster labels)
        2. Concatenate on disk (anndata.concat_on_disk, outer join) so all per-sample files
           are never held in RAM at once; recover raw counts from the carried 'raw' layer
           (present only when samples were normalized) or directly from .X otherwise
        3. Cross-sample gene filtering (single pass over per-sample submatrices)
        4. Recompute QC metrics on the merged matrix (so .obs/.var QC are accurate)
        5. Store .layers["raw"] (post-gene-filter, pre-normalization) only when .X is normalized
        6. Normalize .X on the merged data (opt-in)
        7. Preserve per-sample cluster labels (batch-effect-free, for per-sample visualization)
        8. Build .uns["spot_size"] = {sample_id: [float32, float32]}
        9. Save (sparse CSR, gzip-compressed)

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
                force_recomputing=force_recomputing,
                step_reporter=reporter
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

        # ---- Collect per-sample spot sizes via backed reads (no .X materialized) ----
        spot_sizes: dict[str, np.ndarray] = {}
        for sample in self.samples:
            a = sc.read_h5ad(processed_samples[sample.sample_id], backed='r')
            spot_sizes[sample.sample_id] = np.asarray(
                a.uns.get("spot_size", SpatialTranscriptomic._DEFAULT_SPOT_SIZE.copy()),
                dtype=np.float32,
            )
            if a.file is not None:
                a.file.close()

        reporter.message(f"Concatenating {len(self.samples)} samples...")
        # Memory-efficient on-disk concatenation: streams each per-sample file instead of
        # holding all of them plus the result in RAM simultaneously. Outer join preserves
        # genes when panels differ across samples; absent genes are filled with 0 counts.
        # uns is intentionally dropped (default uns_merge=None) so a per-sample 'log1p' flag
        # can't suppress the merged log1p below; spot_size is rebuilt from the backed reads.
        # The per-sample 'raw' layer (present only when those files were normalized) rides
        # along the concat and is recovered below.
        sample_files = {s.sample_id: processed_samples[s.sample_id] for s in self.samples}
        concat_on_disk_compat(
            sample_files,
            merged_file,
            axis=0,
            join="outer",
            fill_value=0,
            merge="same",
        )
        combined = sc.read_h5ad(merged_file)

        # Recover raw counts for cross-sample filtering / normalization. When the per-sample
        # files were normalized, raw lives in layers['raw']; otherwise .X already holds raw.
        # NOTE: a stale per-sample cache from a run with different normalization flags could
        # make this inconsistent — pair any flag change with force_recomputing=True.
        combined.X = combined.layers.pop('raw', combined.X)

        # Ensure .X is sparse CSR for efficient row-based filter operations (preserve dtype).
        _ensure_sparse_csr(combined)
        gc.collect()

        # ---- Cross-sample gene filtering (single pass over per-sample submatrices) ----
        reporter.message(f"{combined.n_vars} genes before cross-sample filtering")
        if min_spots_per_gene is not None or min_count_spots_ratio_per_gene is not None:
            if min_spots_per_gene is not None:
                reporter.message(f"Filtering genes by expression frequency (min_spots_per_gene={min_spots_per_gene})...")
            if min_count_spots_ratio_per_gene is not None:
                reporter.message(f"Filtering genes by count/spot ratio (min_count_spots_ratio_per_gene={min_count_spots_ratio_per_gene})...")
            combined = self._filter_genes(combined, min_spots_per_gene, min_count_spots_ratio_per_gene)
            reporter.message(f"{combined.n_vars} genes after cross-sample filtering")

        # Recompute QC metrics on the final merged matrix (raw counts) so .obs/.var QC
        # reflect the retained spots and genes; per-sample QC predates cross-sample filtering.
        combined.var['mt'] = combined.var_names.str.upper().str.match(r'^MT[-\.]')
        sc.pp.calculate_qc_metrics(combined, qc_vars=['mt'], percent_top=None, inplace=True)

        # Store raw counts after gene filtering ONLY when normalization will modify .X;
        # otherwise .X already holds the raw counts, so the layer would be a wasteful duplicate.
        will_normalize = total_counts_normalize or log1p_transform
        if will_normalize:
            combined.layers['raw'] = combined.X.copy()

        # ---- Normalize merged dataset ----
        if total_counts_normalize:
            reporter.message("Normalizing total counts...")
            sc.pp.normalize_total(combined, target_sum=self._NORMALIZE_TARGET_SUM, inplace=True)
        if log1p_transform:
            reporter.message("Applying log1p transformation...")
            sc.pp.log1p(combined)

        # Per-sample cluster labels are already in .obs["cluster"] from concatenation
        # (each sample was clustered independently to avoid batch effects)

        # ---- Set output metadata ----
        combined.obs['sample_id'] = pd.Categorical(combined.obs['sample_id'])
        combined.obs['cluster'] = pd.Categorical(combined.obs['cluster'])
        combined.obsm["spatial"] = np.asarray(combined.obsm["spatial"], dtype=np.float32)
        combined.uns["spot_size"] = {sid: ss.tolist() for sid, ss in spot_sizes.items()}

        # Guarantee sparse CSR storage, then save with gzip compression
        reporter.message("Saving combined dataset...")
        _ensure_sparse_csr(combined)
        write_h5ad_compat(combined, merged_file, compression=self._H5AD_COMPRESSION)
        del combined
        gc.collect()
        processed_samples["merged"] = merged_file
        return processed_samples

    def _filter_genes(self, adata: ad.AnnData, min_spots_per_gene: float | None,
                      min_count_spots_ratio: float | None) -> ad.AnnData:
        '''
        Cross-sample gene filtering in a single pass over the per-sample submatrices.

        Combines two opt-in criteria so each sample's rows are sliced and reduced only once
        (rather than once per criterion), and the matrix is materialized only once at the end:

        - min_spots_per_gene: keep a gene expressed in at least this fraction of a sample's
          spots, in at least one sample. ceil ensures min_cells >= 1 when the fraction > 0, so
          genes with zero expression always fail this threshold.
        - min_count_spots_ratio: keep a gene whose (total counts / expressed spots) meets the
          threshold in at least one sample. Genes absent from a sample are neutral (neither pass
          nor fail).

        Both criteria are per-sample: clearing one in a single sample is enough, so a gene
        confined to one sample is retained on the strength of that sample alone.

        When both criteria are requested a gene must satisfy both, though not necessarily in the
        same sample. Computing the ratio test on the full matrix is equivalent to running it after
        the frequency filter: subsetting genes (columns) does not change the per-gene
        expressed/total counts of the surviving genes. Operates on sparse matrices without
        densification.
        '''
        sample_ids = adata.obs['sample_id'].unique()

        freq_preserved = np.zeros(adata.n_vars, dtype=int)
        ratio_preserved = np.zeros(adata.n_vars, dtype=int)

        for sid in sample_ids:
            sample_mask = (adata.obs['sample_id'] == sid).values
            X_sample = adata.X[sample_mask, :]                          # one slice per sample
            expressed_per_gene = np.asarray((X_sample > 0).sum(axis=0)).flatten()

            if min_spots_per_gene is not None:
                min_cells = max(1, int(np.ceil(X_sample.shape[0] * min_spots_per_gene)))
                freq_preserved += (expressed_per_gene >= min_cells).astype(int)

            if min_count_spots_ratio is not None:
                total_counts = np.asarray(X_sample.sum(axis=0)).flatten()
                expressed_mask = expressed_per_gene > 0
                ratio_ok = np.zeros(adata.n_vars, dtype=bool)
                ratio_ok[expressed_mask] = (
                    total_counts[expressed_mask] >= min_count_spots_ratio * expressed_per_gene[expressed_mask]
                )
                ratio_preserved += ratio_ok.astype(int)

        # Passing in a single sample is enough: a gene that clears the per-sample threshold
        # somewhere is retained, regardless of how many samples it is detected in.
        gene_mask = np.ones(adata.n_vars, dtype=bool)
        if min_spots_per_gene is not None:
            gene_mask &= freq_preserved > 0
        if min_count_spots_ratio is not None:
            gene_mask &= ratio_preserved > 0

        return adata[:, gene_mask].copy()


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
