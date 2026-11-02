"""Readout behavioral scenarios."""

import pytest  # noqa: E402 - import follows runtime setup
from typing import TYPE_CHECKING  # noqa: E402 - import follows runtime setup

torch = pytest.importorskip("torch")
if TYPE_CHECKING:
    from focus.registration.encoder import MicroscopyImageFeatureExtractor
else:
    MicroscopyImageFeatureExtractor = pytest.importorskip(
        "focus.registration.encoder"
    ).MicroscopyImageFeatureExtractor

import numpy as np  # noqa: E402 - import follows runtime setup

from focus.registration.readout import (  # noqa: E402 - import follows runtime setup
    Chunk,
    LevelGeometry,
    plan_windows,
    readout_membership,
)  # noqa: E402 - import follows runtime setup
from focus.registration.windows import (  # noqa: E402 - import follows runtime setup
    cut_window_batch,
    resolve_bg_color,
    token_background_mask,
)  # noqa: E402 - import follows runtime setup
from tests.encoding.data import tiny_vit, lattice_geometry  # noqa: E402 - import follows runtime setup


@pytest.mark.skipif(torch is None, reason="torch/timm are not installed")
class TestReadout:
    """Behavioral contracts for readout."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.extractor = MicroscopyImageFeatureExtractor(path=".", model=tiny_vit())

    def test_full_membership_reproduces_the_cls(self) -> None:
        """Verify full membership reproduces the cls."""
        rng = np.random.default_rng(0)
        for h, w in ((64, 64), (48, 64), (96, 96)):
            x = rng.random((2, h, w, 3), dtype=np.float32)
            standard = self.extractor._encode_np_batch(x)
            member = np.ones((2, 1, (h // 16) * (w // 16)), dtype=bool)
            np.testing.assert_allclose(
                self.extractor._encode_readouts(x, member)[:, 0],
                standard,
                rtol=1e-4,
                atol=1e-5,
            )

    def test_readouts_are_independent(self) -> None:
        """Verify readouts are independent."""
        rng = np.random.default_rng(1)
        x = rng.random((1, 64, 64, 3), dtype=np.float32)
        member = rng.random((1, 6, 16)) < 0.4
        member[:, :, 0] = True
        together = self.extractor._encode_readouts(x, member)[0]
        for r in range(6):
            alone = self.extractor._encode_readouts(x, member[:, r : r + 1])[0, 0]
            np.testing.assert_allclose(together[r], alone, rtol=1e-4, atol=1e-5)
        perm = np.array([3, 0, 5, 1, 4, 2])
        np.testing.assert_allclose(
            self.extractor._encode_readouts(x, member[:, perm])[0],
            together[perm],
            rtol=1e-4,
            atol=1e-5,
        )
        assert np.abs(together[0] - together[1]).max() > 1e-3

    def test_spot_features_match_a_single_spot_reference(self) -> None:
        """Verify spot features match a single spot reference."""
        rng = np.random.default_rng(2)
        image = np.ones((400, 500, 3), np.float32)
        image[60:340, 80:300] = rng.random((280, 220, 3), dtype=np.float32) * 0.8
        spatial, mapped, geom = lattice_geometry(
            12, 20.0, 12.0, 0.5, (100.0, 90.0)
        )  # 24 px footprints
        level = LevelGeometry(0, image.shape[:2], image.shape[:2], 0.5)
        out = self.extractor.extract_spot_features(image, mapped, geom, level)

        plan = plan_windows(geom, mapped, level, self.extractor.grid)
        bg = resolve_bg_color("white")
        for chunk in plan.chunks:
            h, w = chunk.shape
            window = cut_window_batch(image, np.array([chunk.origin]), h, w)
            tokens_bg = token_background_mask(window, 16, bg)[0]
            for spot in chunk.spots[:3]:
                member, keep = readout_membership(
                    Chunk(chunk.origin, chunk.shape, np.array([spot])),
                    plan,
                    geom,
                    level,
                    self.extractor.grid,
                    tokens_bg,
                )
                if not keep[0]:
                    assert np.array_equal(out[spot], np.zeros_like(out[spot]))
                    continue
                ref = self.extractor._encode_readouts(window, member[None])[0, 0]
                np.testing.assert_allclose(out[spot], ref, rtol=1e-4, atol=1e-5)

        x_px = mapped[:, 0]
        y_px = mapped[:, 1]
        outside = (
            (x_px + 12 < 80) | (x_px - 12 > 300) | (y_px + 12 < 60) | (y_px - 12 > 340)
        )
        assert outside.any()
        assert np.all(out[outside] == 0)
        assert np.all(np.isfinite(out)) and np.abs(out[~outside]).sum(axis=1).min() > 0

    def test_grid_features_keep_the_standard_cls(self) -> None:
        """Verify grid features keep the standard cls."""
        rng = np.random.default_rng(3)
        image = np.ones((128, 192, 3), np.float32)
        image[:64, :64] = rng.random((64, 64, 3), dtype=np.float32)
        emb, centres = self.extractor.extract_grid_features(image, patch_size=64)
        assert emb.shape == (1, 32)
        np.testing.assert_allclose(centres, [[32.0, 32.0]])
        np.testing.assert_allclose(
            emb[0], self.extractor._encode_np_batch(image[None, :64, :64])[0], rtol=1e-5
        )
