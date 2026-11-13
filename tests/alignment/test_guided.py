"""Guided alignment: composing reference -> guide with the inverse of guided -> guide."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import cast

import anndata as ad
import numpy as np
import pytest

from focus.alignment.guided import GuidedAligner
from focus.config.guides import validate_alignment_guides
from focus.config.types import ModalityConfig, PipelineConfig
from focus.io.paths import alignment_file
from focus.spatial.thin_plate import apply_homography
from focus.spatial.warp import LandmarkWarp
from tests.registration.data import square_lattice

H_RG = np.array([[2.0, 0.1, 300.0], [-0.08, 2.1, 150.0], [0.0, 0.0, 1.0]])
H_TG = np.array([[0.5, 0.02, 40.0], [-0.03, 0.52, 25.0], [0.0, 0.0, 1.0]])
SCALE = np.array([4.0, 4.0])  # guide display -> full resolution


def _guided_warp() -> LandmarkWarp:
    """A guided -> guide warp with a smooth bend from five pins (display frame)."""
    src = np.array([[0, 0], [1200, 0], [1200, 1200], [0, 1200], [600, 600]], float)
    dst = apply_homography(H_TG, src) + [[0, 0], [5, -3], [0, 4], [-4, 0], [9, -7]]
    return LandmarkWarp(H_TG, src, dst, smoothing=0.0, fixed_scale=SCALE)


class TestGuidedComposition:
    """The reference reaches the guided modality through the guide."""

    def setup_method(self) -> None:
        """Reference (ST) aligned to the guide (H&E); MSI to be aligned onto the H&E."""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = str(root)
        self.spatial = square_lattice(15, 100.0, origin=(100.0, 100.0)).astype(
            np.float32
        )
        st = ad.AnnData(np.zeros((len(self.spatial), 1), np.float32))
        st.obsm["spatial"] = self.spatial
        st.uns["focus_preprocessing_fingerprint"] = "st-v1"
        self.st = str(root / "st.h5ad")
        st.write_h5ad(self.st)
        msi = ad.AnnData(np.zeros((4, 1), np.float32))
        msi.obsm["spatial"] = np.zeros((4, 2), np.float32)
        msi.uns["focus_preprocessing_fingerprint"] = "msi-v1"
        self.msi = str(root / "msi.h5ad")
        msi.write_h5ad(self.msi)
        he = ad.AnnData(np.zeros((1, 1), np.float32))
        he.uns["focus_preprocessing_fingerprint"] = "he-v1"
        self.he = str(
            root / "he.h5ad"
        )  # any file with a fingerprint identifies the guide
        he.write_h5ad(self.he)
        self.aligned = alignment_file(self.root, "s", "ST", "h5ad")
        os.makedirs(os.path.dirname(self.aligned), exist_ok=True)
        self._align_reference_to_guide(H_RG, "rg-v1")

    def teardown_method(self) -> None:
        """Release the temporary dataset."""
        self.tmp.cleanup()

    def _align_reference_to_guide(self, matrix: np.ndarray, key: str) -> None:
        """Store the reference -> guide pair as the alignment stage would."""
        adata = ad.read_h5ad(self.st)
        adata.obsm["HE_spatial"] = apply_homography(matrix, self.spatial).astype(
            np.float32
        )
        adata.uns["focus_alignment_inputs"] = {"HE": key}
        if os.path.exists(self.aligned):
            old = ad.read_h5ad(self.aligned)
            for k in ("MSI_spatial",):
                if k in old.obsm:
                    adata.obsm[k] = old.obsm[k]
            adata.uns["focus_alignment_inputs"].update(
                {
                    k: v
                    for k, v in old.uns.get("focus_alignment_inputs", {}).items()
                    if k != "HE"
                }
            )
            if "focus_alignment_warps" in old.uns:
                adata.uns["focus_alignment_warps"] = dict(
                    old.uns["focus_alignment_warps"]
                )
        adata.write_h5ad(self.aligned)

    def _aligner(self) -> GuidedAligner:
        """A guided aligner: MSI (moving) onto H&E (fixed), reference ST."""
        return GuidedAligner(
            path=self.root,
            guide_files={"s": self.he},
            moving_files={"s": self.msi},
            guide_name="HE",
            moving_name="MSI",
            guide_type="microscopy_image",
            moving_type="msi",
            anchor_name="ST",
            anchor_files={"s": self.st},
        )

    def _stored(self) -> ad.AnnData:
        """The reference's aligned file."""
        return ad.read_h5ad(self.aligned)

    def test_confirmed_warp_is_composed_into_the_reference_file(self) -> None:
        """``obsm['MSI_spatial']`` is the inverse warp of the reference's guide positions."""
        aligner = self._aligner()
        assert aligner.is_alignment_needed()
        warp = _guided_warp()
        aligner._warps["s"] = warp.to_json()
        aligner._save_results({})
        out = self._stored()
        in_guide = apply_homography(H_RG, self.spatial.astype(np.float64))
        np.testing.assert_allclose(
            out.obsm["MSI_spatial"], warp.inverse_full(in_guide), rtol=1e-5
        )
        # Going forward through the guided warp lands back on the reference's guide positions.
        np.testing.assert_allclose(
            warp.map_full(out.obsm["MSI_spatial"]), in_guide, atol=1e-2
        )
        # The reference -> guide pair is untouched, and the guided warp is recorded.
        np.testing.assert_array_equal(
            out.obsm["HE_spatial"],
            apply_homography(H_RG, self.spatial).astype(np.float32),
        )
        assert '"guide":"HE"' in out.uns["focus_alignment_warps"]["MSI"]
        assert not self._aligner().is_alignment_needed()

    def test_new_reference_alignment_recomposes_without_the_tool(self) -> None:
        """Re-aligning the reference to the guide re-composes from the stored guided warp."""
        aligner = self._aligner()
        warp = _guided_warp()
        aligner._warps["s"] = warp.to_json()
        aligner._save_results({})
        moved = H_RG @ np.array([[1.0, 0, 25.0], [0, 1.0, -10.0], [0, 0, 1.0]])
        self._align_reference_to_guide(moved, "rg-v2")
        again = self._aligner()
        assert not again.is_alignment_needed()
        assert not again.cache.composition_current("s")
        again.collect_aligned_files()
        out = self._stored()
        in_guide = apply_homography(moved, self.spatial.astype(np.float64))
        np.testing.assert_allclose(
            out.obsm["MSI_spatial"], warp.inverse_full(in_guide), rtol=1e-5
        )
        assert self._aligner().cache.composition_current("s")

    def test_changed_guided_modality_needs_the_tool(self) -> None:
        """A re-preprocessed guided modality invalidates its stored warp."""
        aligner = self._aligner()
        aligner._warps["s"] = _guided_warp().to_json()
        aligner._save_results({})
        msi = ad.read_h5ad(self.msi)
        msi.uns["focus_preprocessing_fingerprint"] = "msi-v2"
        msi.write_h5ad(self.msi)
        assert self._aligner().is_alignment_needed()

    def test_stored_warp_is_offered_when_realigning(self) -> None:
        """The guided warp is read back under the guided modality's key."""
        aligner = self._aligner()
        warp = _guided_warp()
        aligner._warps["s"] = warp.to_json()
        aligner._save_results({})
        from focus.alignment.warps import read_stored_warp

        again = read_stored_warp(self.aligned, self._aligner()._warp_key)
        assert again is not None
        np.testing.assert_array_equal(again.dst, warp.dst)


# --- Configuration --------------------------------------------------------------------------


def _config(**msi: object) -> PipelineConfig:
    """ST reference, H&E pre-aligned, MSI optionally guided."""
    modalities = [
        {"name": "ST", "type": "st", "alignment_strategy": "manual"},
        {"name": "HE", "type": "microscopy_image", "alignment_strategy": "pre_aligned"},
        {"name": "MSI", "type": "msi", "alignment_strategy": "manual", **msi},
    ]
    return cast(
        PipelineConfig,
        {
            "reference_modality": "ST",
            "perform_alignment": True,
            "modalities": [cast(ModalityConfig, m) for m in modalities],
        },
    )


class TestGuideConfig:
    """``alignment_guide`` validation."""

    def test_valid_guide(self) -> None:
        """MSI guided by the H&E of the reference's section."""
        validate_alignment_guides(_config(alignment_guide="HE"), "ST")
        validate_alignment_guides(_config(alignment_guide=None), "ST")
        validate_alignment_guides(_config(), "ST")

    @pytest.mark.parametrize(
        "msi, match",
        [
            ({"alignment_guide": "MSI"}, "its own"),
            ({"alignment_guide": "ST"}, "reference"),
            ({"alignment_guide": "IF"}, "not a declared"),
            ({"alignment_guide": "HE", "alignment_strategy": "pre_aligned"}, "manual"),
            ({"alignment_guide": "HE", "type": "raman"}, "spot-based"),
        ],
    )
    def test_rejected(self, msi: dict[str, object], match: str) -> None:
        """Each invalid guide is rejected with a clear reason."""
        with pytest.raises(ValueError, match=match):
            validate_alignment_guides(_config(**msi), "ST")

    def test_guide_must_not_be_guided(self) -> None:
        """Guides are aligned to the reference directly (depth 1)."""
        config = _config(alignment_guide="HE")
        config["modalities"][1]["alignment_guide"] = "MSI"
        with pytest.raises(ValueError, match="itself guided"):
            validate_alignment_guides(config, "ST")

    def test_reference_cannot_be_guided(self) -> None:
        """The reference is never aligned."""
        config = _config()
        config["modalities"][0]["alignment_guide"] = "HE"
        with pytest.raises(ValueError, match="reference modality is not aligned"):
            validate_alignment_guides(config, "ST")

    def test_ignored_without_alignment(self) -> None:
        """With alignment off, guides are not checked."""
        config = _config(alignment_guide="nonexistent")
        config["perform_alignment"] = False
        validate_alignment_guides(config, "ST")
