"""Propagate preprocessing and alignment changes to dependent cached stages."""

from focus.config.types import PipelineConfig


from focus.config.keys import ConfigParameters, ModalityParameters

from focus.pipeline.context import _get_reference_modality


def _compute_effective_force_flags(
    config: PipelineConfig, changed_preprocessing: set[str] | None = None
) -> tuple[dict[str, bool], dict[str, bool]]:
    """
    Derive effective force_recomputing flags for alignment and registration by cascading
    upstream flags downstream through the pipeline dependency chain.

    Cascade rules (OR-combined per modality):
    - reference modality preprocessing forced → force ALL alignment pairs + ALL registrations
    - target modality preprocessing forced    → force THAT pair's alignment + THAT registration
    - alignment forced (any cause above)      → force THAT registration

    Returns
    -------
    alignment_force : dict[str, bool]
            Effective alignment force per non-reference modality name.
    registration_force : dict[str, bool]
            Effective registration force per non-reference modality name.
    """
    changed_preprocessing = changed_preprocessing or set()
    ref_mod = _get_reference_modality(config)
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]
    ref_preproc_force: bool = ref_mod[ModalityParameters.PROCESSING_SETTINGS].get(
        "force_recomputing", False
    )

    alignment_force: dict[str, bool] = {}
    registration_force: dict[str, bool] = {}

    for modality in config[ConfigParameters.MODALITIES]:
        mod_name = modality[ModalityParameters.NAME]
        if mod_name == ref_name:
            continue

        tgt_preproc_force: bool = modality[ModalityParameters.PROCESSING_SETTINGS].get(
            "force_recomputing", False
        )

        effective_align = (
            modality.get(ModalityParameters.ALIGNMENT_FORCE_RECOMPUTING, False)
            or ref_preproc_force
            or ref_name in changed_preprocessing
            or tgt_preproc_force
            or mod_name in changed_preprocessing
        )
        alignment_force[mod_name] = effective_align

        explicit_reg_force: bool = modality.get(
            ModalityParameters.REGISTRATION_SETTINGS, {}
        ).get("force_recomputing", False)
        registration_force[mod_name] = explicit_reg_force or effective_align

    return alignment_force, registration_force
