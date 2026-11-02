"""Immutable settings of one transcriptomic dataset run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StRunSettings:
    """Configured thresholds and normalization flags before ordered validation."""

    min_count_per_spot: int | None
    max_count_per_spot: int | None
    min_genes_per_spot: int | None
    max_genes_per_spot: int | None
    min_spots_per_gene: float | None
    min_count_spots_ratio_per_gene: float | None
    remove_mitochondrial_genes: bool
    total_counts_normalize: bool
    log1p_transform: bool
    resolution: str
    force_recomputing: bool
