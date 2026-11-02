"""Registration fingerprints, cache identity and serialized method/geometry stamps."""

from focus.io.provenance import stat_signature

from collections.abc import Mapping

import os

import anndata

from focus.io.provenance import (
    cached_fingerprint,
    content_signature,
    array_digest,
    fingerprint,
)
from focus.registration.anchor import AnchorArrays


REGISTRATION_METHOD_VERSION = "footprint-1"

REGISTRATION_FINGERPRINT_KEY = "focus_registration_fingerprint"

REGISTRATION_GEOMETRY_KEY = "focus_registration_geometry"


def registration_fingerprint(
    registration_type: str,
    anchor: AnchorArrays,
    target_path: str,
    settings: Mapping[str, object],
) -> str:
    """Fingerprint of everything one sample's registration output depends on."""
    return fingerprint(
        {
            "method": REGISTRATION_METHOD_VERSION,
            "registration_type": str(registration_type),
            "anchor_spatial": array_digest(anchor.spatial),
            "anchor_in_target": array_digest(anchor.mapped),
            "spot_size": [float(v) for v in anchor.spot_size],
            "target": content_signature(target_path),
            "settings": settings,
        }
    )


def registration_cached(path: str, expected: str) -> bool:
    """True when ``path`` exists and carries ``expected`` as its registration fingerprint."""
    return (
        os.path.exists(path)
        and cached_fingerprint(path, key=REGISTRATION_FINGERPRINT_KEY) == expected
    )


def stamp_registration(
    adata: anndata.AnnData,
    registration_type: str,
    key: str,
    geometry: dict[str, object],
) -> None:
    """Attach the method, fingerprint, and geometry to AnnData in place."""
    adata.uns["registration_type"] = str(registration_type)
    adata.uns[REGISTRATION_FINGERPRINT_KEY] = key
    adata.uns[REGISTRATION_GEOMETRY_KEY] = geometry


def merged_fingerprint(
    sample_files: dict[str, str],
    registration_type: str,
    extra_uns: Mapping[str, object] | None,
) -> str:
    """Hash ordered sample fingerprints (or stat fallbacks) and optional merged metadata."""
    per_sample = {
        sid: cached_fingerprint(f, key=REGISTRATION_FINGERPRINT_KEY)
        or stat_signature(f)
        for sid, f in sample_files.items()
    }
    return fingerprint(
        {
            "method": REGISTRATION_METHOD_VERSION,
            "registration_type": str(registration_type),
            "samples": per_sample,
            "order": list(sample_files),
            "extra_uns": extra_uns or {},
        }
    )
