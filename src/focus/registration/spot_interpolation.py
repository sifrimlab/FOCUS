"""Register a spot-based target onto anchor footprints by overlap area."""

from functools import partial
from typing import cast

import anndata
import numpy as np
import scipy.sparse as sp
from anndata import AnnData
from scipy.spatial import cKDTree

from focus.config.options import RegistrationType, StNormalization
from focus.preprocessing.transcriptomics.normalization import normalize
from focus.preprocessing.transcriptomics.technology import AREA_KEY
from focus.registration.anchor import AnchorArrays
from focus.registration.execution import register_target
from focus.registration.targets import (
    read_target,
    target_normalization,
    target_sources,
    target_spot_size,
)
from focus.registration.types import RegistrationJob
from focus.runtime.reporter_context import get_reporter
from focus.runtime.reporting import StepReporter
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.footprint_shape import footprint_shape
from focus.spatial.matrices import sparse_csr
from focus.spatial.overlap import footprint_area, footprint_overlap

# Stamped in the fingerprint so a change of the method rebuilds only the spot registrations.
_ALGORITHM = "overlap-1"

# The map from the anchor to the target frame should be close to 1 target unit per anchor unit:
# both coordinate systems are in µm. A scale outside this range points to coordinates in pixels.
_SCALE_RANGE = (0.5, 2.0)

COVERAGE_KEY = "coverage"


def _as_csr(matrix: np.ndarray | sp.spmatrix) -> sp.csr_matrix:
    """``matrix`` as a float32 CSR matrix."""
    if sp.issparse(matrix):
        return sparse_csr(cast(sp.spmatrix, matrix)).astype(np.float32)
    return sparse_csr(sp.coo_matrix(np.asarray(matrix))).astype(np.float32)


def _normalize_merged(method: str, merged: AnnData) -> None:
    """Normalize the merged footprint counts with the target's method (one global scale)."""
    merged.X = merged.layers.pop("raw")
    merged.uns["focus_normalization"] = normalize(merged, method)


def _target_footprint(
    target: AnnData, target_xy: np.ndarray, sample_id: str, reporter: StepReporter
) -> np.ndarray:
    """Spot size of the target; the median nearest-neighbour distance when the file has none."""
    size = target_spot_size(target, sample_id)
    if size is not None:
        return size
    finite = target_xy[np.isfinite(target_xy).all(axis=1)]
    if len(finite) < 2:
        raise ValueError(f"Sample '{sample_id}': the target has no placed spots")
    pitch = float(np.median(np.asarray(cKDTree(finite).query(finite, k=2)[0])[:, 1]))
    reporter.warning(
        f"No valid spot_size in the target of sample '{sample_id}': "
        f"using the median spot spacing {pitch:.3g} as the spot size"
    )
    return np.array([pitch, pitch])


class SpotInterpolationRegistration:
    """
    Register a spot-based modality to the anchor by overlap area.

    Every anchor spot has a footprint (its ``spot_size`` around its centre, mapped into the
    target frame) and every target spot has one too (its own ``spot_size`` around its centre). The
    overlap area of the two decides how much of the target spot belongs to the anchor spot, so the
    result does not depend on whether the anchor or the target spots are larger:

    - an anchor inside one target spot takes that spot;
    - an anchor over many target spots takes all of them in proportion to their overlap;
    - target spots with gaps between them leave part of the anchor footprint uncovered, which is
      stored in ``obs['coverage']``.

    What happens to the overlap depends on what the target measures:

    - **Counts** (spatial transcriptomics, extensive): the anchor receives the share of each target
      spot's counts that lies in its footprint, so counts are conserved and weak small spots add up
      on a large anchor without loss. The counts are normalized afterwards on the merged file, with
      ``obs['focus_area']`` the covered footprint area.
    - **Intensities** (mass spectrometry imaging, intensive): the anchor receives the
      area-weighted mean of the target spots it covers, so a constant field stays constant
      whatever the spot sizes.

    Coordinate convention (after alignment):
    - ``anchor_files`` contains the aligned reference AnnData: ``obsm['spatial']`` (anchor
      frame) and ``obsm['{target_name}_spatial']`` (the same spots in the target frame).
    - ``target_files`` contains the preprocessed target AnnData; its ``obsm['spatial']`` is in
      the target frame.

    Both coordinate systems are in µm, which is what makes the two footprint areas comparable.

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
        counts: bool,
        method: str | None,
        reporter: StepReporter,
        sample_id: str,
        anchor: AnchorArrays,
        geom: AnchorGeometry,
    ) -> tuple[AnnData | None, dict[str, object]]:
        """Compute one sample's registered features and geometry metadata."""
        scale = geom.px_per_anchor_unit()
        if not _SCALE_RANGE[0] <= scale <= _SCALE_RANGE[1]:
            reporter.warning(
                f"Sample '{sample_id}': the alignment maps 1 anchor unit to {scale:.3g} target "
                "units. Spot sizes and overlaps assume both coordinate systems are in µm: check "
                "that the anchor coordinates are not in pixels"
            )
        target = read_target(target_files, sample_id)
        target_xy = np.asarray(target.obsm["spatial"], dtype=np.float64)
        shape = footprint_shape(
            target_xy, _target_footprint(target, target_xy, sample_id, reporter)
        )
        overlap = footprint_overlap(geom, target_xy, shape)
        coverage = overlap.coverage()
        n_empty = int((np.asarray(overlap.area.sum(axis=1)).ravel() <= 0).sum())
        reporter.detail(
            f"Anchor: {anchor.n_obs} spots, spot_size={anchor.spot_size}. "
            f"Target: {target_xy.shape[0]} spots, {target.n_vars} features. "
            f"Overlap sampled on the {overlap.sampled} footprints "
            f"({overlap.per_axis} x {overlap.per_axis} points)"
        )
        if n_empty:
            reporter.detail(
                f"{n_empty}/{anchor.n_obs} anchor spots overlap no target spot"
            )
        obs = {"sample_id": [sample_id] * anchor.n_obs, COVERAGE_KEY: coverage}
        spatial = {"spatial": anchor.mapped.copy()}
        if counts:
            raw = target.layers["raw"] if method != StNormalization.NONE else target.X
            summed = _as_csr(overlap.share_weights() @ _as_csr(raw))
            full = footprint_area(geom.shape)
            covered = coverage * full
            obs[AREA_KEY] = np.where(covered > 0, covered, full).astype(np.float32)
            adata = anndata.AnnData(
                X=summed,
                obsm=spatial,
                obs=obs,
                layers={"raw": summed.copy()}
                if method != StNormalization.NONE
                else None,
            )
        else:
            weights = overlap.mean_weights()
            adata = anndata.AnnData(
                X=_as_csr(weights @ _as_csr(target.X)),
                obsm=spatial,
                obs=obs,
                layers={
                    key: _as_csr(weights @ _as_csr(value))
                    for key, value in target.layers.items()
                }
                or None,
            )
        # Carry over var metadata from target (gene names, m/z values, etc.)
        adata.var = target.var.copy()
        return adata, {
            "overlap_sampled": overlap.sampled,
            "overlap_points_per_axis": overlap.per_axis,
        }

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
        Register a spot-based target modality to the anchor by overlap area.

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
        # An ST target carries the method of its merged file: its counts are apportioned, then
        # normalized once on the merged registered file (a sum of normalized values has no meaning).
        method = target_normalization(sources)
        counts = method is not None
        normalize_after = counts and method != StNormalization.NONE

        return register_target(
            RegistrationJob(
                path=self._path,
                samples=reporter.tqdm(
                    common_samples,
                    1,
                    2,
                    "Overlapping target spots with anchor footprints",
                    unit="sample",
                ),
                anchor_files=anchor_files,
                target_files=sources,
                anchor_name=anchor_name,
                target_name=target_name,
                registration_type=self._REGISTRATION_TYPE,
                settings={"algorithm": _ALGORITHM},
                compute=partial(
                    self._compute_sample, sources, counts, method, reporter
                ),
                force_recomputing=force_recomputing,
                reporter=reporter,
                finalize_merged=partial(_normalize_merged, cast(str, method))
                if normalize_after
                else None,
                merged_uns={"focus_target_normalization": method} if counts else None,
            ),
            reporter,
        )
