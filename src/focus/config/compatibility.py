"""Compatibility checks in the established configuration-validation sequence."""

from collections.abc import Mapping
from typing import cast

from focus.config.types import PipelineConfig, ModalityConfig


from focus.config.keys import (
    ConfigParameters,
    ModalityParameters,
    MICROSCOPY_REMOVED_SETTINGS,
    MSI_REMOVED_NORMALIZATIONS,
    MSI_REMOVED_SETTINGS,
    MicroscopyImageProcessingParams,
    MsiPreprocessingParams,
    RAMAN_REMOVED_SETTINGS,
    RamanPreprocessingParams,
    REGISTRATION_REMOVED_TYPES,
    STPreprocessingParams,
    ST_REMOVED_SETTINGS,
)
from focus.config.options import (
    AlignmentStrategy,
    MicroscopyImageType,
    MicroscopyZProjection,
    ModalityType,
    MsiIntensityNormalization,
    MsiSampleType,
    RamanNormalization,
    RegistrationType,
    SpotResolution,
    StCapture,
    StNormalization,
    StUnit,
)
from focus.config.catalog import (
    REGISTRATION_COMPATIBILITY,
    ALIGNMENT_STRATEGY_COMPATIBILITY,
    IMAGE_MODALITY_TYPES,
    SPOT_MODALITY_TYPES,
)


def validate_resolution(config: PipelineConfig) -> None:
    """Validate the MSI clustering resolution and the ST and microscopy preset settings."""

    for modality in config[ConfigParameters.MODALITIES]:
        settings = modality.get(ModalityParameters.PROCESSING_SETTINGS) or {}
        name = modality[ModalityParameters.NAME]
        if modality[ModalityParameters.TYPE] == ModalityType.ST:
            validate_st_settings(name, settings)
        elif modality[ModalityParameters.TYPE] == ModalityType.RAMAN:
            validate_raman_settings(name, settings)
        elif modality[ModalityParameters.TYPE] == ModalityType.MICROSCOPY_IMAGE:
            validate_microscopy_settings(
                name, settings, modality[ModalityParameters.REGISTRATION_TYPE]
            )
        elif modality[ModalityParameters.TYPE] in SPOT_MODALITY_TYPES:
            if modality[ModalityParameters.TYPE] == ModalityType.MSI:
                validate_msi_settings(name, settings)
            resolution = settings.get(
                MsiPreprocessingParams.RESOLUTION, SpotResolution.STANDARD
            )
            if resolution not in SpotResolution.list():
                raise ValueError(
                    f"Modality '{name}': resolution must be one of "
                    f"{SpotResolution.list()}, got {resolution!r}."
                )


def validate_st_settings(name: str, settings: Mapping[str, object]) -> None:
    """Reject removed ST settings (naming their replacement) and unknown technology values."""
    removed = [key for key in settings if key in ST_REMOVED_SETTINGS]
    if removed:
        replacements = ", ".join(
            f"'{key}' (use '{ST_REMOVED_SETTINGS[key]}')" for key in removed
        )
        raise ValueError(
            f"Modality '{name}': these ST settings were removed: {replacements}. "
            "Quality control, the gene filter and normalization are now calibrated from "
            "'capture' and 'unit'."
        )
    choices = {
        STPreprocessingParams.CAPTURE: StCapture.list(),
        STPreprocessingParams.UNIT: StUnit.list(),
        STPreprocessingParams.NORMALIZATION: StNormalization.list(),
    }
    for key, options in choices.items():
        value = settings.get(key, options[0])
        if value not in options:
            raise ValueError(
                f"Modality '{name}': {key} must be one of {options}, got {value!r}."
            )


def validate_msi_settings(name: str, settings: Mapping[str, object]) -> None:
    """Reject removed MSI settings and normalizations (with the reason), unknown presets and bad tolerances."""
    removed = [key for key in settings if key in MSI_REMOVED_SETTINGS]
    if removed:
        reasons = ", ".join(f"'{key}' ({MSI_REMOVED_SETTINGS[key]})" for key in removed)
        raise ValueError(
            f"Modality '{name}': these MSI settings were removed: {reasons}. "
            "Foreground detection, feature selection and binning are now calibrated from "
            "'sample_type' and 'mass_tolerance'."
        )
    normalization = settings.get(
        MsiPreprocessingParams.INTENSITY_NORMALIZATION, MsiIntensityNormalization.NONE
    )
    if normalization in MSI_REMOVED_NORMALIZATIONS:
        raise ValueError(
            f"Modality '{name}': intensity_normalization {normalization!r} was removed "
            f"(use {MSI_REMOVED_NORMALIZATIONS[str(normalization)]!r}; apply log or CLR "
            "transforms downstream)."
        )
    choices = {
        MsiPreprocessingParams.INTENSITY_NORMALIZATION: MsiIntensityNormalization.list(),
        MsiPreprocessingParams.SAMPLE_TYPE: MsiSampleType.list(),
    }
    for key, options in choices.items():
        value = settings.get(key, options[0])
        if value not in options:
            raise ValueError(
                f"Modality '{name}': {key} must be one of {options}, got {value!r}."
            )
    tolerance = settings.get(MsiPreprocessingParams.MASS_TOLERANCE)
    if tolerance is not None and (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, (int, float))
        or not tolerance > 0
    ):
        raise ValueError(
            f"Modality '{name}': mass_tolerance must be a positive number of ppm, got {tolerance!r}."
        )


def validate_raman_settings(name: str, settings: Mapping[str, object]) -> None:
    """Reject removed Raman settings (with the reason), unknown normalizations and non-bool switches."""
    removed = [key for key in settings if key in RAMAN_REMOVED_SETTINGS]
    if removed:
        reasons = ", ".join(
            f"'{key}' ({RAMAN_REMOVED_SETTINGS[key]})" for key in removed
        )
        raise ValueError(
            f"Modality '{name}': these Raman settings were removed: {reasons}. "
            "Use 'denoise', 'remove_offset' and 'normalization'."
        )
    normalization = settings.get(
        RamanPreprocessingParams.NORMALIZATION, RamanNormalization.NONE
    )
    if normalization not in RamanNormalization.list():
        raise ValueError(
            f"Modality '{name}': normalization must be one of "
            f"{RamanNormalization.list()}, got {normalization!r}."
        )
    for key in (
        RamanPreprocessingParams.DENOISE,
        RamanPreprocessingParams.REMOVE_OFFSET,
    ):
        value = settings.get(key)
        if value is not None and not isinstance(value, bool):
            raise ValueError(
                f"Modality '{name}': {key} must be true or false, got {value!r}."
            )


def validate_microscopy_settings(
    name: str, settings: Mapping[str, object], registration: str
) -> None:
    """Reject removed microscopy settings (with the reason), unknown presets and bad pixel sizes."""
    removed = [key for key in settings if key in MICROSCOPY_REMOVED_SETTINGS]
    if removed:
        reasons = ", ".join(
            f"'{key}' ({MICROSCOPY_REMOVED_SETTINGS[key]})" for key in removed
        )
        raise ValueError(
            f"Modality '{name}': these microscopy settings were removed: {reasons}. "
            "Images are stored lossless; use 'image_type', 'z_projection', 'crop_to_tissue', "
            "'remove_background' and 'pixel_size'."
        )
    choices = {
        MicroscopyImageProcessingParams.IMAGE_TYPE: MicroscopyImageType.list(),
        MicroscopyImageProcessingParams.Z_PROJECTION: MicroscopyZProjection.list(),
    }
    for key, options in choices.items():
        value = settings.get(key, options[0])
        if value not in options:
            raise ValueError(
                f"Modality '{name}': {key} must be one of {options}, got {value!r}."
            )
    size = settings.get(MicroscopyImageProcessingParams.PIXEL_SIZE)
    if size is not None and (
        isinstance(size, bool) or not isinstance(size, (int, float)) or not size > 0
    ):
        raise ValueError(
            f"Modality '{name}': pixel_size must be a positive number of µm per pixel, got {size!r}."
        )
    image_type = settings.get(MicroscopyImageProcessingParams.IMAGE_TYPE)
    if (
        image_type == MicroscopyImageType.FLUORESCENCE
        and registration == RegistrationType.FEATURE_EXTRACTION
    ):
        raise ValueError(
            f"Modality '{name}': feature_extraction uses an H&E-pretrained encoder and does not "
            "apply to fluorescence images; use registration_type 'none'."
        )


def validate_registration(config: PipelineConfig) -> None:
    """Check registration strategies against their modality types."""

    # --- Step 9: Registration type compatibility ---
    for modality in config[ConfigParameters.MODALITIES]:
        reg_type = modality[ModalityParameters.REGISTRATION_TYPE]
        if reg_type in REGISTRATION_REMOVED_TYPES:
            raise ValueError(
                f"Modality '{modality[ModalityParameters.NAME]}': registration type "
                f"'{reg_type}' was removed (use '{REGISTRATION_REMOVED_TYPES[reg_type]}'). "
                "One overlap-area method now registers spot modalities of any relative size."
            )
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
