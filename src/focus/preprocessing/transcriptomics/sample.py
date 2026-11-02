"""Validate one spatial count container and coordinate its cached preprocessing output."""

from focus.preprocessing.transcriptomics.parameters import StSampleSettings
from focus.preprocessing.transcriptomics.provenance import sample_fingerprint
from focus.preprocessing.transcriptomics.sample_operations import write_sample

from focus.spatial.matrices import SparseStorage
from typing import cast

from numpy.typing import ArrayLike
from focus.runtime.reporting import StepReporter

import os
import anndata as ad
import numpy as np
import scanpy as sc
import scipy.sparse as sp

from focus.io.paths import preprocessed_file
from focus.config.options import SpotResolution
from focus.spatial.settings import clustering_preset
from focus.runtime.reporter_context import get_reporter
from focus.io.provenance import cached_fingerprint
from focus.preprocessing.base import SampleSource


class SpatialTranscriptomic(SampleSource):
    """
    Class for handling spot-based spatial transcriptomic data.
    Supports any technology (Visium, Xenium, MERFISH, etc.) as long as
    the input is an AnnData file with raw gene counts in .X and
    spatial coordinates in .obsm["spatial"].
    """

    _NORMALIZE_TARGET_SUM = 1e4
    _DEFAULT_SPOT_SIZE = np.array([1.0, 1.0], dtype=np.float32)
    _H5AD_COMPRESSION = "gzip"

    def __init__(self, source_path: str, sample_id: str, modality_name: str) -> None:
        """Create sample output directories and retain its transcriptomics input directory.

        Parameters
        ----------
        source_path : str
            Existing readable dataset root.
        sample_id : str
            Sample directory identifier.
        modality_name : str
            Count-container input subdirectory and processed-output name."""
        super().__init__(source_path, sample_id, modality_name)
        self.input_path = os.path.join(source_path, sample_id, modality_name)

    def _input_file(self) -> str:
        """Locate the sample's supported transcriptomics input file."""
        files = sorted(
            os.path.join(self.input_path, f)
            for f in os.listdir(self.input_path)
            if f.endswith(".h5ad")
        )
        if not files:
            raise FileNotFoundError(f"No .h5ad file found in {self.input_path}")
        return files[0]

    def load_data(self) -> ad.AnnData:
        """
        Load spatial transcriptomic data from the source path.
        The first .h5ad file in sorted filename order in the sample directory is loaded.
        Validates that .obsm["spatial"] exists and normalizes .uns["spot_size"].

        Returns
        -------
        adata : ad.AnnData
            The loaded and validated AnnData object.
        """

        adata = sc.read_h5ad(self._input_file())

        # Validate spatial coordinates
        if "spatial" not in adata.obsm:
            raise ValueError(
                f"Sample {self.sample_id}: AnnData is missing .obsm['spatial']. "
                "Spatial coordinates are required for spatial transcriptomics preprocessing."
            )
        adata.obsm["spatial"] = np.asarray(adata.obsm["spatial"], dtype=np.float32)

        coords = adata.obsm["spatial"]
        if coords.shape != (adata.n_obs, 2) or not np.isfinite(coords).all():
            raise ValueError(
                "ST spatial coordinates must be finite with shape (n_obs, 2)"
            )

        # Normalize spot_size to a float32 array of shape (2,)
        adata.uns["spot_size"] = self._normalize_spot_size(
            adata.uns.get("spot_size", None)
        )

        if not np.isfinite(adata.uns["spot_size"]).all() or np.any(
            adata.uns["spot_size"] <= 0
        ):
            raise ValueError("spot_size must contain positive finite dimensions")

        # Ensure .X is sparse CSR for memory efficiency (preserve dtype; convert CSC/COO too)
        if not sp.issparse(adata.X):
            adata.X = sp.csr_matrix(adata.X)
        elif cast(SparseStorage, adata.X).format != "csr":
            adata.X = cast(SparseStorage, adata.X).tocsr()

        return adata

    @staticmethod
    def _normalize_spot_size(spot_size: ArrayLike) -> np.ndarray:
        """
        Normalize spot_size to a float32 array of shape (2,).
        - None → [1.0, 1.0]
        - scalar → [val, val]
        - array of 1 → [val, val]
        - array of 2 → as-is
        """
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

    def preprocess_data(
        self,
        min_count_per_spot: int | None = None,
        max_count_per_spot: int | None = None,
        min_genes_per_spot: int | None = None,
        max_genes_per_spot: int | None = None,
        remove_mitochondrial_genes: bool = False,
        total_counts_normalize: bool = False,
        log1p_transform: bool = False,
        resolution: str = SpotResolution.STANDARD,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> str:
        """
        Preprocess a single spatial transcriptomic sample.

        Pipeline:
        1. Load and validate data
        2. Flag mitochondrial genes (.var["mt"])
        3. Filter spots by count/gene thresholds (opt-in)
        4. Compute QC metrics (scanpy.pp.calculate_qc_metrics) on the retained spots
        5. Optionally remove mitochondrial genes (opt-in, uses the .var["mt"] QC flag)
        6. Alignment labels on a clustering-only normalized/HVG copy, on native spots or (large or
           sparse samples) lattice blocks / Voronoi supercells; eligibility and provenance are
           persisted, analytical intermediates and units discarded
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
        resolution : str
            SpotResolution preset for the alignment clustering: 'standard' (multicellular spots)
            or 'sub_cellular' (weak high-resolution spots: coarser units, wider smoothing).
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
        """

        reporter = step_reporter or get_reporter()
        output_file = preprocessed_file(
            self.source_path, self.sample_id, self.modality_name, "h5ad"
        )

        clustering_preset(resolution)  # validate before any work
        settings: StSampleSettings = {
            "min_count_per_spot": min_count_per_spot,
            "max_count_per_spot": max_count_per_spot,
            "min_genes_per_spot": min_genes_per_spot,
            "max_genes_per_spot": max_genes_per_spot,
            "remove_mitochondrial_genes": remove_mitochondrial_genes,
            "total_counts_normalize": total_counts_normalize,
            "log1p_transform": log1p_transform,
            "resolution": resolution,
        }
        cache_key = sample_fingerprint(self, settings)
        self.preprocessing_changed = (
            force_recomputing or cached_fingerprint(output_file) != cache_key
        )
        if not self.preprocessing_changed:
            reporter.cached()
            return output_file
        write_sample(self, settings, reporter, cache_key, output_file)
        return output_file
