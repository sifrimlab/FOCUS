"""Structure checks in the established configuration-validation sequence."""

from focus.config.types import RegistrationSettings

from focus.config.types import PipelineConfig
from focus.config.checks import _require_key, _check_type


from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import ModalityType, RegistrationType, AlignmentStrategy


def require_fields(config: PipelineConfig) -> None:
    """Check required top-level fields before applying defaults."""

    # --- Step 2: Required top-level keys and types ---
    _require_key(config, ConfigParameters.DATASET_PATH, str)
    _require_key(config, ConfigParameters.MODALITIES, list)
    _require_key(config, ConfigParameters.REFERENCE_MODALITY, str)

    if len(config[ConfigParameters.MODALITIES]) == 0:
        raise ValueError("'modalities' must contain at least one entry.")


def apply_defaults(config: PipelineConfig) -> list[str]:
    """Apply existing optional defaults and validate their types."""

    # Apply defaults for optional top-level keys
    config.setdefault(ConfigParameters.PERFORM_ALIGNMENT, True)
    config.setdefault(ConfigParameters.PERFORM_REGISTRATION, True)
    config.setdefault(ConfigParameters.HUGGINGFACE_TOKEN, None)
    config.setdefault(ConfigParameters.IGNORE_SAMPLES, [])
    config.setdefault(ConfigParameters.SAMPLES, [])
    config.setdefault(ConfigParameters.LAST_EDIT, None)

    _check_type(config, ConfigParameters.PERFORM_ALIGNMENT, bool)
    _check_type(config, ConfigParameters.PERFORM_REGISTRATION, bool)
    if config[ConfigParameters.HUGGINGFACE_TOKEN] is not None:
        _check_type(config, ConfigParameters.HUGGINGFACE_TOKEN, str)

    ignore_samples = config[ConfigParameters.IGNORE_SAMPLES]
    if not isinstance(ignore_samples, list) or not all(
        isinstance(s, str) for s in ignore_samples
    ):
        raise TypeError("'ignore_samples' must be a list of strings.")

    samples = config[ConfigParameters.SAMPLES]
    if not isinstance(samples, list) or not all(isinstance(s, str) for s in samples):
        raise TypeError("'samples' must be a list of strings.")

    if config[ConfigParameters.LAST_EDIT] is not None and not isinstance(
        config[ConfigParameters.LAST_EDIT], str
    ):
        raise TypeError("'last_edit' must be a string or null.")
    return ignore_samples


def validate_modalities(config: PipelineConfig) -> None:
    """Validate modality declarations and apply their existing defaults."""

    # --- Step 4: Per-modality validation ---
    for i, modality in enumerate(config[ConfigParameters.MODALITIES]):
        if not isinstance(modality, dict):
            raise TypeError(f"Modality at index {i} must be a dictionary.")

        _require_key(modality, ModalityParameters.NAME, str, context=f"modality[{i}]")
        _require_key(modality, ModalityParameters.TYPE, str, context=f"modality[{i}]")
        _require_key(
            modality,
            ModalityParameters.PROCESSING_SETTINGS,
            dict,
            context=f"modality[{i}]",
        )

        mod_type = modality[ModalityParameters.TYPE]
        if mod_type not in ModalityType.list():
            raise ValueError(
                f"Unsupported modality type '{mod_type}' for modality '{modality[ModalityParameters.NAME]}'. "
                f"Supported types: {ModalityType.list()}"
            )

        # Apply defaults for optional modality keys
        modality.setdefault(ModalityParameters.REGISTRATION_TYPE, RegistrationType.NONE)
        modality.setdefault(
            ModalityParameters.REGISTRATION_SETTINGS, RegistrationSettings()
        )
        modality.setdefault(
            ModalityParameters.ALIGNMENT_STRATEGY, AlignmentStrategy.MANUAL
        )
        modality.setdefault(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False)


def validate_names(config: PipelineConfig) -> list[str]:
    """Check unique modality names and the declared reference."""

    # --- Step 5: Unique modality names ---
    names = [m[ModalityParameters.NAME] for m in config[ConfigParameters.MODALITIES]]
    if len(names) != len(set(names)):
        duplicates = [n for n in names if names.count(n) > 1]
        raise ValueError(f"Duplicate modality names: {set(duplicates)}")

    # --- Step 6: reference_modality must match one modality's name ---
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]
    if ref_name not in names:
        raise ValueError(
            f"Reference modality '{ref_name}' not found in declared modalities: {names}"
        )
    return names


def validate_stage_dependencies(config: PipelineConfig) -> None:
    """Require alignment when registration is enabled."""

    # --- Step 7: Logical constraint ---
    if (
        config[ConfigParameters.PERFORM_REGISTRATION]
        and not config[ConfigParameters.PERFORM_ALIGNMENT]
    ):
        raise ValueError(
            "'perform_registration' requires 'perform_alignment' to be true. "
            "Registration depends on aligned coordinates."
        )
