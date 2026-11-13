"""Static shapes of the existing JSON configuration; no runtime coercion."""

from collections.abc import Mapping

from typing import TypeAlias, TypedDict

import numpy as np

MassSetting: TypeAlias = str | Mapping[str, list[float] | np.ndarray] | None


class ProcessingSettings(TypedDict, total=False):
    """Existing modality settings, with their original optional fields."""

    force_recomputing: bool
    image_type: str
    z_projection: str
    remove_background: bool
    crop_to_tissue: bool
    pixel_size: float | None
    lipid_annotation_db: str | None
    mass_tolerance: float
    intensity_normalization: str
    recalibration_reference: MassSetting
    internal_standard_mz: MassSetting
    sample_type: str
    resolution: str
    max_workers: int
    savgol_window: int
    savgol_polyorder: int
    bg_min_area_fraction: float
    otsu_threshold_factor: float
    min_object_size: int
    capture: str
    unit: str
    normalization: str
    quality_control: bool
    remove_controls_and_empty: bool
    remove_mitochondrial_genes: bool


class RegistrationSettings(TypedDict, total=False):
    """Settings consumed by existing registration strategies."""

    force_recomputing: bool
    background_color: str | None
    patch_embeddings: bool
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
    # Modality aligned to instead of the reference; the reference reaches this one through it
    alignment_guide: str | None


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
