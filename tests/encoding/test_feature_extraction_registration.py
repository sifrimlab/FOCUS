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
