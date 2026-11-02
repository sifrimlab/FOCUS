"""Compatibility checks in the established configuration-validation sequence."""

from typing import cast

from focus.config.types import PipelineConfig, ModalityConfig


from focus.config.keys import (
    ConfigParameters,
    ModalityParameters,
    STPreprocessingParams,
)
from focus.config.options import RegistrationType, AlignmentStrategy, SpotResolution
from focus.config.catalog import (
    REGISTRATION_COMPATIBILITY,
    ALIGNMENT_STRATEGY_COMPATIBILITY,
    IMAGE_MODALITY_TYPES,
    SPOT_MODALITY_TYPES,
)


def validate_resolution(config: PipelineConfig) -> None:
    """Validate the existing spot-clustering resolution keys."""

    # Validate the clustering resolution preset of every spot modality (also used by the GUI).
    for modality in config[ConfigParameters.MODALITIES]:
        if modality[ModalityParameters.TYPE] in SPOT_MODALITY_TYPES:
            settings = modality.get(ModalityParameters.PROCESSING_SETTINGS) or {}
            resolution = settings.get(
                STPreprocessingParams.RESOLUTION, SpotResolution.STANDARD
            )
            if resolution not in SpotResolution.list():
                raise ValueError(
                    f"Modality '{modality[ModalityParameters.NAME]}': resolution must be one of "
                    f"{SpotResolution.list()}, got {resolution!r}."
                )


def validate_registration(config: PipelineConfig) -> None:
    """Check registration strategies against their modality types."""

    # --- Step 9: Registration type compatibility ---
    for modality in config[ConfigParameters.MODALITIES]:
        reg_type = modality[ModalityParameters.REGISTRATION_TYPE]
        if reg_type not in RegistrationType.list():
            raise ValueError(
                f"Unsupported registration type '{reg_type}' for modality '{modality[ModalityParameters.NAME]}'. "
                f"Supported types: {RegistrationType.list()}"
            )

        compatible = REGISTRATION_COMPATIBILITY.get(reg_type)
        if compatible is not None:  # None means all types are compatible
            mod_type = modality[ModalityParameters.TYPE]
            if mod_type not in compatible:
                raise ValueError(
                    f"Registration type '{reg_type}' is not compatible with modality type '{mod_type}' "
                    f"(modality '{modality[ModalityParameters.NAME]}'). Compatible types: {compatible}"
                )


def validate_alignment(config: PipelineConfig) -> None:
    """Check reference types, pre-aligned strategies, and modality pairs."""

    # --- Step 9b: Alignment strategy compatibility ---
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]
    ref_modality = next(
        m
        for m in config[ConfigParameters.MODALITIES]
        if m[ModalityParameters.NAME] == ref_name
    )
    ref_type = ref_modality[ModalityParameters.TYPE]
    pre_aligned_count = 0
    for modality in config[ConfigParameters.MODALITIES]:
        strategy = modality[ModalityParameters.ALIGNMENT_STRATEGY]
        if strategy not in AlignmentStrategy.list():
            raise ValueError(
                f"Unsupported alignment strategy '{strategy}' for modality '{modality[ModalityParameters.NAME]}'. "
                f"Supported strategies: {AlignmentStrategy.list()}"
            )
        if validate_pre_aligned(modality, strategy, ref_name, ref_type):
            pre_aligned_count += 1
    if pre_aligned_count > 1:
        raise ValueError(
            f"At most one non-reference modality may use the 'pre_aligned' alignment strategy. "
            f"The reference modality can only be expressed in one coordinate system at a time, "
            f"but {pre_aligned_count} modalities have 'pre_aligned' set."
        )

    validate_alignment_pair(config, ref_name, ref_type)


def validate_pre_aligned(
    modality: ModalityConfig,
    strategy: str,
    ref_name: str,
    ref_type: str,
) -> bool:
    """Validate one pre-aligned strategy and report whether it contributes to the pair limit."""
    if strategy == AlignmentStrategy.PRE_ALIGNED:
        if modality[ModalityParameters.NAME] == ref_name:
            raise ValueError(
                f"Alignment strategy 'pre_aligned' cannot be set on the reference modality "
                f"'{modality[ModalityParameters.NAME]}'."
            )
        # Pre-aligned requires the reference to be spot-based (its coordinates must be
        # expressible in another modality's coordinate system)
        compatible_ref_types = ALIGNMENT_STRATEGY_COMPATIBILITY[
            AlignmentStrategy.PRE_ALIGNED
        ]
        if ref_type not in cast(list[str], compatible_ref_types):
            raise ValueError(
                f"Alignment strategy 'pre_aligned' requires a spot-based reference modality, "
                f"but reference '{ref_name}' has type '{ref_type}'. "
                f"Supported reference types: {compatible_ref_types}"
            )
        return True
    return False


def validate_alignment_pair(
    config: PipelineConfig, ref_name: str, ref_type: str
) -> None:
    """Reject enabled image-to-spot alignment after strategy validation has completed."""
    # --- Step 9c: Alignment modality-pair support ---
    # Alignment maps the reference modality onto each non-reference modality. An image-based
    # reference can only be mapped onto another image (the overlap crop); mapping it onto a
    # spot-based modality is not implemented, so reject it here instead of letting the user
    # align every sample in the GUI and discarding the result.
    if config[ConfigParameters.PERFORM_ALIGNMENT] and ref_type in IMAGE_MODALITY_TYPES:
        spot_targets = [
            m[ModalityParameters.NAME]
            for m in config[ConfigParameters.MODALITIES]
            if m[ModalityParameters.NAME] != ref_name
            and m[ModalityParameters.TYPE] in SPOT_MODALITY_TYPES
        ]
        if spot_targets:
            raise ValueError(
                f"Reference modality '{ref_name}' has image-based type '{ref_type}', which cannot be "
                f"aligned to the spot-based modality/modalities {spot_targets}. An image-based reference "
                f"can only be aligned to image-based modalities "
                f"({', '.join(IMAGE_MODALITY_TYPES)}). Set 'reference_modality' to a spot-based "
                f"modality ({', '.join(SPOT_MODALITY_TYPES)}), which is also required for MuData "
                f"compilation."
            )
