import logging

import anndata
import numpy as np
import scipy.sparse

from focus.constants import RegistrationType
from focus.registration._cache import register_samples, merge_registered_samples
from focus.registration._footprint import footprint_membership
from focus.reporting import get_reporter

logger = logging.getLogger(__name__)


def _as_csr(matrix):
    """Return ``matrix`` as a CSR sparse matrix (no-op when already sparse)."""
    if scipy.sparse.issparse(matrix):
        return matrix.tocsr()
    return scipy.sparse.csr_matrix(matrix)


class SpotAggregationRegistration:
    """
    Register a spot-based modality to the anchor by summing target spots per footprint.

    Same footprint as :class:`SpotInterpolationRegistration`, but the per-anchor reduction is
    a plain **sum** of every target spot whose centre lies inside the anchor spot's footprint
    instead of a Gaussian-weighted average. This *accumulates* the signal coming from the area
    covered by one anchor spot, which is the desired behaviour for subcellular-resolution
    modalities (e.g. VisiumHD): each native spot carries very little signal, so averaging
    would dilute it whereas summing boosts it.

    No normalization is applied. ``X`` and every layer are aggregated the same way (a plain
    sum), and the summed values are kept as-is: deliberately *not* rescaled by how many
    target spots fell within the footprint, since dividing by occupancy would collapse the
    result back towards the averaging behaviour of ``spot_interpolation``.

    The footprint is evaluated in the anchor frame, where ``spot_size`` is defined: target
    positions are mapped back through the inverse of the alignment transform.

    Parameters
    ----------
    path : str
        The path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.SPOT_AGGREGATION

    def __init__(self, path: str) -> None:
        self._path = path

    def register_dataset(
        self,
        anchor_files: dict[str, str],
        target_files: dict[str, str],
        anchor_name: str,
        target_name: str,
        force_recomputing: bool = False,
        step_reporter=None,
    ) -> dict[str, str]:
        """
        Register a spot-based target modality to the anchor by summing per footprint.

        Parameters
        ----------
        anchor_files : dict[str, str]
            Aligned anchor (reference) modality files, with ``obsm['spatial']`` (anchor frame),
            ``obsm['{target_name}_spatial']`` (target frame) and ``uns['spot_size']``.
            {sample_id: h5ad_path}
        target_files : dict[str, str]
            Preprocessed target (non-reference) modality files, with ``obsm['spatial']`` in
            the target frame and the feature matrix ``X``. {sample_id: h5ad_path}
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
        common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
        reporter = step_reporter or get_reporter()

        def compute(sample_id, anchor, geom):
            target_adata = anndata.read_h5ad(target_files[sample_id])
            target_coords = np.asarray(target_adata.obsm['spatial'], dtype=np.float64)
            logger.debug(
                f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
                f"Target: {target_coords.shape[0]} spots, {target_adata.n_vars} features."
            )
            # A @ payload sums the rows of every target spot inside each anchor footprint
            # (sparse-preserving); empty footprints become all-zero rows, dropped downstream by
            # the coverage mask. The same operator is applied to .X and every layer.
            membership = footprint_membership(geom, target_coords, weights="ones")
            adata = anndata.AnnData(
                X=_as_csr(membership @ _as_csr(target_adata.X)),
                obsm={'spatial': anchor.mapped.copy()},
                obs={'sample_id': [sample_id] * anchor.n_obs},
                layers={k: _as_csr(membership @ _as_csr(v)) for k, v in target_adata.layers.items()} or None,
            )
            # Carry over var metadata from target (gene names, m/z values, etc.)
            adata.var = target_adata.var.copy()
            return adata, {}

        registered_files = register_samples(
            path=self._path,
            samples=reporter.tqdm(common_samples, 1, 2, "Aggregating target spots per anchor footprint", unit="sample"),
            anchor_files=anchor_files, target_files=target_files,
            anchor_name=anchor_name, target_name=target_name,
            registration_type=self._REGISTRATION_TYPE, settings={}, compute=compute,
            force_recomputing=force_recomputing, reporter=reporter,
        )
        reporter.step(2, 2, "Merging samples")
        return merge_registered_samples(
            self._path, registered_files, target_name, self._REGISTRATION_TYPE, force_recomputing=force_recomputing,
        )
