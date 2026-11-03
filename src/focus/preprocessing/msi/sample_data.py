"""MSI sample readout and assembly before asynchronous serialization."""

from __future__ import annotations
from typing import TYPE_CHECKING
from focus.preprocessing.msi.run_state import SampleArrays, ResumeState, FeatureState
from focus.preprocessing.msi.interpolation import empty_csr, interpolate_mode

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
import scipy.sparse as sp
from focus.spatial.clustering import compute_cluster_labels
from focus.spatial.settings import clustering_preset
import anndata as ad
import pandas as pd
import gc

from focus.config.keys import MsiMetadata
from focus.config.options import MsiIonMode
from focus.io.paths import preprocessed_file
from focus.preprocessing.msi.normalization import normalize_intensities
from focus.preprocessing.msi.technology import SampleProfile


def interpolate_sample(
    dataset: MsiDataset,
    sample: MsiSample,
    features: FeatureState,
    mass_tolerance: float,
    intensity_normalization: str,
    profile: SampleProfile,
) -> tuple[dict[str, sp.csr_matrix], dict[str, sp.csr_matrix]]:
    """Read, recalibrate, bin and normalize one sample while releasing its raw payload."""
    sample_id = sample.sample_id
    standard_columns = features.standard_columns

    # Recalibrated spectra; a resumed run detects the foreground of a queued sample here.
    original_mzs, intensities = sample.load_payload(mass_tolerance)
    if sample.filtered_idx is None:
        sample.foreground.detect(original_mzs, intensities, mass_tolerance, profile)
    dataset.foreground_masks[sample_id] = sample.foreground_mask

    interp_by_mode: dict[str, sp.csr_matrix] = {}
    norm_by_mode: dict[str, sp.csr_matrix] = {}
    for mode in sample.ion_modes:
        raw = interpolate_mode(
            original_mzs[mode],
            intensities[mode],
            dataset.reference_mz[mode],
            dataset.reference_sigma[mode],
            mass_tolerance,
        )
        interp_by_mode[mode] = raw
        norm_by_mode[mode] = normalize_intensities(
            raw,
            intensity_normalization,
            pixel_coordinates=sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES],
            standard_columns=standard_columns.get(mode),
        )

    del intensities, original_mzs
    gc.collect()
    return interp_by_mode, norm_by_mode


def _polarity_blocks(
    by_mode: dict[str, sp.csr_matrix], rows: int, features: FeatureState
) -> sp.csr_matrix:
    """Positive then negative feature columns of one sample; zeros for an ion mode it lacks."""
    blocks = []
    for mode, cols in (
        (MsiIonMode.POSITIVE, features.positive_cols),
        (MsiIonMode.NEGATIVE, features.negative_cols),
    ):
        if cols:
            blocks.append(
                by_mode[mode].astype(np.float32)
                if mode in by_mode
                else empty_csr(rows, cols)
            )
    if not blocks:
        return empty_csr(rows, 0)
    return sp.csr_matrix(sp.hstack(blocks, format="csr"), dtype=np.float32)


def prepare_arrays(
    dataset: MsiDataset,
    sample: MsiSample,
    resume: ResumeState,
    features: FeatureState,
    mass_tolerance: float,
    intensity_normalization: str,
    profile: SampleProfile,
) -> SampleArrays:
    """Assemble the polarity matrices and native coordinates of one sample."""
    sample_id = sample.sample_id
    ref_mode = (
        MsiIonMode.POSITIVE
        if MsiIonMode.POSITIVE in sample.ion_modes
        else MsiIonMode.NEGATIVE
    )
    interp_by_mode, norm_by_mode = interpolate_sample(
        dataset, sample, features, mass_tolerance, intensity_normalization, profile
    )

    physical_coords = sample._metadata[ref_mode][
        MsiMetadata.PHYSICAL_COORDINATES
    ].astype(np.float32)
    raster_coords = sample._metadata[ref_mode][MsiMetadata.RASTER_COORDINATES]
    raster_size = sample._metadata[ref_mode][MsiMetadata.RASTER_SIZE]
    spot_size = np.array(
        [float(raster_size[0]), float(raster_size[1])], dtype=np.float32
    )
    resume.spot_sizes[sample_id] = spot_size.tolist()

    rows = interp_by_mode[ref_mode].shape[0]
    raw_sparse = _polarity_blocks(interp_by_mode, rows, features)
    X_sparse = _polarity_blocks(norm_by_mode, rows, features)
    del interp_by_mode, norm_by_mode
    gc.collect()
    return SampleArrays(
        physical_coords, raster_coords, spot_size, rows, X_sparse, raw_sparse
    )


def sample_observations(
    dataset: MsiDataset, sample: MsiSample, n_obs: int, qc_modes: list[str]
) -> pd.DataFrame:
    """Build sample identifiers, foreground flags, and recalibration QC columns."""
    sample_id = sample.sample_id
    obs_index = pd.Index([f"{sample_id}_{i}" for i in range(n_obs)], dtype=object)
    obs_df = pd.DataFrame(
        {
            "sample_id": pd.Categorical([sample_id] * n_obs),
            "foreground": pd.Categorical(dataset.foreground_masks[sample_id]),
            "foreground_score": (
                sample.foreground_score
                if sample.foreground_score is not None
                else np.zeros(n_obs, dtype=np.float32)
            ),
        },
        index=obs_index,
    )
    # Applied recalibration per spot: ppm correction at the mean reference m/z and the
    # number of references matched in the narrow pass.
    for mode in qc_modes:
        model = sample.recalibration_model.get(mode)
        obs_df[f"recalibration_ppm_{mode}"] = (
            model.offset_ppm.astype(np.float32)
            if model is not None
            else np.full(n_obs, np.nan, dtype=np.float32)
        )
        obs_df[f"recalibration_matches_{mode}"] = (
            model.matches if model is not None else np.zeros(n_obs, dtype=np.int16)
        )
    return obs_df


def prepare_sample(
    dataset: MsiDataset,
    sample: MsiSample,
    resume: ResumeState,
    features: FeatureState,
    dataset_key: str,
    mass_tolerance: float,
    intensity_normalization: str,
    resolution: str,
    profile: SampleProfile,
) -> tuple[ad.AnnData, str, dict[str, np.ndarray]]:
    """Prepare one processed sample and its output path for asynchronous writing."""
    arrays = prepare_arrays(
        dataset,
        sample,
        resume,
        features,
        mass_tolerance,
        intensity_normalization,
        profile,
    )
    n_obs = arrays.n_obs
    X_sparse, raw_sparse = arrays.normalized, arrays.raw
    physical_coords, raster_coords, spot_size = (
        arrays.physical,
        arrays.raster,
        arrays.spot_size,
    )
    var_df = features.var
    obs_df = sample_observations(dataset, sample, n_obs, features.qc_modes)

    stored_reference = {
        str(mode): np.asarray(masses, dtype=np.float64)
        for mode, masses in (sample.recalibration_reference or {}).items()
    }
    stored_pool = {
        str(mode): dict(entry) for mode, entry in sample.recalibration_pool.items()
    }
    adata = ad.AnnData(
        X=X_sparse,
        layers={"raw": raw_sparse},
        obs=obs_df,
        obsm={"spatial": physical_coords, "raster_coordinates": raster_coords},
        var=var_df.copy(),
        uns={
            "spot_size": spot_size.tolist(),
            "recalibration_reference": stored_reference,
            "recalibration_pool": stored_pool,
            "focus_preprocessing_fingerprint": dataset_key,
        },
    )

    # Per-sample cluster labels for alignment colouring (unit aggregation + Leiden), from the
    # raw binned intensities: every linear normalization of .X is undone by the clustering's
    # own total normalization anyway, and log1p needs non-negative input (CLR is not). Large
    # samples are coarsened into k x k blocks of the acquisition raster (Voronoi supercells
    # if no raster is recovered) and the spots of a unit are summed. Only obs['cluster'] is
    # produced; the summed/normalized matrix and PCA / neighbour intermediates are
    # throwaway, never written to the AnnData.
    adata.obs["cluster"] = pd.Categorical(
        compute_cluster_labels(
            adata.layers["raw"],
            leiden_resolution=dataset._LEIDEN_RESOLUTION,
            normalize_target_sum=None,
            coordinates=adata.obsm.get("spatial"),
            smoothing_rings=(
                profile.smoothing_rings
                if profile.smoothing_rings is not None
                else clustering_preset(resolution)["smoothing_rings"]
            ),
        )
    )

    output_file = preprocessed_file(
        dataset.dataset_source_path, sample.sample_id, sample.modality_name, "h5ad"
    )
    return adata, output_file, stored_reference
