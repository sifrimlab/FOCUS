"""Ordered constructor checks for interactive direct-mapping alignment."""

from focus.config.options import ModalityType


def validate_inputs(
    path: str,
    reference_modality: dict[str, str],
    target_modality: dict[str, str],
    reference_modality_name: str,
    target_modality_name: str,
    reference_modality_type: str,
    target_modality_type: str,
) -> None:
    """Check path/maps, names and modality types, preserving exception types and order."""
    if (
        not isinstance(path, str)
        or not isinstance(reference_modality, dict)
        or not isinstance(target_modality, dict)
    ):
        raise TypeError("Invalid input types.")
    if not isinstance(reference_modality_name, str) or not isinstance(
        target_modality_name, str
    ):
        raise TypeError("Invalid input types.")
    if (
        reference_modality_type not in ModalityType.list()
        or target_modality_type not in ModalityType.list()
    ):
        raise ValueError("Invalid modality type.")
