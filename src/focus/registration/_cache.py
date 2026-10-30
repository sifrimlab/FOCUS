"""
Anchor IO, fingerprints and sample merging shared by every registration engine.

A registered file is reused only when the fingerprint stored in its
``uns['focus_registration_fingerprint']`` equals the one computed from the current inputs:
the registration method version and type, the anchor's native and aligned coordinates, its
``spot_size``, the identity of the target input and the method settings. Files without a
fingerprint (written before it existed) are recomputed in place.
"""
import os
from dataclasses import dataclass

import anndata
import h5py
import numpy as np
from anndata.io import read_elem

from focus.constants import MODALITY_REGISTRATION_MERGED, ModalityType
from focus.provenance import cached_fingerprint, content_signature, array_digest, fingerprint, stat_signature
from focus.registration._footprint import read_spot_size
from focus.reporting import get_reporter
from focus.utils import write_h5ad_atomic, read_ome_physical_size_um

REGISTRATION_METHOD_VERSION = "footprint-1"
REGISTRATION_FINGERPRINT_KEY = "focus_registration_fingerprint"
REGISTRATION_GEOMETRY_KEY = "focus_registration_geometry"

_H5AD_COMPRESSION = "gzip"
_DEFAULT_SPOT_SIZE = np.array([1.0, 1.0])


@dataclass
class AnchorArrays:
    """What registration needs from one aligned anchor file."""
    spatial: np.ndarray       # (N, 2) anchor-native coordinates
    mapped: np.ndarray        # (N, 2) the same spots in the target frame
    spot_size: np.ndarray     # (2,) anchor spot size, anchor units
    spot_size_given: bool     # False when the anchor carried no usable spot_size

    @property
    def n_obs(self) -> int:
        return len(self.spatial)


def read_anchor_arrays(path: str, target_name: str) -> AnchorArrays | None:
    """Read the anchor's coordinates and spot size; None when ``obsm['{target}_spatial']`` is missing."""
    with h5py.File(path, "r") as f:
        key = f"obsm/{target_name}_spatial"
        if key not in f:
            return None
        spatial = np.asarray(read_elem(f["obsm/spatial"]), dtype=np.float32)
        mapped = np.asarray(read_elem(f[key]), dtype=np.float32)
        raw = read_elem(f["uns/spot_size"]) if "uns/spot_size" in f else None
    spot_size = read_spot_size(raw)
    given = spot_size is not None
    return AnchorArrays(spatial, mapped, spot_size if given else _DEFAULT_SPOT_SIZE.copy(), given)


def registration_fingerprint(registration_type: str, anchor: AnchorArrays, target_path: str, settings: dict) -> str:
    """Fingerprint of everything one sample's registration output depends on."""
    return fingerprint({
        "method": REGISTRATION_METHOD_VERSION,
        "registration_type": str(registration_type),
        "anchor_spatial": array_digest(anchor.spatial),
        "anchor_in_target": array_digest(anchor.mapped),
        "spot_size": [float(v) for v in anchor.spot_size],
        "target": content_signature(target_path),
        "settings": settings,
    })


def registration_cached(path: str, expected: str) -> bool:
    """True when ``path`` exists and carries ``expected`` as its registration fingerprint."""
    return os.path.exists(path) and cached_fingerprint(path, key=REGISTRATION_FINGERPRINT_KEY) == expected


def stamp_registration(adata: anndata.AnnData, registration_type: str, key: str, geometry: dict) -> None:
    adata.uns["registration_type"] = str(registration_type)
    adata.uns[REGISTRATION_FINGERPRINT_KEY] = key
    adata.uns[REGISTRATION_GEOMETRY_KEY] = geometry


def write_registered(adata: anndata.AnnData, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_h5ad_atomic(adata, path, compression=_H5AD_COMPRESSION)


def resolve_um_per_px(image_path: str, geom, anchor_type: str, reporter=None) -> float | None:
    """
    Level-0 µm per pixel of a preprocessed image.

    1. ``PhysicalSizeX/Y`` of the image's OME-XML (written by FOCUS preprocessing from the
       source file's metadata).
    2. For an MSI anchor, whose coordinates are always µm, the scale of the alignment map.
    3. Otherwise unknown (None, with a warning).
    """
    reporter = reporter or get_reporter()
    size = read_ome_physical_size_um(image_path)
    if size is not None:
        return float(np.sqrt(size[0] * size[1]))
    if anchor_type == ModalityType.MSI:
        scale = geom.px_per_anchor_unit()
        if np.isfinite(scale) and scale > 0:
            um = 1.0 / scale
            reporter.detail(f"Pixel size taken from the alignment with the MSI anchor: {um:.4g} µm/px")
            return um
    reporter.warning("The image has no pixel size: it is encoded at full resolution")
    return None


def register_samples(
    *,
    path: str,
    samples,
    anchor_files: dict[str, str],
    target_files: dict[str, str],
    anchor_name: str,
    target_name: str,
    registration_type: str,
    settings: dict,
    compute,
    force_recomputing: bool = False,
    reporter=None,
) -> dict[str, str]:
    """
    The per-sample loop every registration engine shares.

    ``settings`` is a dict, or a callable ``settings(sample_id) -> dict``, of the method
    settings the output depends on. For each sample: read the aligned anchor, skip with a
    warning when it has no coordinates in the target frame, reuse the output when its
    fingerprint matches, otherwise build the
    footprint geometry, call ``compute(sample_id, anchor, geom) -> (AnnData | None, dict)``
    and write the result with its registration stamps. The dict returned by ``compute`` is
    merged into the geometry stamp.

    Returns
    -------
    dict[str, str]
        ``{sample_id: registered_file}`` for every sample that has an output.
    """
    from focus.constants import MODALITY_REGISTRATION
    from focus.registration._footprint import AnchorGeometry

    reporter = reporter or get_reporter()
    registered: dict[str, str] = {}
    for sample_id in samples:
        out = MODALITY_REGISTRATION(path, sample_id, target_name, "h5ad")
        anchor = read_anchor_arrays(anchor_files[sample_id], target_name)
        if anchor is None:
            # Only the aligned key locates the anchor spots in the target's frame; obsm['spatial']
            # is the anchor's own frame and would place every footprint elsewhere.
            reporter.warning(
                f"Sample skipped: the anchor '{anchor_name}' has no obsm['{target_name}_spatial'] (was alignment performed?)"
            )
            continue
        sample_settings = settings(sample_id) if callable(settings) else settings
        key = registration_fingerprint(registration_type, anchor, target_files[sample_id], sample_settings)
        if not force_recomputing and registration_cached(out, key):
            reporter.cached()
            registered[sample_id] = out
            continue

        if not anchor.spot_size_given:
            reporter.warning("No spot_size in the anchor: using the default [1.0, 1.0]")
        try:
            geom = AnchorGeometry.from_arrays(anchor.spatial, anchor.mapped, anchor.spot_size)
        except ValueError as e:
            reporter.warning(f"Sample skipped: {e}")
            continue
        n_unplaced = int((~geom.valid).sum())
        if n_unplaced:
            reporter.detail(f"{n_unplaced}/{anchor.n_obs} anchor spots cannot be placed in '{target_name}': zero rows")

        adata, extra = compute(sample_id, anchor, geom)
        if adata is None:
            continue
        stamp_registration(adata, registration_type, key, {**geom.describe(), **extra})
        write_registered(adata, out)
        registered[sample_id] = out
    return registered


def merge_registered_samples(
    path: str,
    registered_files: dict[str, str],
    modality_name: str,
    registration_type: str,
    *,
    force_recomputing: bool = False,
    merged_file: str | None = None,
    extra_uns: dict | None = None,
) -> dict[str, str]:
    """
    Concatenate per-sample registration files into the merged file.

    The merged file is reused when its fingerprint, built from the per-sample fingerprints in
    sample order (a file without one contributes its stat signature), still matches.
    ``merged_file`` overrides the canonical path; ``extra_uns`` is stamped on the merged file.
    ``anndata.concat`` drops per-sample ``uns``, so the registration stamps are re-written:
    the geometry stamp becomes a dict keyed by ``sample_id``.
    """
    sample_files = {k: v for k, v in registered_files.items() if k != "merged"}
    if not sample_files:
        return registered_files

    if merged_file is None:
        merged_file = MODALITY_REGISTRATION_MERGED(path, modality_name, "h5ad")
    os.makedirs(os.path.dirname(merged_file), exist_ok=True)

    per_sample = {
        sid: cached_fingerprint(f, key=REGISTRATION_FINGERPRINT_KEY) or stat_signature(f)
        for sid, f in sample_files.items()
    }
    key = fingerprint({
        "method": REGISTRATION_METHOD_VERSION,
        "registration_type": str(registration_type),
        "samples": per_sample,
        "order": list(sample_files),
        "extra_uns": extra_uns or {},
    })
    if not force_recomputing and registration_cached(merged_file, key):
        get_reporter().cached()
        registered_files["merged"] = merged_file
        return registered_files

    adata_list, geometry = [], {}
    for sample_id, filepath in sample_files.items():
        adata = anndata.read_h5ad(filepath)
        adata.obs_names = [f"{sample_id}_{i}" for i in range(adata.n_obs)]
        if REGISTRATION_GEOMETRY_KEY in adata.uns:
            geometry[sample_id] = dict(adata.uns[REGISTRATION_GEOMETRY_KEY])
        adata_list.append(adata)

    merged = anndata.concat(adata_list, merge="same")
    stamp_registration(merged, registration_type, key, geometry)
    if extra_uns:
        merged.uns.update(extra_uns)
    write_registered(merged, merged_file)
    registered_files["merged"] = merged_file
    return registered_files
