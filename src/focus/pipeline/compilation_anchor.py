"""Resolve and load the anchor data used by multimodal compilation."""

from dataclasses import dataclass
import os

import anndata
import numpy as np
import pandas as pd

from focus.config.keys import ConfigParameters, ModalityParameters
from focus.config.types import PipelineConfig
from focus.pipeline.context import _SPOT_MODALITIES, _get_reference_modality
from focus.pipeline.types import ModalityFiles
from focus.runtime.reporting import StepReporter


@dataclass
class CompilationAnchor:
    """Shared axes and loaded modalities; retain the original anchor until return."""

    anchor: anndata.AnnData
    modalities: dict[str, anndata.AnnData]
    spatial: np.ndarray
    sample_labels: pd.Series
    spot_size: object
    n_obs: int
    sample_ids: np.ndarray


def anchor_path(
    config: PipelineConfig,
    files: ModalityFiles,
    annotations: dict[str, str] | None,
    reporter: StepReporter,
) -> str | None:
    """Prefer annotated anchor data, retaining the original skip checks and warnings."""
    name = config[ConfigParameters.REFERENCE_MODALITY]
    reference = _get_reference_modality(config)
    if reference[ModalityParameters.TYPE] not in _SPOT_MODALITIES:
        reporter.warning(
            "The reference modality is not spot-based: the multimodal dataset is not compiled"
        )
        return None
    path = (
        annotations["merged"]
        if annotations and "merged" in annotations
        else files[name].get("merged")
    )
    if path is None or not os.path.exists(path):
        reporter.warning(
            f"No merged file for the reference '{name}': the multimodal dataset is not compiled"
        )
        return None
    return path


def load_anchor(path: str, name: str) -> CompilationAnchor:
    """Capture shared axes before removing their duplicated modality-level fields.

    Parameters
    ----------
    path, name
        Existing merged anchor H5AD and its configured modality name.

    Returns
    -------
    CompilationAnchor
        Loaded anchor and shared axes. Coordinates retain the original float32
        conversion; sample labels are copied. The loaded AnnData is modified in place.
    """
    anchor = anndata.read_h5ad(path)
    spatial = np.asarray(anchor.obsm["spatial"], dtype=np.float32)
    labels = anchor.obs["sample_id"].copy()
    spot_size = anchor.uns.get("spot_size", None)
    n_obs = anchor.n_obs
    sample_ids = anchor.obs["sample_id"].astype(str).to_numpy()
    modalities: dict[str, anndata.AnnData] = {}
    if "spatial" in anchor.obsm:
        del anchor.obsm["spatial"]
    if "spot_size" in anchor.uns:
        del anchor.uns["spot_size"]
    modalities[name] = anchor
    return CompilationAnchor(
        anchor, modalities, spatial, labels, spot_size, n_obs, sample_ids
    )
