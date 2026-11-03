"""Execute standalone grid encoding with per-level caching and lazy model loading."""

import logging
import os
from typing import cast

import anndata

from focus.config.options import RegistrationType
from focus.io.anndata import write_h5ad_compat
from focus.io.ome import load_ome_tiff_level, ome_tiff_pyramid_dims
from focus.io.paths import merged_registration_file, registration_file
from focus.registration.encoder import MicroscopyImageFeatureExtractor
from focus.registration.free_form_cache import (
    _level_cache_valid,
    _res_level_path,
    image_signature,
)
from focus.registration.free_form_data import grid_anndata, level0_centers
from focus.config.grid_settings import GridSettings
from focus.registration.merging import merge_registered_samples
from focus.runtime.resources import release_memory

_H5AD_COMPRESSION = "gzip"


class GridExtraction:
    """Coordinate the existing grid workflow without changing model or cache policy."""

    def __init__(
        self,
        settings: GridSettings,
        samples: list[str],
        files: dict[str, str],
        logger: logging.Logger,
    ) -> None:
        """Bind resolved inputs and allocate path maps; keep model construction lazy."""
        self.settings = settings
        self.samples = samples
        self.files = files
        self.logger = logger
        self.encoder: MicroscopyImageFeatureExtractor | None = None
        self.outputs: dict[int, dict[str, str]] = {}

    def run(self) -> None:
        """Encode ordered samples, release the model, merge levels, and log outputs."""
        for index, sample in enumerate(self.samples, 1):
            self.logger.info(
                f"[{index}/{len(self.samples)}] Extracting features for sample '{sample}'"
            )
            self.process_sample(sample)
        if self.encoder is not None:
            self.encoder = None
        self.merge_levels()
        release_memory(gpu=True)
        self.log_outputs()

    def process_sample(self, sample: str) -> None:
        """Reuse valid level files and load the model only when a level needs encoding."""
        image_path = self.files[sample]
        dimensions, count = self.pyramid(image_path, sample)
        needed = self.needed_files(sample, count)
        if not needed:
            return
        if self.encoder is None:
            self.logger.info("Loading feature extractor model (Prov-GigaPath)...")
            self.encoder = MicroscopyImageFeatureExtractor(
                path=self.settings.path, hf_token=self.settings.token
            )
        for level, target in needed:
            self.encode_level(image_path, sample, level, target, dimensions)

    def pyramid(
        self, path: str, sample: str
    ) -> tuple[list[tuple[int, int]] | None, int]:
        """Inspect stored levels only for a coarser request, preserving default cache hits."""
        if self.settings.levels > 1:
            dimensions = ome_tiff_pyramid_dims(path)
            available = len(dimensions)
            count = min(self.settings.levels, available)
            if self.settings.levels > available:
                self.logger.warning(
                    f"Sample '{sample}': requested {self.settings.levels} resolution level(s) but its pyramid "
                    f"has only {available}; extracting {available} level(s) for this sample."
                )
            return dimensions, count
        return None, 1

    def needed_files(self, sample: str, count: int) -> list[tuple[int, str]]:
        """Collect missing or stale level files in level order without reading image pixels."""
        needed: list[tuple[int, str]] = []
        for level in range(count):
            target = _res_level_path(
                registration_file(
                    self.settings.path, sample, self.settings.modality, "h5ad"
                ),
                level,
            )
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.outputs.setdefault(level, {})
            if os.path.exists(target) and not self.settings.force:
                cached = anndata.read_h5ad(target)
                valid = _level_cache_valid(
                    cached,
                    level,
                    self.settings.patch_size,
                    self.settings.background,
                    image_signature(self.files[sample]),
                )
                del cached
                if valid:
                    self.logger.info(
                        f"Using cached free-form registration for sample '{sample}' (res level {level})"
                    )
                    self.outputs[level][sample] = target
                    continue
                self.logger.warning(
                    f"Cached registration for '{sample}' (res level {level}) is stale or from a "
                    "different mode; recomputing."
                )
            needed.append((level, target))
        return needed

    def encode_level(
        self,
        path: str,
        sample: str,
        level: int,
        target: str,
        dimensions: list[tuple[int, int]] | None,
    ) -> None:
        """Encode one stored level and write centers in the original full-resolution frame."""
        image = load_ome_tiff_level(path, level=level)
        embeddings, centers = cast(
            MicroscopyImageFeatureExtractor, self.encoder
        ).extract_grid_features(
            image=image,
            background_color=self.settings.background,
            patch_size=self.settings.patch_size,
            step_reporter=None,
        )
        if embeddings.shape[0] == 0:
            self.logger.warning(
                f"Sample '{sample}' (res level {level}): no foreground patches found "
                "(fully background, or level smaller than one patch)."
            )
        if level > 0:
            centers = level0_centers(
                centers, cast(list[tuple[int, int]], dimensions), level
            )
        data = grid_anndata(
            embeddings, centers, sample, self.settings, level, dimensions
        )
        data.uns["image_signature"] = image_signature(path)
        write_h5ad_compat(data, target, compression=_H5AD_COMPRESSION)
        self.outputs[level][sample] = target
        self.logger.debug(
            f"Saved {embeddings.shape[0]} patch embeddings for sample '{sample}' (res level {level})"
        )
        del image, embeddings, centers, data

    def merge_levels(self) -> None:
        """Rebuild affected sample merges with their existing level names and metadata."""
        for level in sorted(self.outputs):
            if level == 0:
                merged = None
                extra = None
            else:
                merged = _res_level_path(
                    merged_registration_file(
                        self.settings.path, self.settings.modality, "h5ad"
                    ),
                    level,
                )
                extra = {"res_level": level, "downsample_factor": float(2**level)}
            self.outputs[level] = merge_registered_samples(
                self.settings.path,
                self.outputs[level],
                self.settings.modality,
                RegistrationType.FEATURE_EXTRACTION,
                force_recomputing=self.settings.force,
                merged_file=merged,
                extra_uns=extra,
            )

    def log_outputs(self) -> None:
        """Report the same per-level and per-sample completion summary."""
        self.logger.info("=" * 60)
        self.logger.info("Feature extraction complete.")
        for level in sorted(self.outputs):
            files = self.outputs[level]
            merged = files.get("merged")
            samples = {key: value for key, value in files.items() if key != "merged"}
            label = "full resolution" if level == 0 else f"resolution level {level}"
            self.logger.info(
                f"  '{self.settings.modality}' [{label}]: {len(samples)} per-sample file(s)"
                + (f"; merged: {merged}" if merged else "; (no merged file produced)")
            )
            for sample, path in samples.items():
                self.logger.info(f"      {sample}: {path}")
        self.logger.info("=" * 60)
