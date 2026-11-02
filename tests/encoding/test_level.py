"""Level behavioral scenarios."""

import numpy as np

from focus.registration.readout import select_encoding_level


class TestLevel:
    """Behavioral contracts for level."""

    def test_nearest_level_without_resampling(self) -> None:
        """Verify nearest level without resampling."""
        dims = [(1001, 1001), (500, 500), (250, 250)]
        assert select_encoding_level(0.25, dims).level == 1
        assert select_encoding_level(0.5, dims).level == 0
        assert select_encoding_level(1.0, dims).level == 0
        assert select_encoding_level(0.13, dims).level == 2
        none = select_encoding_level(None, dims)
        assert (none.level, none.um_per_px) == (0, None)
        level = select_encoding_level(0.25, dims)
        np.testing.assert_allclose(level.scale, [500 / 1001, 500 / 1001])
        np.testing.assert_allclose(
            level.to_level0(level.to_level([[1000.0, 3.0]])), [[1000.0, 3.0]]
        )

    def test_pyramid_levels_of_preprocessed_images(self) -> None:
        """Verify pyramid levels of preprocessed images."""
        import os
        import tempfile
        from unittest.mock import patch

        from focus.preprocessing.microscopy.sample import MicroscopyImage
        from focus.io.ome import load_ome_tiff_level, ome_tiff_pyramid_dims

        writer = MicroscopyImage.__new__(MicroscopyImage)
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "focus.preprocessing.microscopy.settings._MAX_PYRAMID_PIXELS", 100 * 100
            ),
        ):
            for channels in (3, 2, 1):
                image = np.random.default_rng(channels).random(
                    (200, 300, channels), dtype=np.float32
                )
                path = os.path.join(tmp, f"c{channels}.ome.tiff")
                writer.pyramid._save_image_pyramid(image, path, np.dtype(np.uint8))
                assert ome_tiff_pyramid_dims(path) == [(200, 300), (100, 150), (50, 75)]
                level0 = load_ome_tiff_level(path, 0).reshape(200, 300, channels)
                np.testing.assert_allclose(
                    level0, np.round(image * 255) / 255, atol=1e-6
                )
                assert (
                    load_ome_tiff_level(path, 1).reshape(100, 150, -1).shape[2]
                    == channels
                )
