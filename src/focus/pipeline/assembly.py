"""Registered modality loading and scientific dataset assembly."""

from __future__ import annotations
from focus.pipeline.compilation_anchor import CompilationAnchor
from focus.spatial.matrices import SparseStorage
from typing import cast
from focus.config.types import ModalityConfig
from focus.pipeline.types import ModalityFiles

import os
import anndata
import numpy as np
import scipy.sparse
import pandas as pd
import mudata

from focus.config.keys import ModalityParameters
from focus.io.paths import multimodal_file
from focus.io.mudata import write_h5mu_compat
from focus.runtime.reporting import StepReporter
from focus.pipeline.context import logger


def load_registered(
    modalities: list[ModalityConfig],
    registered_files: ModalityFiles,
    ref_name: str,
    anchor: CompilationAnchor,
    reporter: StepReporter,
) -> None:
    """Load compatible registered modalities without changing anchor row order."""

    # Add registered non-anchor modalities. We do NOT overwrite obs_names yet —
    # obs_name synchronisation happens after the zero-vector filter so the
    # anchor's filtered slice is what gets propagated.
    for modality in modalities:
        mod_name = modality[ModalityParameters.NAME]
        if mod_name == ref_name:
            continue

        if mod_name not in registered_files:
            logger.debug(
                f"No registration output for '{mod_name}', skipping in MuData."
            )
            continue

        merged_path = registered_files[mod_name].get("merged")
        if merged_path is None or not os.path.exists(merged_path):
            reporter.warning(
                f"'{mod_name}' has no merged registration file and is left out"
            )
            continue

        registered = read_registered(merged_path, mod_name, ref_name, anchor, reporter)
        if registered is not None:
            anchor.modalities[mod_name] = registered


def filter_coverage(
    mod_dict: dict[str, anndata.AnnData],
    ref_name: str,
    n_anchor_obs: int,
    shared_spatial: np.ndarray,
    shared_sample_id: pd.Series,
    reporter: StepReporter,
) -> tuple[dict[str, anndata.AnnData], np.ndarray, pd.Series, int] | None:
    """Filter uncovered anchor rows consistently across modalities."""

    # Filter anchor spots with no coverage in any target modality
    reporter.step(2, 3, "Filtering uncovered spots")
    valid_mask = _compute_valid_spot_mask(mod_dict, ref_name, n_anchor_obs)
    if valid_mask is not None:
        n_removed = int(np.sum(~valid_mask))
        reporter.detail(
            f"{n_removed}/{n_anchor_obs} reference spots removed: no coverage in at least one modality"
        )
        mod_dict = {k: v[valid_mask].copy() for k, v in mod_dict.items()}
        shared_spatial = shared_spatial[valid_mask]
        shared_sample_id = shared_sample_id[valid_mask]
        n_anchor_obs = int(np.sum(valid_mask))
        if n_anchor_obs == 0:
            reporter.warning(
                "No reference spot has coverage in all modalities: the multimodal dataset is not compiled"
            )
            return None
    return (mod_dict, shared_spatial, shared_sample_id, n_anchor_obs)


def prepare_axes(mod_dict: dict[str, anndata.AnnData], ref_name: str) -> None:
    """Synchronize observation identifiers and namespace modality feature names."""

    # Synchronise obs_names across modalities using the (possibly filtered) anchor.
    anchor_obs_names = mod_dict[ref_name].obs_names.tolist()
    for mod_name, adata in mod_dict.items():
        if mod_name == ref_name:
            continue
        adata.obs_names = anchor_obs_names

    # Namespace var_names per modality. MSI uses bare integer strings ("0", "1", ...)
    # and Raman/microscopy may carry similarly generic names, so cross-modality
    # var_name collisions are possible. Mudata's `_update_attr` takes a fragile
    # duplicates/intersection branch in that case and raises a KeyError on read.
    # Prefixing var_names with the modality name eliminates that risk and keeps
    # mudata on its simple, lossless code path on both write and read.
    for mod_name, adata in mod_dict.items():
        adata.var_names = pd.Index(
            [f"{mod_name}:{v}" for v in adata.var_names], dtype=object
        )
        adata.var_names_make_unique()

    logger.debug(
        "Compiling MuData: "
        + ", ".join(
            f"{k}: n_obs={v.n_obs} n_vars={v.n_vars} "
            f"obs_unique={v.obs_names.is_unique} var_unique={v.var_names.is_unique}"
            for k, v in mod_dict.items()
        )
    )


def write_multimodal(
    anchor: CompilationAnchor,
    ref_name: str,
    dataset_path: str,
    reporter: StepReporter,
) -> str:
    """Construct and write the final multimodal scientific container."""

    # Build MuData
    reporter.step(3, 3, "Writing MuData")
    mdata = mudata.MuData(anchor.modalities)

    mdata.obs["sample_id"] = anchor.sample_labels.values
    mdata.obsm["spatial"] = anchor.spatial
    if anchor.spot_size is not None:
        mdata.uns["spot_size"] = anchor.spot_size

    # Propagate spatial annotations (if present) to top-level mdata.obs
    if "spatial_annotation" in anchor.modalities[ref_name].obs.columns:
        mdata.obs["spatial_annotation"] = (
            anchor.modalities[ref_name].obs["spatial_annotation"].values
        )
        logger.debug(
            "Spatial annotation labels promoted to mdata.obs['spatial_annotation']"
        )

    output_path = multimodal_file(dataset_path, "h5mu")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_h5mu_compat(mdata, output_path)
    reporter.detail(
        f"Saved {output_path}: {len(anchor.modalities)} modalities, {anchor.n_obs} observations"
    )

    # Drop the compiled MuData and every modality AnnData it holds (anchor + all registered
    # modalities are resident at once here) before returning to the orchestrator.
    del mdata
    return output_path


def _compute_valid_spot_mask(
    mod_dict: dict[str, anndata.AnnData],
    ref_name: str,
    n_obs: int,
) -> np.ndarray | None:
    """
    Returns a bool mask (n_obs,) — True where a spot has non-zero coverage in
    every non-anchor modality. Returns None when no filtering is needed.

    Zero-vector rows arise when an anchor spot's footprint holds no target data:
    - SpotInterpolation / SpotAggregation / RamanPixelInterpolation: no target spot or
      pixel centre lies inside the footprint (the spot is outside the target tissue).
    - FeatureExtraction: every image token of the footprint is background
      (>= 99% background pixels), or the footprint lies outside the image.
    - Any engine: the spot cannot be placed in the target frame.
    """
    mask = np.ones(n_obs, dtype=bool)
    any_empty = False

    for mod_name, adata in mod_dict.items():
        if mod_name == ref_name:
            continue
        X = adata.X
        if scipy.sparse.issparse(X):
            X_csr = cast(SparseStorage, X).tocsr().copy()
            X_csr.eliminate_zeros()
            mod_mask = np.asarray(X_csr.getnnz(axis=1) > 0)
        else:
            mod_mask = ~np.all(X == 0.0, axis=1)
        n_empty = int(np.sum(~mod_mask))
        if n_empty > 0:
            logger.debug(
                f"Coverage check '{mod_name}': {n_empty}/{n_obs} uncovered spots (all-zero features)"
            )
            any_empty = True
        mask &= mod_mask

    return mask if any_empty else None


def filter_anchor(anchor: CompilationAnchor, name: str, reporter: StepReporter) -> bool:
    """Replace shared axes and modalities together; return False for empty coverage."""
    filtered = filter_coverage(
        anchor.modalities,
        name,
        anchor.n_obs,
        anchor.spatial,
        anchor.sample_labels,
        reporter,
    )
    if filtered is None:
        return False
    anchor.modalities, anchor.spatial, anchor.sample_labels, anchor.n_obs = filtered
    return True


def read_registered(
    merged_path: str,
    mod_name: str,
    ref_name: str,
    anchor: CompilationAnchor,
    reporter: StepReporter,
) -> anndata.AnnData | None:
    """Load a registered modality only when its rows match the anchor's sample order."""
    reg_adata = anndata.read_h5ad(merged_path)

    if reg_adata.n_obs != anchor.n_obs:
        reporter.warning(
            f"'{mod_name}' is left out: {reg_adata.n_obs} observations vs {anchor.n_obs} in the reference"
        )
        del reg_adata
        return None

    # Verify row alignment against the anchor by comparing per-row sample_id.
    # Matching counts alone is not enough: the per-sample concat order in
    # registration.merge_registered_samples can diverge from preprocessing's order, and
    # silently mis-pairing spots produces a MuData that mudata cannot read back.
    if "sample_id" not in reg_adata.obs.columns:
        reporter.warning(
            f"'{mod_name}' is left out: it has no obs['sample_id'], so its rows cannot be "
            "matched to the reference"
        )
        del reg_adata
        return None
    reg_sample_ids = reg_adata.obs["sample_id"].astype(str).to_numpy()
    if not np.array_equal(reg_sample_ids, anchor.sample_ids):
        reporter.warning(
            f"'{mod_name}' is left out: its sample order differs from the reference '{ref_name}'"
        )
        del reg_adata
        return None

    if "spatial" in reg_adata.obsm:
        del reg_adata.obsm["spatial"]
    if "spot_size" in reg_adata.uns:
        del reg_adata.uns["spot_size"]

    return reg_adata
