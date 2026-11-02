"""Static shapes of the existing JSON configuration; no runtime coercion."""

from collections.abc import Mapping

from typing import TypeAlias, TypedDict

import numpy as np

MassSetting: TypeAlias = str | Mapping[str, list[float] | np.ndarray] | None


class ProcessingSettings(TypedDict, total=False):
    """Existing modality settings, with their original optional fields."""

    force_recomputing: bool
    color_enhancement: bool
    remove_background: bool
    crop_to_tissue: bool
    background_color: str
    min_object_coverage: float
    clip_percentile: int
    crop_margin: int
    gamma: float
    contrast_saturation: float
    lipid_annotation_db: str | None
    mass_tolerance: int
    frequency_threshold: float
    intensity_normalization: str
    recalibration_reference: MassSetting
    internal_standard_mz: MassSetting
    detect_background: bool
    sample_type: str
    resolution: str
    max_workers: int
    savgol_window: int
    savgol_polyorder: int
    bg_min_area_fraction: float
    otsu_threshold_factor: float
    min_object_size: int
    min_count_per_spot: int | None
    max_count_per_spot: int | None
    min_genes_per_spot: int | None
    max_genes_per_spot: int | None
    min_spots_per_gene: float | None
    min_count_spots_ratio_per_gene: float | None
    remove_mitochondrial_genes: bool
    total_counts_normalize: bool
    log1p_transform: bool


class RegistrationSettings(TypedDict, total=False):
    """Settings consumed by existing registration strategies."""

    force_recomputing: bool
    background_color: str | None
    patch_size: int


class ModalityConfig(TypedDict, total=False):
    """One modality declaration, retaining its dictionary representation."""

    name: str
    type: str
    processing_settings: ProcessingSettings
    registration_type: str
    registration_settings: RegistrationSettings
    alignment_strategy: str
    alignment_force_recomputing: bool


class AnnotationConfig(TypedDict):
    """Source modality and format of spatial annotations."""

    modality_name: str
    file_type: str


class PipelineConfig(TypedDict, total=False):
    """Configuration after the existing validation/defaulting sequence."""

    dataset_path: str
    modalities: list[ModalityConfig]
    reference_modality: str
    perform_alignment: bool
    perform_registration: bool
    huggingface_token: str | None
    spatial_annotations: AnnotationConfig | None
    ignore_samples: list[str]
    samples: list[str]
    last_edit: str | None
