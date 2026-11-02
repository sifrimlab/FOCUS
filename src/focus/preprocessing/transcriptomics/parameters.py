"""Keyword settings consumed by SpatialTranscriptomicDataset."""

from typing import TypedDict


class StSampleSettings(TypedDict):
    """Original per-sample filter, normalization and clustering fingerprint fields."""

    min_count_per_spot: int | None
    max_count_per_spot: int | None
    min_genes_per_spot: int | None
    max_genes_per_spot: int | None
    remove_mitochondrial_genes: bool
    total_counts_normalize: bool
    log1p_transform: bool
    resolution: str


class SpatialTranscriptomicSettings(StSampleSettings):
    """Dataset preprocessing keywords, extending sample settings with shared feature filters."""

    min_spots_per_gene: float | None
    min_count_spots_ratio_per_gene: float | None
    force_recomputing: bool
