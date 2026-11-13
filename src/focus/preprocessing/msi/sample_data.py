"""MSI sample readout and assembly before asynchronous serialization."""

from __future__ import annotations
from collections.abc import Callable
from typing import TYPE_CHECKING
from focus.preprocessing.msi.run_state import SampleArrays, ResumeState, FeatureState
from focus.preprocessing.msi.interpolation import empty_csr, interpolate_mode

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset
    from focus.preprocessing.msi.sample import MsiSample

import numpy as np
import scipy.sparse as sp
import anndata as ad
import pandas as pd
import gc

from focus.config.keys import MsiMetadata
from focus.config.options import (
    MsiIntensityNormalization,
    MsiIonMode,
    SpotResolution,
)
from focus.io.paths import preprocessed_file
from focus.preprocessing.msi.normalization import normalize_intensities
from focus.preprocessing.msi.technology import SampleProfile
from focus.runtime.reporter_context import get_reporter
from focus.spatial.clustering import Represent, cluster_spots, unit_mean
from focus.spatial.display import store_display
from focus.spatial.settings import FEATURES, FOREGROUND, clustering_preset
from focus.spatial.units import SpatialUnits
from focus.spot_colors import cluster_colors, label_order


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
        sample.foreground.detect(
            original_mzs,
            intensities,
            mass_tolerance,
            profile,
            dataset.lipid_annotation_db,
        )
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

    label_sample(
        adata,
        dataset.foreground_masks[sample.sample_id],
        intensity_normalization,
        resolution,
        profile,
    )

    output_file = preprocessed_file(
        dataset.dataset_source_path, sample.sample_id, sample.modality_name, "h5ad"
    )
    return adata, output_file, stored_reference


def label_sample(
    adata: ad.AnnData,
    foreground: np.ndarray,
    intensity_normalization: str,
    resolution: str,
    profile: SampleProfile,
) -> None:
    """Alignment display labels of one sample, with eligibility and provenance, in ``adata``.

    :func:`focus.spatial.clustering.cluster_spots` in the mode of the sample type. Only foreground
    spectra with a peak take part; background spectra are ``'unassigned'``. The representation is
    the normalized ``.X`` averaged over each unit (``none``: the summed raw rows divided by their
    nonzero median), then ``log1p``: raw intensities are never clustered. Only ``obs['cluster']``,
    ``obs['clustering_eligible']``, ``uns['cluster_colors']`` and ``uns['focus_clustering']`` are
    written; units and every intermediate are discarded.
    """
    raw = sp.csr_matrix(adata.layers["raw"])
    include = np.asarray(foreground, dtype=bool) & (np.diff(raw.indptr) > 0)
    if (
        profile.clustering_mode == FOREGROUND
        and resolution == SpotResolution.SUB_CELLULAR
    ):
        get_reporter().detail(
            f"Clustering: {profile.sample_type} keeps native pixels; resolution is not used"
        )

    labels, eligible, info = cluster_spots(
        raw,
        np.asarray(adata.obsm["spatial"]),
        clustering_preset(profile.clustering_mode, resolution, FEATURES),
        _representation(adata.X, include, intensity_normalization),
        feature_names=adata.var_names.to_numpy(dtype=str),
        include=include,
    )
    info["resolution"] = resolution
    info["sample_type"] = profile.sample_type
    store_display(adata, labels, info, _feature_label(adata))
    categories = label_order(labels)
    adata.obs["clustering_eligible"] = eligible
    adata.obs["cluster"] = pd.Categorical(labels, categories=categories)
    colors = cluster_colors(categories)
    adata.uns["cluster_colors"] = [colors[label] for label in categories]
    adata.uns["focus_clustering"] = info


def _feature_label(adata: ad.AnnData) -> Callable[[str], str]:
    """Display label of a feature image: its m/z and, when annotated, the first annotation."""
    var = adata.var

    def label(name: str) -> str:
        """'m/z 760.5855 · PC 34:1 [M+H]+' (the annotation cut at its first '; ')."""
        if name not in var.index or "mz" not in var.columns:
            return str(name)
        row = var.loc[name]
        text = f"m/z {float(row['mz']):.4f}"
        annotation = str(row.get("lipid_annotation", "Unannotated"))
        if annotation and annotation != "Unannotated":
            text += f" · {annotation.split('; ')[0]}"
        return text

    return label


def _representation(
    X: sp.csr_matrix, include: np.ndarray, normalization: str
) -> Represent:
    """MSI representation of the clustering units: normalized intensities on a log scale.

    The mean of the normalized spectra ``X`` of a unit's included spots; ``none`` divides each
    summed raw spectrum by the median of its nonzero features instead.
    """

    def represent(
        summed: sp.csr_matrix, units: SpatialUnits, kept: np.ndarray
    ) -> tuple[sp.csr_matrix, str]:
        """Normalized intensities of the kept units on a log scale."""
        if normalization == MsiIntensityNormalization.NONE:
            method = MsiIntensityNormalization.MEDIAN
            rows = normalize_intensities(summed, method)
            label = f"{method} (fallback for none) + log1p"
        else:
            rows = unit_mean(X, units, kept, include)
            label = f"{normalization} + log1p"
        rows = sp.csr_matrix(rows, dtype=np.float32, copy=True)
        np.log1p(rows.data, out=rows.data)
        return rows, label

    return represent
