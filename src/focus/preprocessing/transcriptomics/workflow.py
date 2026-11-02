"""Coordinate transcriptomic samples, merged caching, and scientific persistence."""

from __future__ import annotations
from typing import TYPE_CHECKING
import os
import gc
from focus.preprocessing.transcriptomics.run_settings import StRunSettings
from focus.preprocessing.transcriptomics.dataset_operations import (
    collect_sample_metadata,
    read_combined,
    filter_combined,
    set_merged_metadata,
)
from focus.preprocessing.transcriptomics.normalization import normalize_counts
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr
from focus.preprocessing.transcriptomics.clustering import METHOD_VERSION
from focus.io.paths import merged_preprocessed_file
from focus.io.provenance import fingerprint, cached_fingerprint
from focus.io.anndata import write_h5ad_compat
from focus.runtime.reporting import StepReporter

if TYPE_CHECKING:
    from focus.preprocessing.transcriptomics.dataset import SpatialTranscriptomicDataset


def preprocess_samples(
    dataset: SpatialTranscriptomicDataset,
    settings: StRunSettings,
    reporter: StepReporter,
) -> dict[str, str]:
    """Preprocess each sample in the configured order, preserving its cache decisions."""
    processed_samples: dict[str, str] = {}
    for sample in reporter.tqdm(
        dataset.samples, 1, 2, "Processing samples", unit="sample"
    ):
        processed_samples[sample.sample_id] = sample.preprocess_data(
            min_count_per_spot=settings.min_count_per_spot,
            max_count_per_spot=settings.max_count_per_spot,
            min_genes_per_spot=settings.min_genes_per_spot,
            max_genes_per_spot=settings.max_genes_per_spot,
            remove_mitochondrial_genes=settings.remove_mitochondrial_genes,
            total_counts_normalize=settings.total_counts_normalize,
            log1p_transform=settings.log1p_transform,
            resolution=settings.resolution,
            force_recomputing=settings.force_recomputing,
            step_reporter=reporter,
        )
    return processed_samples


def execute_dataset(
    dataset: SpatialTranscriptomicDataset,
    settings: StRunSettings,
    reporter: StepReporter,
) -> dict[str, str]:
    """Process sample outputs and reuse or rebuild the cross-sample container."""
    processed_samples = preprocess_samples(dataset, settings, reporter)
    reporter.step(2, 2, "Building combined dataset")

    merged_file = merged_preprocessed_file(
        dataset.dataset_source_path, dataset.samples[0].modality_name, "h5ad"
    )
    os.makedirs(os.path.dirname(merged_file), exist_ok=True)

    sample_keys = {
        sid: cached_fingerprint(file) for sid, file in processed_samples.items()
    }
    merged_key = fingerprint(
        {
            "method": METHOD_VERSION,
            "samples": sample_keys,
            "min_spots_per_gene": settings.min_spots_per_gene,
            "min_count_spots_ratio_per_gene": settings.min_count_spots_ratio_per_gene,
            "total_counts_normalize": settings.total_counts_normalize,
            "log1p_transform": settings.log1p_transform,
        }
    )
    samples_changed = any(
        getattr(sample, "preprocessing_changed", True) for sample in dataset.samples
    )
    if (
        not settings.force_recomputing
        and not samples_changed
        and cached_fingerprint(merged_file) == merged_key
    ):
        reporter.cached()
        processed_samples["merged"] = merged_file
        return processed_samples
    write_combined(
        dataset, settings, processed_samples, merged_file, merged_key, reporter
    )
    processed_samples["merged"] = merged_file
    return processed_samples


def write_combined(
    dataset: SpatialTranscriptomicDataset,
    settings: StRunSettings,
    processed_samples: dict[str, str],
    merged_file: str,
    merged_key: str,
    reporter: StepReporter,
) -> None:
    """Filter raw merged counts, normalize opt-in outputs, persist, then release them."""
    spot_sizes, clustering_provenance = collect_sample_metadata(
        dataset, processed_samples
    )
    combined = read_combined(dataset, processed_samples, merged_file, reporter)
    combined = filter_combined(
        dataset,
        combined,
        settings.min_spots_per_gene,
        settings.min_count_spots_ratio_per_gene,
        reporter,
    )
    normalize_counts(
        combined,
        settings.total_counts_normalize,
        settings.log1p_transform,
        dataset._NORMALIZE_TARGET_SUM,
        reporter,
    )
    set_merged_metadata(combined, spot_sizes, clustering_provenance, merged_key)

    # Guarantee sparse CSR storage, then save with gzip compression
    reporter.detail("Saving combined dataset")
    _ensure_sparse_csr(combined)
    write_h5ad_compat(combined, merged_file, compression=dataset._H5AD_COMPRESSION)
    del combined
    gc.collect()
