"""Select and execute the configured registration strategy for each target modality."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.pipeline.registration_request import RegistrationRequest

if TYPE_CHECKING:
    from focus.registration.feature_extraction import FeatureExtractorRegistration

from focus.registration.types import TargetRegistrationFactory

from focus.pipeline.types import ModalityFiles

from focus.config.types import PipelineConfig
from focus.runtime.reporting import StepReporter


from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import RegistrationType
from focus.config.catalog import DISPLAY_NAMES
from focus.runtime.resources import release_memory
from focus.pipeline.context import _get_reference_modality, registration_targets


def _run_registration(
    config: PipelineConfig,
    modality_files: ModalityFiles,
    aligned_files: ModalityFiles,
    step_reporter: StepReporter,
    force_overrides: dict[str, bool] | None = None,
) -> ModalityFiles:
    """
    Register each non-reference modality that has a registration_type != 'none'.

    Every engine evaluates the anchor spot footprints in the anchor frame, mapping the target
    through the inverse of the alignment transform, and reuses a per-sample output only when
    its stored fingerprint matches the current inputs.

    For FeatureExtraction (image modalities):
            One Prov-GigaPath CLS embedding per anchor spot, restricted to the image tokens inside
            its footprint.
    For SpotInterpolation (spot modalities):
            Gaussian-weighted average of the target spots inside each anchor footprint.
    For SpotAggregation (spot modalities):
            Sum of the target spots inside each anchor footprint.
    For RamanPixelInterpolation (raman modality):
            Gaussian-weighted average of the Raman OME-TIFF pixels inside each anchor footprint.

    Returns
    -------
    dict
            {modality_name: {sample_id: registered_file_path}}
    """
    dataset_path = config[ConfigParameters.DATASET_PATH]
    modalities = config[ConfigParameters.MODALITIES]
    ref_name = config[ConfigParameters.REFERENCE_MODALITY]

    registered_files: dict[str, dict[str, str]] = {}

    targets = registration_targets(modalities, ref_name)
    for reg_idx, modality in enumerate(targets, 1):
        mod_name = modality[ModalityParameters.NAME]
        reg_type = modality[ModalityParameters.REGISTRATION_TYPE]

        reg_settings = modality[ModalityParameters.REGISTRATION_SETTINGS]
        with step_reporter.modality(mod_name, reg_idx, len(targets)):
            step_reporter.detail(f"Method: {DISPLAY_NAMES[reg_type]}")
            if not _register_modality(
                RegistrationRequest(
                    reg_type,
                    mod_name,
                    ref_name,
                    dataset_path,
                    config,
                    reg_settings,
                    modality_files,
                    aligned_files,
                    registered_files,
                    step_reporter,
                    force_overrides,
                )
            ):
                continue

        # The engine (and, for FeatureExtraction, the pretrained GigaPath model it loaded)
        # went out of scope with _register_modality: free it and any cached GPU blocks
        # before the next modality / stage.
        release_memory(gpu=True)

    return registered_files


def _register_modality(request: RegistrationRequest) -> bool:
    """Run one modality's registration engine; False when the type is unknown."""
    # Imported here (not at module top) so that importing this module to run only the
    # alignment stage does not load torch / timm / huggingface_hub. See the import note
    # at the top of this file.
    from focus.registration.feature_extraction import FeatureExtractorRegistration
    from focus.registration.spot_interpolation import SpotInterpolationRegistration
    from focus.registration.spot_aggregation import SpotAggregationRegistration
    from focus.registration.raman_pixel import RamanPixelInterpolationRegistration

    target_strategies: dict[str, TargetRegistrationFactory] = {
        RegistrationType.SPOT_INTERPOLATION: SpotInterpolationRegistration,
        RegistrationType.SPOT_AGGREGATION: SpotAggregationRegistration,
        RegistrationType.RAMAN_PIXEL_INTERPOLATION: RamanPixelInterpolationRegistration,
    }
    if request.kind == RegistrationType.FEATURE_EXTRACTION:
        register_image(request, FeatureExtractorRegistration)

    elif request.kind in target_strategies:
        target_engine = target_strategies[request.kind](path=request.path)
        request.outputs[request.modality] = target_engine.register_dataset(
            anchor_files=request.aligned[request.modality],
            target_files=request.inputs[request.modality],
            anchor_name=request.reference,
            target_name=request.modality,
            force_recomputing=request.force,
            step_reporter=request.reporter,
        )

    else:
        request.reporter.warning(
            f"Unknown registration type '{request.kind}', skipping"
        )
        return False

    return True


def register_image(
    request: RegistrationRequest, engine_class: type[FeatureExtractorRegistration]
) -> None:
    """Dispatch image registration with its original keyword contract and warning order."""
    engine = engine_class(
        path=request.path, hf_token=request.config[ConfigParameters.HUGGINGFACE_TOKEN]
    )
    # aligned_files[mod_name] contains the aligned reference AnnData, which holds
    # obsm['spatial'] (the reference frame) and obsm['{mod_name}_spatial'] (the same spots
    # in the image's pixel frame); the footprints are mapped between the two.
    if "patch_size" in request.settings:
        request.reporter.warning(
            "patch_size is ignored: the encoder window is fixed by the model"
        )
    request.outputs[request.modality] = engine.register_dataset(
        image_files=request.inputs[request.modality],
        anchor_files=request.aligned[request.modality],
        image_name=request.modality,
        anchor_name=request.reference,
        anchor_type=_get_reference_modality(request.config)[ModalityParameters.TYPE],
        force_recomputing=request.force,
        background_color=request.settings.get("background_color", None),
        step_reporter=request.reporter,
    )
