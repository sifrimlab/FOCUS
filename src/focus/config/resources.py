"""Resources checks in the established configuration-validation sequence."""

from focus.config.types import PipelineConfig

import os


from focus.config.keys import (
    ConfigParameters,
    ModalityParameters,
    MsiPreprocessingParams,
)
from focus.config.options import ModalityType, RegistrationType, MsiIonMode
from focus.io.discovery import find_imzml_pair


def validate_samples(dataset_path: str, sample_ids: list[str]) -> None:
    """Require at least one discovered sample."""
    if len(sample_ids) == 0:
        raise ValueError(f"No sample directories found in '{dataset_path}'.")


def validate_raw_inputs(
    config: PipelineConfig,
    dataset_path: str,
    sample_ids: list[str],
    require_raw_inputs: bool,
) -> None:
    """Check raw directories and acquired MSI file pairs when requested."""

    # Raw per-(sample, modality) input directories and the MSI lipid database are
    # consumed only by preprocessing; an alignment-only run from preprocessed files
    # needs neither, so skip these existence checks when require_raw_inputs is False.
    if require_raw_inputs:
        for modality in config[ConfigParameters.MODALITIES]:
            mod_name = modality[ModalityParameters.NAME]
            for sample_id in sample_ids:
                sample_modality_dir = os.path.join(dataset_path, sample_id, mod_name)
                if not os.path.isdir(sample_modality_dir):
                    raise FileNotFoundError(
                        f"Missing modality directory for sample '{sample_id}': {sample_modality_dir}"
                    )

            # Check modality-specific support files
            mod_type = modality[ModalityParameters.TYPE]
            settings = modality[ModalityParameters.PROCESSING_SETTINGS]

            if mod_type == ModalityType.MSI:
                lipid_db = settings.get(MsiPreprocessingParams.LIPID_ANNOTATION_DB)
                if lipid_db is not None and not os.path.isfile(lipid_db):
                    raise FileNotFoundError(
                        f"Lipid annotation database not found for modality '{mod_name}': {lipid_db}"
                    )

                # MSI input lives in pos/ and/or neg/ ion mode subdirectories. The GUI creates both
                # for every sample, so validate the imzML/IBD pairs rather than the directories:
                # a sample needs a complete pair in at least one ion mode.
                for sample_id in sample_ids:
                    mod_dir = os.path.join(dataset_path, sample_id, mod_name)
                    # Materialise the list rather than using a generator with any(): short-circuiting
                    # on the first hit would skip the incomplete-pair check on the other ion mode.
                    pairs = [
                        find_imzml_pair(os.path.join(mod_dir, mode))
                        for mode in MsiIonMode.list()
                    ]
                    if not any(pairs):
                        raise FileNotFoundError(
                            f"No complete .imzML + .ibd file pair found for sample '{sample_id}' in "
                            f"'{mod_dir}'. Expected one in at least one of the ion mode "
                            f"subdirectories: {MsiIonMode.list()}."
                        )


def validate_token(config: PipelineConfig, require_raw_inputs: bool) -> None:
    """Apply the existing model-token requirement only for full runs."""

    # --- Step 10: HuggingFace token requirement ---
    # The token is only used to download the registration feature extractor; an
    # alignment-only run never registers, so skip the requirement when
    # require_raw_inputs is False.
    if require_raw_inputs:
        needs_hf_token = any(
            m[ModalityParameters.REGISTRATION_TYPE]
            == RegistrationType.FEATURE_EXTRACTION
            for m in config[ConfigParameters.MODALITIES]
        )
        if needs_hf_token:
            hf_token = config[ConfigParameters.HUGGINGFACE_TOKEN]
            if not hf_token or not isinstance(hf_token, str):
                raise ValueError(
                    "'huggingface_token' is required when any modality uses 'feature_extraction' registration."
                )
