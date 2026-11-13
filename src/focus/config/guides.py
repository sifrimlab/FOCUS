"""Validation of ``alignment_guide``: aligning a modality through another instead of the reference."""

from focus.config.catalog import SPOT_MODALITY_TYPES
from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.options import AlignmentStrategy
from focus.config.types import PipelineConfig


def validate_alignment_guides(config: PipelineConfig, ref_name: str) -> None:
    """
    Check ``alignment_guide``: the modality a target is aligned to instead of the reference.

    A guided target ``T`` with guide ``G`` is aligned interactively onto ``G``; the reference
    reaches ``T`` through its own alignment onto ``G``. So ``G`` must be another non-reference
    modality aligned directly to the reference, and ``T`` must be a spot-based modality aligned
    manually (its spots are the moving layer).
    """
    modalities = {
        m[ModalityParameters.NAME]: m for m in config[ConfigParameters.MODALITIES]
    }
    for name, modality in modalities.items():
        guide = modality.get(ModalityParameters.ALIGNMENT_GUIDE)
        if guide is None or guide == "":
            continue
        where = f"Modality '{name}' has alignment_guide '{guide}'"
        if not config[ConfigParameters.PERFORM_ALIGNMENT]:
            continue
        if name == ref_name:
            raise ValueError(f"{where}, but the reference modality is not aligned.")
        if guide == name:
            raise ValueError(f"{where}: a modality cannot guide its own alignment.")
        if guide == ref_name:
            raise ValueError(
                f"{where}, which is the reference: leave alignment_guide empty to align "
                f"to the reference directly."
            )
        if guide not in modalities:
            raise ValueError(f"{where}, which is not a declared modality.")
        if modalities[guide].get(ModalityParameters.ALIGNMENT_GUIDE):
            raise ValueError(
                f"{where}, which is itself guided: the guide must be aligned directly to "
                f"the reference."
            )
        if modality[ModalityParameters.ALIGNMENT_STRATEGY] != AlignmentStrategy.MANUAL:
            raise ValueError(
                f"{where}: a guided modality must use the 'manual' strategy."
            )
        if modality[ModalityParameters.TYPE] not in SPOT_MODALITY_TYPES:
            raise ValueError(
                f"{where}, but only spot-based modalities ({', '.join(SPOT_MODALITY_TYPES)}) "
                f"can be guided: their spots are the moving layer of the guided alignment."
            )
