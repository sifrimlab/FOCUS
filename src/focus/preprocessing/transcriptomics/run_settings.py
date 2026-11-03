"""Immutable settings of one transcriptomic dataset run."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StRunSettings:
    """Configured technology, quality control and normalization of one dataset run."""

    capture: str
    unit: str
    normalization: str
    quality_control: bool
    remove_mitochondrial_genes: bool
    force_recomputing: bool
