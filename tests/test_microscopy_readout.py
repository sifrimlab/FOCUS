"""Footprint-restricted CLS readout. Run with: PYTHONPATH=src python -m unittest discover -s tests -v."""
import unittest

import numpy as np

from focus.registration._footprint import AnchorGeometry
from focus.registration._readout_plan import (
    Chunk, LevelGeometry, TokenGrid, plan_windows, readout_membership, select_encoding_level,
)
from focus.registration._utils import cut_window_batch, resolve_bg_color, token_background_mask

try:
    import torch
    from timm.models.vision_transformer import VisionTransformer
    from focus.registration.microscopy_image import MicroscopyImageFeatureExtractor
except ImportError:  # the encoder stack is optional for the rest of the suite
    torch = None


def tiny_vit(img_size=64):
    torch.manual_seed(0)
    model = VisionTransformer(
        img_size=img_size, patch_size=16, embed_dim=32, depth=2, num_heads=2,
        num_classes=0, dynamic_img_size=True,
    )
    with torch.no_grad():
        model.cls_token.normal_(std=1.0)
        model.pos_embed.normal_(std=1.0)
    return model


def lattice_geometry(n, pitch_um, spot_um, um_per_px, offset_px):
    i, j = np.meshgrid(np.arange(n), np.arange(n))
    spatial = np.column_stack([i.ravel(), j.ravel()]).astype(np.float64) * pitch_um
    mapped = spatial / um_per_px + offset_px
    return spatial, mapped, AnchorGeometry.from_arrays(spatial, mapped, np.array([spot_um, spot_um]))


class LevelTests(unittest.TestCase):
    def test_nearest_level_without_resampling(self):
        dims = [(1001, 1001), (500, 500), (250, 250)]
        self.assertEqual(select_encoding_level(0.25, dims).level, 1)
        self.assertEqual(select_encoding_level(0.5, dims).level, 0)
        self.assertEqual(select_encoding_level(1.0, dims).level, 0)
        self.assertEqual(select_encoding_level(0.13, dims).level, 2)
        none = select_encoding_level(None, dims)
        self.assertEqual((none.level, none.um_per_px), (0, None))
        level = select_encoding_level(0.25, dims)
        np.testing.assert_allclose(level.scale, [500 / 1001, 500 / 1001])
        np.testing.assert_allclose(level.to_level0(level.to_level([[1000.0, 3.0]])), [[1000.0, 3.0]])

    def test_pyramid_levels_of_preprocessed_images(self):
        import os
        import tempfile
        from unittest.mock import patch

        from focus.preprocessing.microscopy_image import MicroscopyImage
        from focus.utils import load_ome_tiff_level, ome_tiff_pyramid_dims

        writer = MicroscopyImage.__new__(MicroscopyImage)
        with tempfile.TemporaryDirectory() as tmp, patch.object(MicroscopyImage, "_MAX_PYRAMID_PIXELS", 100 * 100):
            for channels in (3, 2, 1):
                image = np.random.default_rng(channels).random((200, 300, channels), dtype=np.float32)
                path = os.path.join(tmp, f"c{channels}.ome.tiff")
                writer._save_image_pyramid(image, path, np.dtype(np.uint8))
                self.assertEqual(ome_tiff_pyramid_dims(path), [(200, 300), (100, 150), (50, 75)])
                level0 = load_ome_tiff_level(path, 0).reshape(200, 300, channels)
                np.testing.assert_allclose(level0, np.round(image * 255) / 255, atol=1e-6)
                self.assertEqual(load_ome_tiff_level(path, 1).reshape(100, 150, -1).shape[2], channels)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.grid = TokenGrid(patch=16, base_side=224)
        self.dims = (1200, 1500)
        self.level = LevelGeometry(0, self.dims, self.dims, None)

    def check_plan(self, spatial, mapped, geom):
        plan = plan_windows(geom, mapped, self.level, self.grid)
        seen = np.concatenate([c.spots for c in plan.chunks] + [plan.unplaced])
        self.assertEqual(sorted(seen.tolist()), list(range(len(spatial))))
        h, w = self.dims
        for c in plan.chunks:
            ox, oy = c.origin
            self.assertTrue(0 <= ox and ox + c.shape[1] <= w and 0 <= oy and oy + c.shape[0] <= h)
            boxes = plan.boxes[c.spots]
            interior = (boxes[:, 0] > 0) & (boxes[:, 1] > 0) & (boxes[:, 2] < w) & (boxes[:, 3] < h)
            touching = (ox == 0) | (oy == 0) | (ox + c.shape[1] == w) | (oy + c.shape[0] == h)
            if not touching:
                margin = np.minimum.reduce([boxes[:, 0] - ox, boxes[:, 1] - oy,
                                            ox + c.shape[1] - boxes[:, 2], oy + c.shape[0] - boxes[:, 3]])
                self.assertTrue(np.all(margin[interior] >= self.grid.context - 1e-6))
        return plan

    def test_point_spots_share_windows(self):
        spatial, mapped, geom = lattice_geometry(40, 8.0, 8.0, 0.5, (20.0, 30.0))   # Visium HD 8 µm bins
        plan = self.check_plan(spatial, mapped, geom)
        self.assertLess(len(plan.chunks), len(spatial) / 10)

    def test_large_spots_get_large_windows(self):
        self.dims = (1600, 1600)
        self.level = LevelGeometry(0, self.dims, self.dims, None)
        spatial, mapped, geom = lattice_geometry(3, 200.0, 200.0, 0.5, (250.0, 250.0))   # 400 px footprints, all inside
        plan = self.check_plan(spatial, mapped, geom)
        self.assertTrue(all(c.shape[0] >= 400 + 2 * self.grid.context for c in plan.chunks))

    def test_tiny_spot_reads_the_token_holding_its_centre(self):
        spatial, mapped, geom = lattice_geometry(3, 40.0, 2.0, 0.5, (300.3, 200.7))   # 4 px footprints
        plan = plan_windows(geom, mapped, self.level, self.grid)
        chunk = plan.chunks[0]
        n_tokens = (chunk.shape[0] // 16) * (chunk.shape[1] // 16)
        member, keep = readout_membership(chunk, plan, geom, self.level, self.grid, np.zeros(n_tokens, bool))
        self.assertTrue(keep.all())
        nx = chunk.shape[1] // 16
        for r, spot in enumerate(chunk.spots):
            cx, cy = mapped[spot]
            expected = int((cy - chunk.origin[1]) // 16) * nx + int((cx - chunk.origin[0]) // 16)
            self.assertEqual(np.flatnonzero(member[r]).tolist(), [expected])

    def test_token_background_order_matches_the_patch_grid(self):
        window = np.ones((1, 48, 64, 3), np.float32)
        window[0, 16:32, 32:48] = 0.3                                   # token row 1, column 2
        bg = token_background_mask(window, 16, resolve_bg_color("white"))
        self.assertEqual(np.flatnonzero(~bg[0]).tolist(), [1 * 4 + 2])


@unittest.skipIf(torch is None, "torch/timm are not installed")
class ReadoutTests(unittest.TestCase):
    def setUp(self):
        self.extractor = MicroscopyImageFeatureExtractor(path=".", model=tiny_vit())

    def test_full_membership_reproduces_the_cls(self):
        rng = np.random.default_rng(0)
        for h, w in ((64, 64), (48, 64), (96, 96)):
            x = rng.random((2, h, w, 3), dtype=np.float32)
            standard = self.extractor._encode_np_batch(x)
            member = np.ones((2, 1, (h // 16) * (w // 16)), dtype=bool)
            np.testing.assert_allclose(self.extractor._encode_readouts(x, member)[:, 0], standard, rtol=1e-4, atol=1e-5)

    def test_readouts_are_independent(self):
        rng = np.random.default_rng(1)
        x = rng.random((1, 64, 64, 3), dtype=np.float32)
        member = rng.random((1, 6, 16)) < 0.4
        member[:, :, 0] = True
        together = self.extractor._encode_readouts(x, member)[0]
        for r in range(6):
            alone = self.extractor._encode_readouts(x, member[:, r:r + 1])[0, 0]
            np.testing.assert_allclose(together[r], alone, rtol=1e-4, atol=1e-5)
        perm = np.array([3, 0, 5, 1, 4, 2])
        np.testing.assert_allclose(self.extractor._encode_readouts(x, member[:, perm])[0], together[perm], rtol=1e-4, atol=1e-5)
        self.assertGreater(np.abs(together[0] - together[1]).max(), 1e-3)

    def test_spot_features_match_a_single_spot_reference(self):
        rng = np.random.default_rng(2)
        image = np.ones((400, 500, 3), np.float32)
        image[60:340, 80:300] = rng.random((280, 220, 3), dtype=np.float32) * 0.8
        spatial, mapped, geom = lattice_geometry(12, 20.0, 12.0, 0.5, (100.0, 90.0))    # 24 px footprints
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
                    Chunk(chunk.origin, chunk.shape, np.array([spot])), plan, geom, level, self.extractor.grid, tokens_bg)
                if not keep[0]:
                    self.assertTrue(np.array_equal(out[spot], np.zeros_like(out[spot])))
                    continue
                ref = self.extractor._encode_readouts(window, member[None])[0, 0]
                np.testing.assert_allclose(out[spot], ref, rtol=1e-4, atol=1e-5)

        x_px = mapped[:, 0]
        y_px = mapped[:, 1]
        outside = (x_px + 12 < 80) | (x_px - 12 > 300) | (y_px + 12 < 60) | (y_px - 12 > 340)
        self.assertTrue(outside.any())
        self.assertTrue(np.all(out[outside] == 0))
        self.assertTrue(np.all(np.isfinite(out)) and np.abs(out[~outside]).sum(axis=1).min() > 0)

    def test_grid_features_keep_the_standard_cls(self):
        rng = np.random.default_rng(3)
        image = np.ones((128, 192, 3), np.float32)
        image[:64, :64] = rng.random((64, 64, 3), dtype=np.float32)
        emb, centres = self.extractor.extract_grid_features(image, patch_size=64)
        self.assertEqual(emb.shape, (1, 32))
        np.testing.assert_allclose(centres, [[32.0, 32.0]])
        np.testing.assert_allclose(emb[0], self.extractor._encode_np_batch(image[None, :64, :64])[0], rtol=1e-5)


@unittest.skipIf(torch is None, "torch/timm are not installed")
class FeatureExtractionRegistrationTests(unittest.TestCase):
    def test_encodes_the_level_nearest_half_micron_and_caches(self):
        import os
        import tempfile
        from unittest.mock import patch

        import anndata as ad
        import tifffile

        from focus.provenance import cached_fingerprint
        from focus.registration._cache import REGISTRATION_FINGERPRINT_KEY, REGISTRATION_GEOMETRY_KEY
        from focus.registration.registration import FeatureExtractorRegistration

        class TinyExtractor(MicroscopyImageFeatureExtractor):
            def __init__(self, path, hf_token=None):
                super().__init__(path, model=tiny_vit())

        rng = np.random.default_rng(4)
        with tempfile.TemporaryDirectory() as tmp:
            base = np.full((640, 800, 3), 255, np.uint8)
            base[100:540, 100:700] = rng.integers(0, 200, (440, 600, 3), dtype=np.uint8)
            image = os.path.join(tmp, "image.ome.tiff")
            with tifffile.TiffWriter(image, ome=True) as tif:
                for k, level in enumerate((base, base[::2, ::2])):
                    tif.write(level, photometric="rgb", metadata={
                        "axes": "YXS", "PhysicalSizeX": 0.25 * 2 ** k, "PhysicalSizeY": 0.25 * 2 ** k})
            i, j = np.meshgrid(np.arange(8), np.arange(8))
            spatial = np.column_stack([i.ravel(), j.ravel()]).astype(np.float32) * 20.0 + 10.0   # MSI, µm
            anchor = ad.AnnData(X=np.zeros((64, 1), np.float32))
            anchor.obsm["spatial"] = spatial
            anchor.obsm["he_spatial"] = spatial / 0.25                                          # level-0 px
            anchor.uns["spot_size"] = np.array([20.0, 20.0], np.float32)
            anchor_path = os.path.join(tmp, "anchor.h5ad")
            anchor.write_h5ad(anchor_path)

            engine = FeatureExtractorRegistration(tmp)
            with patch("focus.registration.microscopy_image.MicroscopyImageFeatureExtractor", TinyExtractor):
                files = engine.register_dataset({"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi")
                out = ad.read_h5ad(files["s1"])
                self.assertEqual(out.X.shape, (64, 32))
                geometry = out.uns[REGISTRATION_GEOMETRY_KEY]
                self.assertEqual((int(geometry["encoded_level"]), geometry["lattice"]), (1, "square"))
                self.assertAlmostEqual(float(geometry["encoded_um_per_px"]), 0.5)
                np.testing.assert_allclose(out.obsm["spatial"], spatial / 0.25)
                mtime = os.stat(files["s1"]).st_mtime_ns
                engine.register_dataset({"s1": image}, {"s1": anchor_path}, "he", "msi", anchor_type="msi")
                self.assertEqual(os.stat(files["s1"]).st_mtime_ns, mtime)
                self.assertIsNotNone(cached_fingerprint(files["s1"], key=REGISTRATION_FINGERPRINT_KEY))


if __name__ == "__main__":
    unittest.main()
