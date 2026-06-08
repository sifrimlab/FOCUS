import os
import logging

import anndata
import numpy as np
import scipy.sparse
from scipy.spatial import cKDTree

from focus.constants import MODALITY_REGISTRATION, MODALITY_REGISTRATION_MERGED, RegistrationType
from focus.utils import write_h5ad_compat, read_merged_sample_ids, registration_cache_valid

logger = logging.getLogger(__name__)

_H5AD_COMPRESSION = "gzip"


def _as_csr(matrix):
    """Return ``matrix`` as a CSR sparse matrix (no-op when already sparse)."""
    if scipy.sparse.issparse(matrix):
        return matrix.tocsr()
    return scipy.sparse.csr_matrix(matrix)


class SpotAggregationRegistration:
    """
    Register a spot-based modality to the anchor by summing target spots per footprint.

    Structurally identical to :class:`SpotInterpolationRegistration`, but the per-anchor
    reduction is a plain **sum** of every target spot that falls within the anchor spot's
    area instead of a Gaussian-weighted average. This *accumulates* the signal coming from
    the area covered by one anchor spot, which is the desired behaviour for subcellular-
    resolution modalities (e.g. VisiumHD): each native spot carries very little signal, so
    averaging would dilute it whereas summing boosts it.

    No normalization is applied. ``X`` and every layer are aggregated the same way (a plain
    sum), and the summed values are kept as-is — deliberately *not* rescaled by how many
    target spots fell within the footprint, since dividing by occupancy would collapse the
    result back towards the averaging behaviour of ``spot_interpolation``.

    Coordinate convention (after alignment swap):
    - ``anchor_files`` contains the aligned reference AnnData. Its
      ``obsm['{target_name}_spatial']`` holds the anchor (reference) spot positions
      expressed in the target modality's coordinate system.
    - ``target_files`` contains the preprocessed non-reference AnnData. Its
      ``obsm['spatial']`` holds the target spot positions in their native (same) space.
    - Both coordinate sets are therefore in the target modality's coordinate system,
      so spatial distances are meaningful.

    Parameters
    ----------
    path : str
        The path to the dataset folder.
    """

    _REGISTRATION_TYPE = RegistrationType.SPOT_AGGREGATION

    def __init__(self, path: str) -> None:
        self._path = path

    @staticmethod
    def _footprint_membership_matrix(
        anchor_coordinates: np.ndarray,
        anchor_spot_size: np.ndarray,
        target_coordinates: np.ndarray,
    ) -> scipy.sparse.csr_matrix:
        """
        Build the sparse anchor-vs-target membership matrix.

        For each anchor spot, find every target spot whose position falls within the anchor
        spot's rectangular footprint, then return the 0/1 assignment matrix ``A`` such that
        ``A @ features`` sums the feature rows of all member target spots into each anchor
        spot (sparse-preserving: CSR @ CSR -> CSR). Footprints may overlap, so the same
        target spot can be a member of several anchors (many-to-many).

        Parameters
        ----------
        anchor_coordinates : np.ndarray
            (N_anchor, 2) anchor spot positions expressed in the target modality's
            coordinate system [x, y].
        anchor_spot_size : np.ndarray
            (2,) spot dimensions [sx, sy] in the target modality's coordinate units.
        target_coordinates : np.ndarray
            (N_target, 2) target spot positions in their native coordinate system [x, y].
            Must be in the same coordinate space as ``anchor_coordinates``.

        Returns
        -------
        scipy.sparse.csr_matrix
            (N_anchor, N_target) 0/1 matrix; ``A[i, j] == 1`` iff target spot ``j`` lies
            within anchor spot ``i``'s footprint. Anchors with no member target spots are
            all-zero rows.
        """
        n_anchor = anchor_coordinates.shape[0]
        n_target = target_coordinates.shape[0]

        sx, sy = float(anchor_spot_size[0]), float(anchor_spot_size[1])
        half_sx, half_sy = sx / 2.0, sy / 2.0

        # Build spatial index on target coordinates for fast lookup
        tree = cKDTree(target_coordinates)

        # Search radius: diagonal of the spot rectangle
        search_radius = np.sqrt(half_sx ** 2 + half_sy ** 2)

        rows: list[np.ndarray] = []
        cols: list[np.ndarray] = []
        n_empty = 0
        for i in range(n_anchor):
            cx, cy = anchor_coordinates[i]

            # Find candidate target spots within search radius
            candidate_indices = tree.query_ball_point([cx, cy], r=search_radius)
            if not candidate_indices:
                n_empty += 1
                continue

            candidates = target_coordinates[candidate_indices]
            dx = candidates[:, 0] - cx
            dy = candidates[:, 1] - cy

            # Filter: keep only those strictly within the rectangular spot area
            within_mask = (np.abs(dx) <= half_sx) & (np.abs(dy) <= half_sy)
            valid_local = np.where(within_mask)[0]
            if valid_local.size == 0:
                n_empty += 1
                continue

            valid_global = np.asarray(candidate_indices, dtype=np.intp)[valid_local]
            rows.append(np.full(valid_global.size, i, dtype=np.intp))
            cols.append(valid_global)

        if rows:
            row_idx = np.concatenate(rows)
            col_idx = np.concatenate(cols)
            data = np.ones(row_idx.size, dtype=np.float32)
        else:
            row_idx = np.empty(0, dtype=np.intp)
            col_idx = np.empty(0, dtype=np.intp)
            data = np.empty(0, dtype=np.float32)

        if n_empty > 0:
            logger.debug(f"{n_empty}/{n_anchor} anchor spots had no target spots within range")

        return scipy.sparse.csr_matrix((data, (row_idx, col_idx)), shape=(n_anchor, n_target))

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

        For each anchor spot, finds the target spots within the anchor's spot_size area and
        sums their feature vectors. ``X`` and every layer are aggregated identically; no
        normalization is applied.

        Parameters
        ----------
        anchor_files : dict[str, str]
            Aligned anchor (reference) modality files. Each AnnData must contain:
            - ``obsm['{target_name}_spatial']``: anchor spot positions in the target
              modality's coordinate system (set during alignment).
            - ``uns['spot_size']``: anchor spot dimensions defining the neighbourhood.
            {sample_id: h5ad_path}
        target_files : dict[str, str]
            Preprocessed target (non-reference) modality files. Each AnnData must contain:
            - ``obsm['spatial']``: target spot positions in their native coordinate system
              (same space as ``obsm['{target_name}_spatial']`` in the anchor).
            - ``X``: feature matrix to aggregate.
            {sample_id: h5ad_path}
        anchor_name : str
            Name of the anchor (reference) modality.
        target_name : str
            Name of the target modality being registered.
        force_recomputing : bool
            Whether to recompute even if cached results exist.
        step_reporter : StepReporter, optional
            If provided, reports per-sample progress to the GUI.

        Returns
        -------
        dict[str, str]
            {sample_id: registered_h5ad_path, "merged": merged_h5ad_path}
        """
        common_samples = sorted(set(anchor_files.keys()) & set(target_files.keys()) - {"merged"})
        registered_files: dict[str, str] = {}
        total_samples = len(common_samples)

        all_cached = True  # tracks whether all per-sample files came from valid cache

        for sample_idx, sample_id in enumerate(common_samples, 1):
            logger.info(f"Registering '{target_name}' for sample '{sample_id}'")

            if step_reporter:
                step_reporter.set_sample(sample_id, sample_idx, total_samples)

            # Output path
            reg_dir = os.path.join(self._path, sample_id, "registration")
            os.makedirs(reg_dir, exist_ok=True)
            registered_file = MODALITY_REGISTRATION(self._path, sample_id, target_name, "h5ad")

            # Load anchor (aligned reference): spot_size and anchor coords in target's space.
            # obsm['{target_name}_spatial'] holds anchor spot positions expressed in the
            # target modality's coordinate system, set during the alignment step.
            # Also loaded here to validate cache obs count before potentially skipping recomputing.
            anchor_adata = anndata.read_h5ad(anchor_files[sample_id])

            # Cache check — validate obs count and registration mode against the anchor to
            # detect stale caches (wrong size, or a file left by a different registration mode;
            # the output path is shared with spot_interpolation, so the obs-count check alone
            # would pass for a stale file from the other mode).
            if os.path.exists(registered_file) and not force_recomputing:
                cached = anndata.read_h5ad(registered_file)
                if registration_cache_valid(cached, anchor_adata.n_obs, self._REGISTRATION_TYPE):
                    logger.info(f"Using cached registration for sample '{sample_id}'")
                    registered_files[sample_id] = registered_file
                    continue
                logger.warning(
                    f"Cached registration for '{sample_id}' is stale "
                    f"(obs={cached.n_obs} vs anchor {anchor_adata.n_obs}, "
                    f"type={cached.uns.get('registration_type')} vs {self._REGISTRATION_TYPE}); recomputing."
                )

            all_cached = False

            if 'spot_size' in anchor_adata.uns:
                spot_size = np.asarray(anchor_adata.uns['spot_size'], dtype=np.float32).flatten()
                if spot_size.size == 1:
                    spot_size = np.array([float(spot_size[0]), float(spot_size[0])], dtype=np.float32)
            else:
                logger.warning(f"No spot_size in anchor for sample '{sample_id}', using default [1.0, 1.0]")
                spot_size = np.array([1.0, 1.0], dtype=np.float32)

            coord_key = f'{target_name}_spatial'
            if coord_key not in anchor_adata.obsm:
                logger.error(
                    f"Anchor '{anchor_name}' sample '{sample_id}' missing obsm['{coord_key}']. "
                    f"Ensure alignment was performed. Skipping."
                )
                continue
            anchor_coords = np.asarray(anchor_adata.obsm[coord_key], dtype=np.float32)

            # Load target modality: native spatial coordinates and feature matrix.
            # Both anchor_coords and target_coords are in the target modality's coordinate system.
            target_adata = anndata.read_h5ad(target_files[sample_id])
            target_coords = np.asarray(target_adata.obsm['spatial'], dtype=np.float32)

            logger.debug(
                f"Anchor: {anchor_coords.shape[0]} spots, spot_size={spot_size}. "
                f"Target: {target_coords.shape[0]} spots, {target_adata.n_vars} features."
            )

            # Build the membership matrix once, then aggregate by sparse-preserving matmul.
            # A @ payload sums the rows of every target spot that falls within each anchor
            # footprint; empty footprints become all-zero rows (dropped downstream by the
            # coverage mask). The same operation is applied to .X and every layer.
            membership = self._footprint_membership_matrix(
                anchor_coordinates=anchor_coords,
                anchor_spot_size=spot_size,
                target_coordinates=target_coords,
            )

            registered_features = _as_csr(membership @ _as_csr(target_adata.X))

            registered_layers: dict[str, scipy.sparse.csr_matrix] = {}
            for layer_key in target_adata.layers:
                registered_layers[layer_key] = _as_csr(membership @ _as_csr(target_adata.layers[layer_key]))

            # Build output AnnData at anchor positions
            adata = anndata.AnnData(
                X=registered_features,
                obsm={'spatial': anchor_coords.copy()},
                obs={'sample_id': [sample_id] * anchor_coords.shape[0]},
                layers=registered_layers if registered_layers else None,
            )

            # Carry over var metadata from target (gene names, m/z values, etc.)
            adata.var = target_adata.var.copy()
            # Stamp the mode so the cache check above can tell our output apart from a
            # spot_interpolation file written to the same path.
            adata.uns['registration_type'] = self._REGISTRATION_TYPE

            write_h5ad_compat(adata, registered_file, compression=_H5AD_COMPRESSION)
            registered_files[sample_id] = registered_file
            logger.debug(f"Saved registration for sample '{sample_id}': {registered_features.shape}")

            # Free the membership matrix and aggregated outputs (the largest per-sample
            # allocations) before the next sample.
            del anchor_adata, target_adata, membership, registered_features, registered_layers, adata

        # Merge across samples
        registered_files = self._merge_samples(
            registered_files, target_name,
            force_recomputing=force_recomputing, all_per_sample_cached=all_cached,
        )
        return registered_files

    def _merge_samples(
        self,
        registered_files: dict[str, str],
        modality_name: str,
        force_recomputing: bool = False,
        all_per_sample_cached: bool = False,
    ) -> dict[str, str]:
        """Merge per-sample registration files."""
        sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
        if not sample_files:
            return registered_files

        merge_dir = os.path.join(self._path, "merged", "registration")
        os.makedirs(merge_dir, exist_ok=True)
        merged_file = MODALITY_REGISTRATION_MERGED(self._path, modality_name, "h5ad")

        # Cache check: reuse merged only when all per-sample files were cached and the merged
        # file exists with exactly the active sample composition.
        if os.path.exists(merged_file) and not force_recomputing and all_per_sample_cached:
            active_ids = set(sample_files.keys())
            merged_ids = read_merged_sample_ids(merged_file)
            if merged_ids == active_ids:
                logger.info(f"Using cached merged registration for '{modality_name}'")
                registered_files["merged"] = merged_file
                return registered_files

        logger.info(f"Merging registration files for '{modality_name}'")
        adata_list = []
        for sample_id, filepath in sample_files.items():
            adata = anndata.read_h5ad(filepath)
            adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
            adata_list.append(adata)

        merged = anndata.concat(adata_list, merge='same')
        merged.uns['registration_type'] = self._REGISTRATION_TYPE

        write_h5ad_compat(merged, merged_file, compression=_H5AD_COMPRESSION)
        registered_files["merged"] = merged_file
        return registered_files
