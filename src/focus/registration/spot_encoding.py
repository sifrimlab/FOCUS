"""Per-run microscopy encoder state, cache settings and anchor-spot encoding."""

from __future__ import annotations
from focus.registration.types import RegistrationJob
from focus.config.options import RegistrationType, SegmentationBackgroundColor
from focus.io.provenance import read_ome_annotations

import anndata
import numpy as np
from anndata import AnnData

from focus.io.ome import (
    read_ome_physical_size_um,
    ome_tiff_pyramid_dims,
    load_ome_tiff_level,
)
from focus.registration.anchor import AnchorArrays, resolve_um_per_px
from focus.registration.encoder import MicroscopyImageFeatureExtractor, SpotFeatures
from focus.registration.readout import (
    LevelGeometry,
    patch_anchor_xy,
    select_encoding_level,
)
from focus.registration.logging import logger
from focus.runtime.reporting import StepReporter
from focus.spatial.footprint import AnchorGeometry

# Identifies the spot readout in the cache fingerprint; bump it when the embeddings change.
_ALGORITHM = "token-mean-1"


class SpotEncoding:
    """Retain one lazily created encoder across the ordered samples of one registration."""

    def __init__(
        self,
        path: str,
        hf_token: str | None,
        image_files: dict[str, str],
        anchor_type: str | None,
        background_color: str,
        reporter: StepReporter,
        patch_embeddings: bool = False,
    ) -> None:
        """Bind source maps and options without reading images or constructing a model.

        With ``patch_embeddings`` every spot also stores the embedding of its encoder patch
        (``obsm['patch_embedding']``), the patch centre in the anchor frame
        (``obsm['patch_spatial']``) and the patch index (``obs['patch_id']``)."""
        self._path = path
        self._hf_token = hf_token
        self.image_files = image_files
        self.anchor_type = anchor_type
        self.background_color = background_color
        self.reporter = reporter
        self.patch_embeddings = bool(patch_embeddings)
        self.extractor: MicroscopyImageFeatureExtractor | None = None

    def job(
        self,
        anchor_files: dict[str, str],
        anchor_name: str,
        image_name: str,
        common_samples: list[str],
        force_recomputing: bool,
    ) -> RegistrationJob:
        """Declare the ordered per-sample encoding job without consuming its sample iterator."""
        return RegistrationJob(
            path=self._path,
            samples=self.reporter.samples(common_samples),
            anchor_files=anchor_files,
            target_files=self.image_files,
            anchor_name=anchor_name,
            target_name=image_name,
            registration_type=RegistrationType.FEATURE_EXTRACTION,
            settings=self.settings,
            compute=self.compute,
            force_recomputing=force_recomputing,
            reporter=self.reporter,
        )

    def background(self, sample_id: str) -> str:
        """Fill colour of removed background: the image's own record, else the setting.

        Preprocessing records ``focus_background_fill`` (``none``, 0 or the sample maximum),
        so a filled image is skipped with the colour it was actually filled with.
        """
        fill = (read_ome_annotations(self.image_files[sample_id]) or {}).get(
            "focus_background_fill", "none"
        )
        if fill == "none":
            return str(self.background_color)
        if float(fill) == 0:
            return SegmentationBackgroundColor.BLACK
        return SegmentationBackgroundColor.WHITE

    def settings(self, sample_id: str) -> dict[str, object]:
        """Build the registration settings used to fingerprint this sample."""
        size = read_ome_physical_size_um(self.image_files[sample_id])
        return {
            "background_color": self.background(sample_id),
            "model": MicroscopyImageFeatureExtractor.MODEL_ID,
            "anchor_type": str(self.anchor_type),
            "physical_size_um": None if size is None else [float(v) for v in size],
            "algorithm": _ALGORITHM,
            "patch_embeddings": self.patch_embeddings,
        }

    def compute(
        self, sample_id: str, anchor: AnchorArrays, geom: AnchorGeometry
    ) -> tuple[AnnData | None, dict[str, object]]:
        """Compute one sample's registered features and geometry metadata."""
        image_path = self.image_files[sample_id]
        um0 = resolve_um_per_px(image_path, geom, self.anchor_type, self.reporter)
        level = select_encoding_level(um0, ome_tiff_pyramid_dims(image_path))
        if level.um_per_px is not None:
            self.reporter.detail(
                f"Encoding pyramid level {level.level} ({level.um_per_px:.3g} µm/px)"
            )

        if self.extractor is None:
            self.reporter.step(1, 3, "Loading feature extractor")
            self.extractor = MicroscopyImageFeatureExtractor(
                path=self._path, hf_token=self._hf_token
            )

        image = load_ome_tiff_level(image_path, level.level)
        features = self.extractor.extract_spot_features(
            image,
            anchor.mapped,
            geom,
            level,
            background_color=self.background(sample_id),
            patch_embeddings=self.patch_embeddings,
            step_reporter=self.reporter,
        )
        del image
        patch_spatial = None
        if features.patch_id is not None:
            patch_spatial = patch_anchor_xy(
                geom, features.lattice, level, features.patch_id, anchor.mapped
            )
        return encoded_sample(features, anchor, sample_id, level, patch_spatial)


def encoded_sample(
    features: SpotFeatures,
    anchor: AnchorArrays,
    sample_id: str,
    level: LevelGeometry,
    patch_spatial: np.ndarray | None = None,
) -> tuple[AnnData, dict[str, object]]:
    """Wrap the spot embeddings, copied level-0 coordinates and any patch layer with metadata.

    ``X`` holds the footprint embeddings and ``obsm['spatial']`` the spots in level-0 image
    pixels. With patch embeddings, ``obsm['patch_embedding']`` is each spot's patch CLS,
    ``obsm['patch_spatial']`` the patch centre in the anchor frame and ``obs['patch_id']`` the
    patch index (unique within the sample, -1 for spots without a patch)."""
    adata = anndata.AnnData(
        X=features.x,
        obsm={"spatial": anchor.mapped.copy()},
        obs={"sample_id": [sample_id] * anchor.n_obs},
    )
    with_patches = features.patch_id is not None
    if features.patch_id is not None:
        assert features.patch_embedding is not None and patch_spatial is not None
        adata.obsm["patch_embedding"] = features.patch_embedding
        adata.obsm["patch_spatial"] = patch_spatial.astype(np.float32)
        adata.obs["patch_id"] = features.patch_id.astype(np.int64)
    lattice = features.lattice
    h, w = lattice.window_shape
    extra: dict[str, object] = {
        "encoder": MicroscopyImageFeatureExtractor.MODEL_ID,
        "encoded_level": int(level.level),
        "readout": "token_mean",
        "window_px": [int(h), int(w)],
        "stride_px": int(lattice.stride),
        "patch_embeddings": with_patches,
    }
    if level.um_per_px is not None:
        extra["encoded_um_per_px"] = float(level.um_per_px)
        extra["window_um"] = [float(h * level.um_per_px), float(w * level.um_per_px)]
    logger.debug(f"Saved {anchor.n_obs} spot embeddings for sample '{sample_id}'")
    return adata, extra
