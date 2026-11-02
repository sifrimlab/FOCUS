"""Construct shared feature metadata and internal-standard columns."""

from __future__ import annotations
from typing import cast
from typing import TYPE_CHECKING
from focus.preprocessing.msi.run_state import FeatureState

if TYPE_CHECKING:
    from focus.preprocessing.msi.dataset import MsiDataset

import numpy as np

import pandas as pd

from focus.config.options import MsiIntensityNormalization, MsiIonMode


def feature_metadata(
    dataset: MsiDataset,
    intensity_normalization: str,
    internal_standard_mz: dict[str, np.ndarray] | None,
    mass_tolerance: int,
) -> FeatureState:
    """Construct shared feature metadata and internal-standard columns."""

    # Build var metadata (shared across all samples); reads self.reference_mz and self.lipid_annotations,
    # which are either freshly computed above or pulled from the partial-resume cache.
    positive_cols = (
        dataset.reference_mz[MsiIonMode.POSITIVE].shape[0]
        if MsiIonMode.POSITIVE in dataset.reference_mz
        else 0
    )
    negative_cols = (
        dataset.reference_mz[MsiIonMode.NEGATIVE].shape[0]
        if MsiIonMode.NEGATIVE in dataset.reference_mz
        else 0
    )
    total_cols = positive_cols + negative_cols

    mz_parts, mode_parts, annotation_parts = [], [], []
    for mode in [MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE]:
        if mode not in dataset.reference_mz or dataset.reference_mz[mode].size == 0:
            continue
        mz_parts.append(dataset.reference_mz[mode])
        mode_parts.extend([mode] * dataset.reference_mz[mode].shape[0])
        if (
            dataset.lipid_annotation_db is not None
            and mode in dataset.lipid_annotations
        ):
            annotation_parts.append(dataset.lipid_annotations[mode])
        else:
            annotation_parts.append(
                np.array(["Unannotated"] * dataset.reference_mz[mode].shape[0])
            )

    merged_reference_mz = (
        np.concatenate(mz_parts).astype(np.float32)
        if mz_parts
        else np.array([], dtype=np.float32)
    )
    merged_mode_labels = mode_parts
    merged_annotations = (
        np.concatenate(annotation_parts)
        if annotation_parts
        else np.array(["Unannotated"] * total_cols)
    )

    var_df = pd.DataFrame(
        {
            "mz": merged_reference_mz,
            "mz_mode": pd.Categorical(merged_mode_labels),
            "lipid_annotation": pd.Categorical(merged_annotations),
        },
        index=pd.Index([str(i) for i in range(total_cols)], dtype=object),
    )

    # Backbone columns of the internal standards, for 'internal_standard' normalization.
    standard_columns: dict[str, np.ndarray] = {}
    if intensity_normalization == MsiIntensityNormalization.INTERNAL_STANDARD:
        for mode, masses in cast(dict[str, np.ndarray], internal_standard_mz).items():
            backbone = np.asarray(
                dataset.reference_mz.get(mode, np.array([])), dtype=np.float64
            )
            if backbone.size == 0:
                continue
            nearest = np.abs(backbone[None, :] - masses[:, None]).argmin(axis=1)
            distance_ppm = np.abs(backbone[nearest] - masses) / masses * 1e6
            if np.any(distance_ppm > mass_tolerance):
                absent = ", ".join(
                    f"{m:.4f}" for m in masses[distance_ppm > mass_tolerance]
                )
                raise ValueError(
                    f"Internal standard m/z {absent} ({str(mode).upper()}) has no m/z backbone feature "
                    f"within {mass_tolerance} ppm; check internal_standard_mz."
                )
            standard_columns[mode] = np.unique(nearest)

    # Ion modes with features: each gets its recalibration QC columns in every sample, so all
    # per-sample files share one obs schema for the on-disk merge.
    qc_modes: list[str] = [
        mode
        for mode in (MsiIonMode.POSITIVE, MsiIonMode.NEGATIVE)
        if mode in dataset.reference_mz and dataset.reference_mz[mode].size > 0
    ]
    return FeatureState(
        positive_cols, negative_cols, total_cols, var_df, standard_columns, qc_modes
    )
