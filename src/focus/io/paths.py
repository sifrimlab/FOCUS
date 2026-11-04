"""Construct the existing per-sample and merged pipeline output paths."""

import os
from focus.config.choices import _AbstractEnum


def preprocessed_file(
    base_path: str, sample_id: str, modality_name: str, file_type: str
) -> str:
    """Return the existing preprocessed file path."""
    return os.path.join(
        base_path,
        sample_id,
        FocusOutputDirectories.PREPROCESSING,
        modality_name,
        f"{modality_name}_{sample_id}_processed.{file_type}",
    )


def merged_preprocessed_file(base_path: str, modality_name: str, file_type: str) -> str:
    """Return the existing merged preprocessed file path."""
    return os.path.join(
        base_path,
        FocusOutputDirectories.MERGED,
        FocusOutputDirectories.PREPROCESSING,
        f"{modality_name}_merged_processed.{file_type}",
    )


def alignment_file(
    base_path: str, sample_id: str, modality_name: str, file_type: str
) -> str:
    """Return the existing alignment file path."""
    return os.path.join(
        base_path,
        sample_id,
        FocusOutputDirectories.ALIGNMENT,
        f"{modality_name}_{sample_id}_processed_aligned.{file_type}",
    )


def merged_alignment_file(base_path: str, modality_name: str, file_type: str) -> str:
    """Return the existing merged alignment file path."""
    return os.path.join(
        base_path,
        FocusOutputDirectories.MERGED,
        FocusOutputDirectories.ALIGNMENT,
        f"{modality_name}_merged_processed_aligned.{file_type}",
    )


def registration_file(
    base_path: str, sample_id: str, modality_name: str, file_type: str
) -> str:
    """Return the existing registration file path."""
    return os.path.join(
        base_path,
        sample_id,
        FocusOutputDirectories.REGISTRATION,
        f"{modality_name}_{sample_id}_processed_aligned_registered.{file_type}",
    )


def merged_registration_file(base_path: str, modality_name: str, file_type: str) -> str:
    """Return the existing merged registration file path."""
    return os.path.join(
        base_path,
        FocusOutputDirectories.MERGED,
        FocusOutputDirectories.REGISTRATION,
        f"{modality_name}_merged_processed_aligned_registered.{file_type}",
    )


def multimodal_file(base_path: str, file_type: str) -> str:
    """Return the existing multimodal file path."""
    return os.path.join(
        base_path, FocusOutputDirectories.MERGED, f"multimodal_dataset.{file_type}"
    )


def annotation_file(
    base_path: str, sample_id: str, modality_name: str, file_type: str
) -> str:
    """Return the existing annotation file path."""
    return os.path.join(
        base_path,
        sample_id,
        FocusOutputDirectories.ANNOTATIONS,
        f"{modality_name}_{sample_id}_annotated.{file_type}",
    )


def merged_annotation_file(base_path: str, modality_name: str, file_type: str) -> str:
    """Return the existing merged annotation file path."""
    return os.path.join(
        base_path,
        FocusOutputDirectories.MERGED,
        FocusOutputDirectories.ANNOTATIONS,
        f"{modality_name}_merged_annotated.{file_type}",
    )


class FocusOutputDirectories(_AbstractEnum):
    """Directory names in the existing on-disk output layout."""

    PREPROCESSING = "preprocessing"
    ALIGNMENT = "alignment"
    REGISTRATION = "registration"
    ANNOTATIONS = "annotations"
    PLOTS = "plots"
    RESOURCES = "resources"
    MERGED = "merged"
    # Raw source data of the dataset (e.g. pre-h5ad spatial transcriptomics); never a sample.
    RAW = "raw"


def is_reserved_directory_name(name: str) -> bool:
    """Return whether name is a FOCUS system directory name, ignoring case."""
    return name.casefold() in {d.casefold() for d in FocusOutputDirectories.list()}
