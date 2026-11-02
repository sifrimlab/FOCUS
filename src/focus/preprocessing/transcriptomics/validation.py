"""Ordered dataset threshold checks for transcriptomics."""

from focus.preprocessing.transcriptomics.run_settings import StRunSettings


def validate_thresholds(settings: StRunSettings) -> None:
    """Raise the existing threshold errors in their original validation order."""
    if settings.min_count_per_spot is not None and settings.min_count_per_spot <= 0:
        raise ValueError("min_count_per_spot must be greater than 0.")
    if settings.max_count_per_spot is not None and settings.max_count_per_spot <= 0:
        raise ValueError("max_count_per_spot must be greater than 0.")
    if settings.min_genes_per_spot is not None and settings.min_genes_per_spot <= 0:
        raise ValueError("min_genes_per_spot must be greater than 0.")
    if settings.max_genes_per_spot is not None and settings.max_genes_per_spot <= 0:
        raise ValueError("max_genes_per_spot must be greater than 0.")
    if settings.min_spots_per_gene is not None and not (
        0 < settings.min_spots_per_gene < 1
    ):
        raise ValueError("min_spots_per_gene must be between 0 and 1.")
    if (
        settings.min_count_spots_ratio_per_gene is not None
        and settings.min_count_spots_ratio_per_gene <= 0
    ):
        raise ValueError("min_count_spots_ratio_per_gene must be greater than 0.")
