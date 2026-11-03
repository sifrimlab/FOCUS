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
from focus.registration.encoder import MicroscopyImageFeatureExtractor
from focus.registration.readout import LevelGeometry, select_encoding_level
from focus.registration.logging import logger
from focus.runtime.reporting import StepReporter
from focus.spatial.footprint import AnchorGeometry


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
    ) -> None:
        """Bind source maps and options without reading images or constructing a model."""
        self._path = path
        self._hf_token = hf_token
        self.image_files = image_files
        self.anchor_type = anchor_type
        self.background_color = background_color
        self.reporter = reporter
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
        embeddings = self.extractor.extract_spot_features(
            image,
            anchor.mapped,
            geom,
            level,
            background_color=self.background(sample_id),
            step_reporter=self.reporter,
        )
        del image
        return encoded_sample(embeddings, anchor, sample_id, level)


def encoded_sample(
    embeddings: np.ndarray,
    anchor: AnchorArrays,
    sample_id: str,
    level: LevelGeometry,
) -> tuple[AnnData, dict[str, object]]:
    """Wrap (N, D) embeddings and copied level-0 coordinates with encoding metadata."""
    adata = anndata.AnnData(
        X=embeddings,
        obsm={"spatial": anchor.mapped.copy()},
        obs={"sample_id": [sample_id] * anchor.n_obs},
    )
    extra = {
        "encoder": MicroscopyImageFeatureExtractor.MODEL_ID,
        "encoded_level": int(level.level),
    }
    if level.um_per_px is not None:
        extra["encoded_um_per_px"] = float(level.um_per_px)
    logger.debug(f"Saved {anchor.n_obs} spot embeddings for sample '{sample_id}'")
    return adata, extra
