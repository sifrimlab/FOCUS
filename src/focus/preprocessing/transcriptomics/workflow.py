"""Coordinate transcriptomic samples, merged caching, and scientific persistence."""

from __future__ import annotations
from typing import TYPE_CHECKING
import os
import gc

import numpy as np
from anndata import AnnData

from focus.config.options import StNormalization
from focus.preprocessing.transcriptomics.run_settings import StRunSettings
from focus.preprocessing.transcriptomics.dataset_operations import (
    collect_sample_metadata,
    merged_qc,
    read_combined,
    set_merged_metadata,
)
from focus.preprocessing.transcriptomics.normalization import normalize
from focus.preprocessing.transcriptomics.matrix import _ensure_sparse_csr
from focus.preprocessing.transcriptomics.clustering import METHOD_VERSION
from focus.preprocessing.transcriptomics.technology import (
    AREA_KEY,
    Technology,
    technology,
)
from focus.io.paths import merged_preprocessed_file
from focus.io.provenance import fingerprint, cached_fingerprint
from focus.io.anndata import write_h5ad_atomic
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
            capture=settings.capture,
            unit=settings.unit,
            normalization=settings.normalization,
            quality_control=settings.quality_control,
            remove_mitochondrial_genes=settings.remove_mitochondrial_genes,
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
    tech = technology(settings.capture, settings.unit)
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
            "normalization": settings.normalization,
            "min_gene_observations": tech.min_gene_observations,
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
        dataset, tech, settings, processed_samples, merged_file, merged_key, reporter
    )
    processed_samples["merged"] = merged_file
    return processed_samples


def resolve_normalization(
    combined: AnnData, tech: Technology, requested: str, reporter: StepReporter
) -> str:
    """
    The method to apply: ``auto`` resolved by the technology.

    ``auto`` falls back from area normalization to ``log_normalize``, with a warning, when some
    spot has no finite positive area (segmented cells without an area column).
    """
    method = tech.normalization(requested)
    if (
        requested == StNormalization.AUTO
        and method == StNormalization.AREA_LOG_NORMALIZE
    ):
        area = (
            combined.obs[AREA_KEY].to_numpy(dtype=np.float64)
            if AREA_KEY in combined.obs
            else np.full(combined.n_obs, np.nan)
        )
        if not np.all(np.isfinite(area) & (area > 0)):
            reporter.warning(
                "Some cells have no area column: log_normalize is used instead of area normalization"
            )
            method = StNormalization.LOG_NORMALIZE
    return method


def write_combined(
    dataset: SpatialTranscriptomicDataset,
    tech: Technology,
    settings: StRunSettings,
    processed_samples: dict[str, str],
    merged_file: str,
    merged_key: str,
    reporter: StepReporter,
) -> None:
    """Merge raw counts on the selected union panel, normalize globally, persist, release."""
    spot_sizes, clustering_provenance, detection = collect_sample_metadata(
        dataset, processed_samples
    )
    combined = read_combined(
        dataset,
        processed_samples,
        merged_file,
        detection,
        tech.min_gene_observations,
        reporter,
    )
    merged_qc(combined)
    method = resolve_normalization(combined, tech, settings.normalization, reporter)
    combined.uns["focus_normalization"] = normalize(combined, method, reporter)
    set_merged_metadata(combined, spot_sizes, clustering_provenance, merged_key)

    reporter.detail("Saving combined dataset")
    _ensure_sparse_csr(combined)
    write_h5ad_atomic(combined, merged_file, compression=dataset._H5AD_COMPRESSION)
    del combined
    gc.collect()
