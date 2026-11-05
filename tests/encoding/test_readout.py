"""Spot readout behavioral scenarios (torch, tiny ViT)."""

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
    LevelGeometry,
    build_lattice,
    footprint_tokens,
)
from focus.registration.windows import (  # noqa: E402 - import follows runtime setup
    cut_window_batch,
    resolve_bg_color,
    token_background_grid,
)
from tests.encoding.data import tiny_vit, lattice_geometry  # noqa: E402 - import follows runtime setup


def _tissue(
    shape: tuple[int, int], box: tuple[int, int, int, int], seed: int
) -> np.ndarray:
    """White image with a random tissue rectangle ``(y0, y1, x0, x1)``."""
    rng = np.random.default_rng(seed)
    image = np.ones(shape + (3,), np.float32)
    y0, y1, x0, x1 = box
    image[y0:y1, x0:x1] = rng.random((y1 - y0, x1 - x0, 3), dtype=np.float32) * 0.8
    return image


@pytest.mark.skipif(torch is None, reason="torch/timm are not installed")
class TestReadout:
    """Behavioral contracts for the spot readout."""

    def setup_method(self) -> None:
        """Create the isolated fixture state (96 px windows: a 3-token stride, so ties occur)."""
        self.extractor = MicroscopyImageFeatureExtractor(path=".", model=tiny_vit(96))

    def test_window_outputs_are_the_standard_forward(self) -> None:
        """The window CLS is the model's own embedding; tokens are its final-layer patch tokens."""
        rng = np.random.default_rng(0)
        x = rng.random((3, 96, 96, 3), dtype=np.float32)
        tokens, cls = self.extractor._encode_windows(x)
        np.testing.assert_allclose(
            cls, self.extractor._encode_np_batch(x), rtol=1e-5, atol=1e-6
        )
        m = self.extractor.patch_encoder
        with torch.inference_mode():
            feats = m.forward_features(self.extractor._to_device(x))
        assert tokens.shape == (3, 36, 32)
        np.testing.assert_allclose(
            tokens, feats[:, m.num_prefix_tokens :].cpu().numpy(), rtol=1e-5, atol=1e-6
        )

    def test_spot_features_match_a_per_window_reference(self) -> None:
        """Each embedding is the weighted mean of its tokens, each read from its own window alone."""
        image = _tissue((400, 500), (60, 340, 80, 300), seed=2)
        spatial, mapped, geom = lattice_geometry(
            12, 20.0, 12.0, 0.5, (100.0, 90.0)
        )  # 24 px footprints
        level = LevelGeometry(0, image.shape[:2], image.shape[:2], 0.5)
        out = self.extractor.extract_spot_features(image, mapped, geom, level)

        lattice = build_lattice(image.shape[:2], self.extractor.grid)
        bg = token_background_grid(image, 16, resolve_bg_color("white"))
        t = footprint_tokens(geom, mapped, level, lattice, bg)
        h, w = lattice.window_shape
        ref = np.zeros_like(out.x)
        for win in np.unique(t.window):
            window = cut_window_batch(image, lattice.origin([win]), h, w)
            tokens, _ = self.extractor._encode_windows(window)
            sel = t.window == win
            np.add.at(
                ref,
                t.spot[sel],
                (t.weight[sel] / t.total[t.spot[sel]])[:, None]
                * tokens[0, t.local[sel]],
            )
        np.testing.assert_allclose(out.x, ref, rtol=1e-4, atol=1e-5)

        x_px, y_px = mapped[:, 0], mapped[:, 1]
        outside = (
            (x_px + 12 < 80) | (x_px - 12 > 300) | (y_px + 12 < 60) | (y_px - 12 > 340)
        )
        assert outside.any()
        assert np.all(out.x[outside] == 0)
        assert (
            np.all(np.isfinite(out.x)) and np.abs(out.x[~outside]).sum(axis=1).min() > 0
        )
        assert out.patch_id is None and out.patch_embedding is None

    def test_results_do_not_depend_on_the_batch_size(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """Spots whose tokens fall in different batches get the same embedding."""
        image = _tissue((300, 360), (20, 280, 30, 330), seed=5)
        spatial, mapped, geom = lattice_geometry(10, 25.0, 30.0, 0.5, (40.0, 30.0))
        level = LevelGeometry(0, image.shape[:2], image.shape[:2], 0.5)
        many = self.extractor.extract_spot_features(
            image, mapped, geom, level, patch_embeddings=True
        )
        monkeypatch.setattr(
            "focus.registration.encoder.estimate_batch_size", lambda *a, **k: 1
        )
        one = self.extractor.extract_spot_features(
            image, mapped, geom, level, patch_embeddings=True
        )
        np.testing.assert_allclose(many.x, one.x, rtol=1e-5, atol=1e-6)
        assert many.patch_embedding is not None and one.patch_embedding is not None
        np.testing.assert_allclose(
            many.patch_embedding, one.patch_embedding, rtol=1e-5, atol=1e-6
        )
        np.testing.assert_array_equal(many.patch_id, one.patch_id)

    def test_patch_embeddings_are_the_cls_of_the_linked_window(self) -> None:
        """Each live spot carries its window's standard CLS; only zero rows have no patch."""
        image = _tissue((400, 500), (60, 340, 80, 300), seed=3)
        spatial, mapped, geom = lattice_geometry(12, 20.0, 12.0, 0.5, (100.0, 90.0))
        level = LevelGeometry(0, image.shape[:2], image.shape[:2], 0.5)
        out = self.extractor.extract_spot_features(
            image, mapped, geom, level, patch_embeddings=True
        )
        assert out.patch_id is not None and out.patch_embedding is not None
        zero = ~np.abs(out.x).any(axis=1)
        np.testing.assert_array_equal(out.patch_id == -1, zero)
        assert np.all(out.patch_embedding[zero] == 0)
        h, w = out.lattice.window_shape
        for win in np.unique(out.patch_id[out.patch_id >= 0]):
            window = cut_window_batch(image, out.lattice.origin([win]), h, w)
            cls = self.extractor._encode_np_batch(window)[0]
            rows = out.patch_embedding[out.patch_id == win]
            np.testing.assert_allclose(
                rows, np.broadcast_to(cls, rows.shape), rtol=1e-4, atol=1e-5
            )
        assert len(np.unique(out.patch_id[out.patch_id >= 0])) > 1

    def test_grid_features_keep_the_standard_cls(self) -> None:
        """Verify grid features keep the standard cls."""
        extractor = MicroscopyImageFeatureExtractor(path=".", model=tiny_vit())
        rng = np.random.default_rng(3)
        image = np.ones((128, 192, 3), np.float32)
        image[:64, :64] = rng.random((64, 64, 3), dtype=np.float32)
        emb, centres = extractor.extract_grid_features(image, patch_size=64)
        assert emb.shape == (1, 32)
        np.testing.assert_allclose(centres, [[32.0, 32.0]])
        np.testing.assert_allclose(
            emb[0], extractor._encode_np_batch(image[None, :64, :64])[0], rtol=1e-5
        )
