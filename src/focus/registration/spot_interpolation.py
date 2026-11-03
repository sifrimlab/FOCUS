"""Gaussian-weighted interpolation of target spot features onto anchor footprints."""

from functools import partial

from focus.registration.types import RegistrationJob

from focus.registration.anchor import AnchorArrays
from focus.spatial.footprint import AnchorGeometry
from anndata import AnnData

from focus.runtime.reporting import StepReporter

import anndata
import numpy as np

from focus.config.options import RegistrationType
from focus.registration.execution import register_target
from focus.registration.targets import read_target, target_sources
from focus.spatial.membership import footprint_membership, row_normalize
from focus.runtime.reporter_context import get_reporter
from focus.registration.matrix import _dense
from focus.registration.logging import logger


class SpotInterpolationRegistration:
    """
    Register a spot-based modality to the anchor by Gaussian-weighted interpolation.

    For each anchor spot, the target spots whose centres lie inside the anchor's footprint are
    averaged with weights ``exp(-d^2 / (2 sigma^2))``, ``sigma = sqrt(sx * sy) / 2``. The
    footprint and the distance ``d`` live in the anchor frame, where ``spot_size`` is defined:
    target positions are mapped back through the inverse of the alignment transform.

    Coordinate convention (after alignment):
    - ``anchor_files`` contains the aligned reference AnnData: ``obsm['spatial']`` (anchor
      frame) and ``obsm['{target_name}_spatial']`` (the same spots in the target frame).
    - ``target_files`` contains the preprocessed target AnnData; its ``obsm['spatial']`` is in
      the target frame.

    Parameters
    ----------
    path : str
            The path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.SPOT_INTERPOLATION

    def __init__(self, path: str) -> None:
        """Retain the dataset root; registration reads source files when requested.

        Parameters
        ----------
        path : str
            Dataset root for sample and merged registration outputs."""
        self._path = path

    def _compute_sample(
        self,
        target_files: dict[str, str],
        sample_id: str,
        anchor: AnchorArrays,
        geom: AnchorGeometry,
    ) -> tuple[AnnData | None, dict[str, object]]:
        """Compute one sample's registered features and geometry metadata."""
        target_adata = read_target(target_files, sample_id)
        target_coords = np.asarray(target_adata.obsm["spatial"], dtype=np.float64)
        weights = row_normalize(
            footprint_membership(geom, target_coords, weights="gaussian")
        )
        logger.debug(
            f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
            f"Target: {target_coords.shape[0]} spots, {target_adata.n_vars} features."
        )
        # The same operator interpolates .X and every layer (same spots, different payloads).
        adata = anndata.AnnData(
            X=_dense(weights @ target_adata.X).astype(np.float32),
            obsm={"spatial": anchor.mapped.copy()},
            obs={"sample_id": [sample_id] * anchor.n_obs},
            layers={
                k: _dense(weights @ v).astype(np.float32)
                for k, v in target_adata.layers.items()
            }
            or None,
        )
        # Carry over var metadata from target (gene names, m/z values, etc.)
        adata.var = target_adata.var.copy()
        n_empty = int((np.diff(weights.indptr) == 0).sum())
        if n_empty:
            logger.debug(
                f"{n_empty}/{anchor.n_obs} anchor spots had no target spots in their footprint"
            )
        return adata, {}

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter: StepReporter | None = None,
    ) -> dict[str, str]:
        """
        Register a spot-based target modality to the anchor using Gaussian interpolation.

        Parameters
        ----------
        anchor_files : dict[str, str]
                Aligned anchor (reference) modality files. {sample_id: h5ad_path}
        target_files : dict[str, str]
                Preprocessed target (non-reference) modality files. {sample_id: h5ad_path}; each
                sample is read from its rows of the "merged" entry when that file exists.
        anchor_name : str
                Name of the anchor (reference) modality.
        target_name : str
                Name of the target modality being registered.
        force_recomputing : bool
                Recompute even when the stored fingerprint matches.
        step_reporter : StepReporter, optional
                If provided, reports per-sample progress to the GUI.

        Returns
        -------
        dict[str, str]
                {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        common_samples = sorted(
            set(anchor_files.keys()) & set(target_files.keys()) - {"merged"}
        )
        reporter = step_reporter or get_reporter()
        sources = target_sources(target_files)

        return register_target(
            RegistrationJob(
                path=self._path,
                samples=reporter.tqdm(
                    common_samples,
                    1,
                    2,
                    "Interpolating features onto anchor spots",
                    unit="sample",
                ),
                anchor_files=anchor_files,
                target_files=sources,
                anchor_name=anchor_name,
                target_name=target_name,
                registration_type=self._REGISTRATION_TYPE,
                settings={},
                compute=partial(self._compute_sample, sources),
                force_recomputing=force_recomputing,
                reporter=reporter,
            ),
            reporter,
        )
