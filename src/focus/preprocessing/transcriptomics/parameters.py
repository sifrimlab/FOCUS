"""Keyword settings consumed by SpatialTranscriptomicDataset."""

from typing import TypedDict


class StSampleSettings(TypedDict):
    """Per-sample technology, quality-control and feature settings (fingerprinted)."""

    capture: str
    unit: str
    quality_control: bool
    remove_mitochondrial_genes: bool


class SpatialTranscriptomicSettings(StSampleSettings):
    """Dataset preprocessing keywords, extending sample settings with the merged normalization."""

    normalization: str
    force_recomputing: bool
