"""Keyword settings consumed by SpatialTranscriptomicDataset."""

from typing import TypedDict


class StSampleSettings(TypedDict):
    """Per-sample technology, quality-control, feature and normalization settings (fingerprinted).

    The normalization is applied to the merged dataset; each sample's alignment labels are
    computed on it too, so it enters the sample fingerprint.
    """

    capture: str
    unit: str
    normalization: str
    quality_control: bool
    remove_controls_and_empty: bool
    remove_mitochondrial_genes: bool


class SpatialTranscriptomicSettings(StSampleSettings):
    """Dataset preprocessing keywords, extending sample settings with the cache override."""

    force_recomputing: bool
