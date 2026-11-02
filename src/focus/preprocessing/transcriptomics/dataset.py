"""Coordinate transcriptomic sample processing, shared gene filtering and merged persistence."""

from focus.preprocessing.transcriptomics.run_settings import StRunSettings
from focus.preprocessing.transcriptomics.validation import validate_thresholds
from focus.preprocessing.transcriptomics.workflow import execute_dataset

from focus.runtime.reporting import StepReporter


from focus.config.options import SpotResolution
from focus.spatial.settings import clustering_preset
from focus.runtime.reporter_context import get_reporter
from focus.preprocessing.base import DatasetState
from focus.preprocessing.transcriptomics.sample import SpatialTranscriptomic


class SpatialTranscriptomicDataset(DatasetState[SpatialTranscriptomic]):
    """
    Class for handling multiple spatial transcriptomic samples.
    Processes each sample individually, then merges into a combined dataset
    with cross-sample gene filtering and unified normalization.
    """

    _NORMALIZE_TARGET_SUM = 1e4
    _H5AD_COMPRESSION = "gzip"

    def __init__(self, path: str, samples: list[SpatialTranscriptomic]) -> None:
        """Retain the dataset root and ordered modality sample objects.

        Parameters
        ----------
        path : str
            Root used for existing per-sample and merged output paths.
        samples : list
            Modality sample objects retained by reference."""
        super().__init__(path, samples)

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
        resolution: str = SpotResolution.STANDARD,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
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
        resolution : str
            SpotResolution preset for the alignment clustering: 'standard' (multicellular spots)
            or 'sub_cellular' (weak high-resolution spots: coarser units, wider smoothing).
        force_recomputing : bool
            Whether to force recomputing even if output exists.

        Returns
        -------
        processed_samples : dict[str, str]
            Maps sample IDs (and "merged") to output file paths.
        """

        settings = StRunSettings(
            min_count_per_spot,
            max_count_per_spot,
            min_genes_per_spot,
            max_genes_per_spot,
            min_spots_per_gene,
            min_count_spots_ratio_per_gene,
            remove_mitochondrial_genes,
            total_counts_normalize,
            log1p_transform,
            resolution,
            force_recomputing,
        )
        validate_thresholds(settings)
        clustering_preset(resolution)
        reporter = step_reporter or get_reporter()
        return execute_dataset(self, settings, reporter)
