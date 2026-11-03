"""Merge registered sample containers while retaining per-sample geometry provenance."""

from focus.registration.provenance import merged_fingerprint

from collections.abc import Callable, Mapping

import os

import anndata

from focus.io.paths import merged_registration_file
from focus.runtime.reporter_context import get_reporter
from focus.registration.provenance import (
    REGISTRATION_GEOMETRY_KEY,
    registration_cached,
    stamp_registration,
)
from focus.registration.storage import write_registered


def merge_registered_samples(
    path: str,
    registered_files: dict[str, str],
    modality_name: str,
    registration_type: str,
    *,
    force_recomputing: bool = False,
    merged_file: str | None = None,
    extra_uns: Mapping[str, object] | None = None,
    finalize: Callable[[anndata.AnnData], None] | None = None,
) -> dict[str, str]:
    """
    Concatenate per-sample registration files into the merged file.

    The merged file is reused when its fingerprint, built from the per-sample fingerprints in
    sample order (a file without one contributes its stat signature), still matches.
    ``merged_file`` overrides the canonical path; ``extra_uns`` is stamped on the merged file.
    ``anndata.concat`` drops per-sample ``uns``, so the registration stamps are re-written:
    the geometry stamp becomes a dict keyed by ``sample_id``. ``finalize`` is applied to the
    merged AnnData before it is written (``extra_uns`` should name what it does, so the
    fingerprint follows it).
    """
    sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
    if not sample_files:
        return registered_files

    if merged_file is None:
        merged_file = merged_registration_file(path, modality_name, "h5ad")
    os.makedirs(os.path.dirname(merged_file), exist_ok=True)

    key = merged_fingerprint(sample_files, registration_type, extra_uns)
    if not force_recomputing and registration_cached(merged_file, key):
        get_reporter().cached()
        registered_files["merged"] = merged_file
        return registered_files

    adata_list: list[anndata.AnnData] = []
    geometry: dict[str, object] = {}
    for sample_id, filepath in sample_files.items():
        adata = anndata.read_h5ad(filepath)
        adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
        if REGISTRATION_GEOMETRY_KEY in adata.uns:
            geometry[sample_id] = dict(adata.uns[REGISTRATION_GEOMETRY_KEY])
        adata_list.append(adata)

    merged = anndata.concat(adata_list, merge="same")
    if finalize is not None:
        finalize(merged)
    stamp_registration(merged, registration_type, key, geometry)
    if extra_uns:
        merged.uns.update(extra_uns)
    write_registered(merged, merged_file)
    registered_files["merged"] = merged_file
    return registered_files
