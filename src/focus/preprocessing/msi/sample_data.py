"""MSI sample readout and assembly before asynchronous serialization."""

from __future__ import annotations
from typing import cast
from typing import TYPE_CHECKING
from focus.preprocessing.msi.run_state import SampleArrays, ResumeState, FeatureState
from focus.preprocessing.msi.interpolation import interpolate_mode

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset
    from focus.preprocessing.msi.sample import MsiSample
from typing import TYPE_CHECKING

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


def interpolate_sample(
    dataset: MsiDataset,
    sample: MsiSample,
    features: FeatureState,
    mass_tolerance: int,
    intensity_normalization: str,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Read, bin, and normalize one sample while releasing its raw payload."""
    sample_id = sample.sample_id
    standard_columns = features.standard_columns

    # Load raw spectra for this sample
    original_mzs, intensities, _, _ = sample.load_payload(
        annotation_db=dataset.lipid_annotation_db, mass_tolerance=mass_tolerance
    )
    dataset.foreground_masks[sample_id] = cast(np.ndarray, sample.foreground_mask)

    # Interpolate and normalize each ion mode; keep local copies only
    interp_by_mode: dict[str, np.ndarray] = {}
    norm_by_mode: dict[str, np.ndarray] = {}

    for mode in sample.ion_modes:
        merged_intensities = interpolate_mode(
            original_mzs[mode],
            intensities[mode],
            dataset.reference_mz[mode],
            sample._metadata[mode][MsiMetadata.INTENSITIES_DTYPE],
            mass_tolerance,
        )

        # Store raw before normalization overwrites the reference
        interp_by_mode[mode] = merged_intensities
        merged_intensities = normalize_intensities(
            merged_intensities,
            intensity_normalization,
            pixel_coordinates=sample._metadata[mode][MsiMetadata.PIXEL_COORDINATES],
            standard_columns=standard_columns.get(mode),
        )

        norm_by_mode[mode] = merged_intensities

    del intensities, original_mzs
    gc.collect()
    return interp_by_mode, norm_by_mode


def prepare_arrays(
    dataset: MsiDataset,
    sample: MsiSample,
    resume: ResumeState,
    features: FeatureState,
    mass_tolerance: int,
    intensity_normalization: str,
) -> SampleArrays:
    """Assemble polarity matrices and native coordinates with the original release points."""
    sample_id = sample.sample_id
    ref_mode = (
        MsiIonMode.POSITIVE
        if MsiIonMode.POSITIVE in sample.ion_modes
        else MsiIonMode.NEGATIVE
    )
    spot_sizes = resume.spot_sizes
    positive_cols = features.positive_cols
    negative_cols = features.negative_cols
    total_cols = features.total_cols
    interp_by_mode, norm_by_mode = interpolate_sample(
        dataset, sample, features, mass_tolerance, intensity_normalization
    )

    # Build combined pos+neg matrices for this sample
    physical_coords = sample._metadata[ref_mode][
        MsiMetadata.PHYSICAL_COORDINATES
    ].astype(np.float32)
    raster_coords = sample._metadata[ref_mode][MsiMetadata.RASTER_COORDINATES]
    raster_size = sample._metadata[ref_mode][MsiMetadata.RASTER_SIZE]
    spot_size = np.array(
        [float(raster_size[0]), float(raster_size[1])], dtype=np.float32
    )
    spot_sizes[sample_id] = spot_size.tolist()

    rows = interp_by_mode[ref_mode].shape[0]
    raw_matrix = np.zeros((rows, total_cols), dtype=np.float32)
    norm_matrix = np.zeros((rows, total_cols), dtype=np.float32)

    if MsiIonMode.POSITIVE in sample.ion_modes and positive_cols > 0:
        raw_matrix[:, :positive_cols] = interp_by_mode[MsiIonMode.POSITIVE].astype(
            np.float32
        )
        norm_matrix[:, :positive_cols] = norm_by_mode[MsiIonMode.POSITIVE].astype(
            np.float32
        )
    if MsiIonMode.NEGATIVE in sample.ion_modes and negative_cols > 0:
        raw_matrix[:, positive_cols:] = interp_by_mode[MsiIonMode.NEGATIVE].astype(
            np.float32
        )
        norm_matrix[:, positive_cols:] = norm_by_mode[MsiIonMode.NEGATIVE].astype(
            np.float32
        )

    del interp_by_mode, norm_by_mode
    gc.collect()

    X_sparse = sp.csr_matrix(norm_matrix)
    raw_sparse = sp.csr_matrix(raw_matrix)
    del raw_matrix, norm_matrix
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
    mass_tolerance: int,
    intensity_normalization: str,
    resolution: str,
) -> tuple[ad.AnnData, str, dict[str, np.ndarray]]:
    """Prepare one processed sample and its output path for asynchronous writing."""
    arrays = prepare_arrays(
        dataset, sample, resume, features, mass_tolerance, intensity_normalization
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
    adata = ad.AnnData(
        X=X_sparse,
        layers={"raw": raw_sparse},
        obs=obs_df,
        obsm={"spatial": physical_coords, "raster_coordinates": raster_coords},
        var=var_df.copy(),
        uns={
            "spot_size": spot_size.tolist(),
            "recalibration_reference": stored_reference,
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
            smoothing_rings=clustering_preset(resolution)["smoothing_rings"],
        )
    )

    output_file = preprocessed_file(
        dataset.dataset_source_path, sample.sample_id, sample.modality_name, "h5ad"
    )
    return adata, output_file, stored_reference
