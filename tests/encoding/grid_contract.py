"""Controlled standalone grid-extraction workflows for baseline comparisons."""

from __future__ import annotations

import importlib
import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import anndata as ad
import numpy as np
import tifffile


@dataclass
class GridTrace:
    """Model, read, and write events plus controlled per-file write identities."""

    models: int = 0
    encodings: int = 0
    pyramids: int = 0
    writes: int = 0
    versions: dict[str, int] = field(default_factory=dict)


class TinyGridEncoder:
    """Deterministic embeddings with the real grid encoder's interface and geometry."""

    def __init__(self, trace: GridTrace) -> None:
        """Bind counters without downloading or initializing a pretrained model."""
        self.trace = trace

    def extract_grid_features(
        self,
        image: np.ndarray,
        background_color: str,
        patch_size: int,
        step_reporter: object = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return three features per complete tile and its center in level pixels."""
        self.trace.encodings += 1
        rows, columns = np.mgrid[
            : image.shape[0] // patch_size, : image.shape[1] // patch_size
        ]
        centers = np.column_stack([columns.ravel(), rows.ravel()]).astype(np.float32)
        centers = centers * patch_size + patch_size // 2
        features = np.column_stack([centers, np.arange(len(centers))]).astype(
            np.float32
        )
        return features, centers


def load_script(path: Path) -> ModuleType:
    """Import an entrypoint as a fixture without executing its main guard."""
    spec = importlib.util.spec_from_file_location("focus_grid_contract_script", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_grid_input(root: Path) -> None:
    """Create one preprocessed RGB image with two stored pyramid levels."""
    path = root / "s" / "preprocessing" / "he" / "he_s_processed.ome.tiff"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tifffile.TiffWriter(path, ome=True) as writer:
        writer.write(
            np.full((512, 512, 3), 0.25, np.float32),
            subifds=1,
            photometric="rgb",
            metadata={"axes": "YXS"},
        )
        writer.write(
            np.full((256, 256, 3), 0.25, np.float32), subfiletype=1, photometric="rgb"
        )


def json_value(value: object) -> object:
    """Normalize decoded scientific metadata through its explicit JSON boundary."""
    if isinstance(value, np.ndarray):
        return {
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "values": cast(object, value.tolist()),
        }
    if isinstance(value, np.generic):
        return {"scalar_dtype": value.dtype.str, "value": cast(object, value.item())}
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def decoded_grid_files(root: Path) -> dict[str, object]:
    """Read the complete generated feature matrices, axes, identifiers, and metadata."""
    files: dict[str, object] = {}
    for path in sorted(root.rglob("*.h5ad")):
        data = ad.read_h5ad(path)
        files[str(path.relative_to(root))] = {
            "shape": list(data.shape),
            "dtype": data.X.dtype.str,
            "X": json_value(np.asarray(data.X)),
            "spatial": json_value(data.obsm["spatial"]),
            "obs_names": list(data.obs_names),
            "var_names": list(data.var_names),
            "sample_id": data.obs["sample_id"].astype(str).tolist(),
            "uns": json_value(data.uns),
        }
    return files


def grid_config(root: Path, force: bool, background: str) -> Path:
    """Write supported CLI settings using a deliberately unused placeholder token."""
    path = root / "config.json"
    path.write_text(
        json.dumps(
            {
                "dataset_path": str(root),
                "reference_modality": "he",
                "perform_alignment": True,
                "perform_registration": True,
                "huggingface_token": "controlled-unused-placeholder",
                "modalities": [
                    {
                        "name": "he",
                        "type": "microscopy_image",
                        "processing_settings": {},
                        "registration_type": "feature_extraction",
                        "registration_settings": {
                            "force_recomputing": force,
                            "background_color": background,
                        },
                    }
                ],
            }
        )
    )
    return path


def run_grid_case(
    script: ModuleType,
    implementation: ModuleType,
    root: Path,
    trace: GridTrace,
    levels: int,
    force: bool,
    background: str,
) -> dict[str, object]:
    """Run one CLI case with controlled model outputs and file-identity changes."""
    config = grid_config(root, force, background)
    writer = implementation.write_h5ad_compat
    dimensions = implementation.ome_tiff_pyramid_dims
    merger = importlib.import_module(implementation.merge_registered_samples.__module__)
    fingerprint = getattr(merger, "merged_fingerprint", None)
    if fingerprint is not None:
        merger = importlib.import_module(fingerprint.__module__)
    trace.models = trace.encodings = trace.pyramids = trace.writes = 0

    def create_encoder(path: str, hf_token: str | None) -> TinyGridEncoder:
        """Record each lazy model construction without using its placeholder token."""
        trace.models += 1
        return TinyGridEncoder(trace)

    def write(data: ad.AnnData, path: str, **kwargs: Any) -> None:
        """Track rewrites at the external AnnData persistence boundary."""
        writer(data, path, **kwargs)
        trace.writes += 1
        trace.versions[path] = trace.versions.get(path, 0) + 1

    def pyramid(path: str) -> list[tuple[int, int]]:
        """Record metadata reads, including the default cache-hit no-read contract."""
        trace.pyramids += 1
        return cast(list[tuple[int, int]], dimensions(path))

    def identity(path: str) -> list[str | int]:
        """Use stable paths and logical write times for deterministic merge fingerprints."""
        return [Path(path).name, 1, trace.versions.get(path, 0)]

    with (
        patch.object(implementation, "MicroscopyImageFeatureExtractor", create_encoder),
        patch.object(implementation, "write_h5ad_compat", write),
        patch.object(implementation, "ome_tiff_pyramid_dims", pyramid),
        patch.object(merger, "stat_signature", identity),
        patch.object(implementation, "image_signature", lambda path: "image"),
        patch(
            "sys.argv",
            [
                "feature_extraction_only.py",
                "-c",
                str(config),
                "--resolution-levels",
                str(levels),
            ],
        ),
    ):
        try:
            script.main()
        except SystemExit as error:
            assert error.code == 0
    return {
        "events": [trace.models, trace.encodings, trace.pyramids, trace.writes],
        "files": decoded_grid_files(root),
    }


def collect_grid_contract(
    script: ModuleType, implementation: ModuleType, root: Path
) -> list[dict[str, object]]:
    """Exercise initial computation, default cache hits, coarser levels, force, and invalidation."""
    prepare_grid_input(root)
    trace = GridTrace()
    return [
        run_grid_case(script, implementation, root, trace, levels, force, color)
        for levels, force, color in [
            (1, False, "white"),
            (1, False, "white"),
            (2, False, "white"),
            (2, True, "white"),
            (2, False, "black"),
        ]
    ]
