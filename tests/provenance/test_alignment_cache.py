"""AlignmentCache behavioral scenarios."""

from typing import cast

import os
import tempfile
from pathlib import Path

import anndata as ad
import numpy as np

from focus.alignment.coordinator import DirectMappingAligner
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.io.provenance import (
    read_ome_annotations,
    cached_fingerprint,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.runtime.reporter_context import get_reporter
from tests.provenance.data import _OFFSET, _write_source, _strip_provenance


class TestAlignmentCache:
    """Behavioral contracts for alignment cache."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_source(self.root / "s" / "he" / "img.tif")
        self.sample = MicroscopyImage(str(self.root), "s", "he")
        self.image = self.sample.process_image(crop_margin=100)

        rng = np.random.default_rng(0)
        self.spatial = rng.uniform([800, 1200], [1500, 2000], size=(40, 2)).astype(
            np.float32
        )
        anchor = ad.AnnData(np.zeros((40, 1), np.float32))
        anchor.obsm["spatial"] = self.spatial
        anchor.uns["focus_preprocessing_fingerprint"] = "anchor-fingerprint"
        self.anchor = str(self.root / "anchor.h5ad")
        anchor.write_h5ad(self.anchor)

        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._path = str(self.root)
        aligner._target_modality = {"s": self.anchor}
        aligner._reference_modality = {"s": self.image}
        aligner._target_modality_type = "st"
        aligner._reference_modality_type = "microscopy_image"
        aligner._target_modality_name = "st"
        aligner._reference_modality_name = "he"
        aligner._common_samples = ["s"]
        aligner._reporter = get_reporter()
        self.aligner = aligner

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        self.tmp.cleanup()

    def _stamp_manual_alignment(self) -> str:
        """An aligned anchor file whose key was computed against the image as it is now."""
        adata = ad.read_h5ad(self.anchor)
        adata.obsm["he_spatial"] = self.spatial + 5
        adata.uns["focus_alignment_inputs"] = {"he": self.aligner.cache._input_key("s")}
        path = self.aligner.cache._aligned_output_path("s")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        adata.write_h5ad(path)
        return path

    def test_pixel_identical_regeneration_keeps_a_legacy_alignment(self) -> None:
        """Verify pixel identical regeneration keeps a legacy alignment."""
        _strip_provenance(self.image)
        assert cached_fingerprint(self.image) is None
        path = self._stamp_manual_alignment()  # stat-based legacy key
        self.sample.process_image(crop_margin=100)  # no fingerprint: regenerated
        assert SUPERSEDED_SIGNATURE_KEY in cast(
            dict[str, str], read_ome_annotations(self.image)
        )
        assert self.aligner.cache._cache_matches("s")
        stored = ad.read_h5ad(path).uns["focus_alignment_inputs"]["he"]
        assert stored == self.aligner.cache._input_key("s")
        assert self.aligner.cache._cache_matches("s")  # idempotent

    def test_changed_pixels_invalidate_a_legacy_alignment(self) -> None:
        """Verify changed pixels invalidate a legacy alignment."""
        _strip_provenance(self.image)
        self._stamp_manual_alignment()
        self.sample.process_image(crop_margin=60)
        assert SUPERSEDED_SIGNATURE_KEY not in cast(
            dict[str, str], read_ome_annotations(self.image)
        )
        assert not self.aligner.cache._cache_matches("s")

    def test_current_alignment_matches_and_survives_a_pixel_identical_rerun(
        self,
    ) -> None:
        """Verify current alignment matches and survives a pixel identical rerun."""
        self._stamp_manual_alignment()
        assert self.aligner.cache._cache_matches("s")
        self.sample.process_image(crop_margin=100, force_recomputing=True)
        assert self.aligner.cache._cache_matches("s")

    def test_pre_aligned_coordinates_subtract_the_crop_offset(self) -> None:
        """Verify pre aligned coordinates subtract the crop offset."""
        aligned = self.aligner.uniform_aligned_dataset()
        result = ad.read_h5ad(aligned["s"])
        np.testing.assert_allclose(result.obsm["he_spatial"], self.spatial - _OFFSET)
        assert self.aligner.cache._cache_matches("s", pre_aligned=True)

        self.sample.process_image(crop_margin=60)
        assert not self.aligner.cache._cache_matches("s", pre_aligned=True)
        aligned = self.aligner.uniform_aligned_dataset()
        np.testing.assert_allclose(
            ad.read_h5ad(aligned["s"]).obsm["he_spatial"], self.spatial - (_OFFSET + 40)
        )
