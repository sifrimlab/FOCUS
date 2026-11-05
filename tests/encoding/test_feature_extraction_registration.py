"""FeatureExtractionRegistration behavioral scenarios."""

import pytest  # noqa: E402 - import follows runtime setup
from typing import TYPE_CHECKING  # noqa: E402 - import follows runtime setup

torch = pytest.importorskip("torch")
if TYPE_CHECKING:
    from focus.registration.encoder import MicroscopyImageFeatureExtractor
else:
    MicroscopyImageFeatureExtractor = pytest.importorskip(
        "focus.registration.encoder"
    ).MicroscopyImageFeatureExtractor
from tests.assertions import assert_close  # noqa: E402 - import follows runtime setup

import numpy as np  # noqa: E402 - import follows runtime setup

from tests.encoding.data import tiny_vit  # noqa: E402 - import follows runtime setup


import os  # noqa: E402 - optional encoder dependency checked above
import tempfile  # noqa: E402 - optional encoder dependency checked above
from unittest.mock import patch  # noqa: E402 - optional encoder dependency checked above

import anndata as ad  # noqa: E402 - optional encoder dependency checked above
import tifffile  # noqa: E402 - optional encoder dependency checked above

from focus.io.provenance import cached_fingerprint  # noqa: E402 - optional encoder dependency checked above
from focus.registration.provenance import (  # noqa: E402 - optional encoder dependency checked above
    REGISTRATION_FINGERPRINT_KEY,
    REGISTRATION_GEOMETRY_KEY,
)
from focus.registration.feature_extraction import FeatureExtractorRegistration  # noqa: E402 - optional encoder dependency checked above


class TinyExtractor(MicroscopyImageFeatureExtractor):
    """Use a deterministic small ViT while retaining the production encoder interface."""

    def __init__(self, path: str, hf_token: str | None = None) -> None:
        """Construct the encoder with the test model and avoid remote authentication."""
        super().__init__(path, model=tiny_vit())


def encoding_inputs(tmp: str, rng: np.random.Generator) -> tuple[str, str, np.ndarray]:
    """Write two image levels at 0.25/0.5 µm/px and a 64-spot MSI alignment in level-0 pixels."""
    base = np.full((640, 800, 3), 255, np.uint8)
    base[100:540, 100:700] = rng.integers(0, 200, (440, 600, 3), dtype=np.uint8)
    image = os.path.join(tmp, "image.ome.tiff")
    with tifffile.TiffWriter(image, ome=True) as tif:
        for k, level in enumerate((base, base[::2, ::2])):
            tif.write(
                level,
                photometric="rgb",
                metadata={
                    "axes": "YXS",
                    "PhysicalSizeX": 0.25 * 2**k,
                    "PhysicalSizeY": 0.25 * 2**k,
                },
            )
    i, j = np.meshgrid(np.arange(8), np.arange(8))
    spatial = (
        np.column_stack([i.ravel(), j.ravel()]).astype(np.float32) * 20.0 + 10.0
    )  # MSI, µm
    anchor = ad.AnnData(X=np.zeros((64, 1), np.float32))
    anchor.obsm["spatial"] = spatial
    anchor.obsm["he_spatial"] = spatial / 0.25  # level-0 px
    anchor.uns["spot_size"] = np.array([20.0, 20.0], np.float32)
    anchor_path = os.path.join(tmp, "anchor.h5ad")
    anchor.write_h5ad(anchor_path)
    return image, anchor_path, spatial


@pytest.mark.skipif(torch is None, reason="torch/timm are not installed")
class TestFeatureExtractionRegistration:
    """Behavioral contracts for feature extraction registration."""

    def test_encodes_the_level_nearest_half_micron_and_caches(self) -> None:
        """Verify encodes the level nearest half micron and caches."""
        rng = np.random.default_rng(4)
        with tempfile.TemporaryDirectory() as tmp:
            image, anchor_path, spatial = encoding_inputs(tmp, rng)

            engine = FeatureExtractorRegistration(tmp)
            with patch(
                "focus.registration.spot_encoding.MicroscopyImageFeatureExtractor",
                TinyExtractor,
            ):
                files = engine.register_dataset(
                    {"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi"
                )
                out = ad.read_h5ad(files["s1"])
                assert out.X.shape == (64, 32)
                geometry = out.uns[REGISTRATION_GEOMETRY_KEY]
                assert (int(geometry["encoded_level"]), geometry["lattice"]) == (
                    1,
                    "square",
                )
                assert_close(float(geometry["encoded_um_per_px"]), 0.5)
                np.testing.assert_allclose(out.obsm["spatial"], spatial / 0.25)
                mtime = os.stat(files["s1"]).st_mtime_ns
                engine.register_dataset(
                    {"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi"
                )
                assert os.stat(files["s1"]).st_mtime_ns == mtime
                assert (
                    cached_fingerprint(files["s1"], key=REGISTRATION_FINGERPRINT_KEY)
                    is not None
                )

    def test_default_output_is_the_token_mean_without_patch_layers(self) -> None:
        """Without patch embeddings only X, the spot coordinates and the sample id are written."""
        rng = np.random.default_rng(5)
        with tempfile.TemporaryDirectory() as tmp:
            image, anchor_path, _ = encoding_inputs(tmp, rng)
            with patch(
                "focus.registration.spot_encoding.MicroscopyImageFeatureExtractor",
                TinyExtractor,
            ):
                files = FeatureExtractorRegistration(tmp).register_dataset(
                    {"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi"
                )
            out = ad.read_h5ad(files["s1"])
            assert list(out.obsm.keys()) == ["spatial"]
            assert list(out.obs.columns) == ["sample_id"]
            geometry = out.uns[REGISTRATION_GEOMETRY_KEY]
            assert geometry["readout"] == "token_mean"
            assert list(geometry["window_px"]) == [64, 64]
            assert int(geometry["stride_px"]) == 32
            assert not bool(geometry["patch_embeddings"])

    def test_patch_embeddings_add_the_patch_layer_and_change_the_cache_key(
        self,
    ) -> None:
        """The opt-in patch layer is written per spot, keeps X, and is a different cache entry."""
        from focus.registration.readout import TokenGrid, build_lattice

        rng = np.random.default_rng(6)
        with tempfile.TemporaryDirectory() as tmp:
            image, anchor_path, spatial = encoding_inputs(tmp, rng)
            engine = FeatureExtractorRegistration(tmp)
            with patch(
                "focus.registration.spot_encoding.MicroscopyImageFeatureExtractor",
                TinyExtractor,
            ):
                files = engine.register_dataset(
                    {"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi"
                )
                plain = ad.read_h5ad(files["s1"])
                key = cached_fingerprint(files["s1"], key=REGISTRATION_FINGERPRINT_KEY)
                files = engine.register_dataset(
                    {"s1": image, "s2": image},
                    {"s1": anchor_path, "s2": anchor_path},
                    "he",
                    "msi",
                    anchor_type="msi",
                    patch_embeddings=True,
                )
            out = ad.read_h5ad(files["s1"])
            assert (
                cached_fingerprint(files["s1"], key=REGISTRATION_FINGERPRINT_KEY) != key
            )
            np.testing.assert_allclose(out.X, plain.X, rtol=1e-5, atol=1e-6)
            assert out.obsm["patch_embedding"].shape == (64, 32)
            assert out.obsm["patch_spatial"].shape == (64, 2)
            patch_id = out.obs["patch_id"].to_numpy()
            assert patch_id.dtype == np.int64
            # Spots on the white border read nothing: no X, no patch.
            linked = patch_id >= 0
            np.testing.assert_array_equal(linked, np.abs(out.X).any(axis=1))
            assert 0 < linked.sum() < len(linked)
            assert np.all(out.obsm["patch_embedding"][~linked] == 0)
            assert np.isnan(out.obsm["patch_spatial"][~linked]).all()
            for pid in np.unique(patch_id[linked]):
                rows = out.obsm["patch_embedding"][patch_id == pid]
                assert np.all(rows == rows[0])
            # Level 1 is encoded at 0.5 µm/px and the anchor is in µm: centre_px * 0.5 = µm.
            lattice = build_lattice((320, 400), TokenGrid(patch=16, base_side=64))
            np.testing.assert_allclose(
                out.obsm["patch_spatial"][linked],
                lattice.centre(patch_id[linked]) * 0.5,
                atol=1e-4,
            )
            # Every spot lies inside its patch (32 px window = 16 µm half-side).
            offset = np.abs(out.obsm["patch_spatial"][linked] - spatial[linked])
            assert np.all(offset <= 16.0 + 1e-6)
            assert bool(out.uns[REGISTRATION_GEOMETRY_KEY]["patch_embeddings"])

            merged = ad.read_h5ad(files["merged"])
            assert merged.n_obs == 128
            assert {"spatial", "patch_embedding", "patch_spatial"} <= set(merged.obsm)
            np.testing.assert_array_equal(
                merged.obs["patch_id"].to_numpy(), np.r_[patch_id, patch_id]
            )
