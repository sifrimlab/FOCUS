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
        """SubIFD pyramids of 1, 2 and 3 channels read back as HWC levels with native values."""
        import os
        import tempfile
        from unittest.mock import patch

        import dask.array as da

        from focus.io.ome import load_ome_tiff_level, ome_tiff_pyramid_dims
        from focus.io import ome_write as pyramid

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("focus.io.ome_write.TILE", 64),
        ):
            for channels in (3, 2, 1):
                image = np.random.default_rng(channels).integers(
                    0, 256, (200, 300, channels), dtype=np.uint8
                )
                rgb = channels == 3
                path = os.path.join(tmp, f"c{channels}.ome.tiff")
                levels = pyramid.stage_levels(
                    da.from_array(image),  # type: ignore[no-untyped-call]
                    rgb,
                    os.path.join(tmp, f"stage{channels}"),
                )
                pyramid.write_pyramid(
                    levels, path, rgb, "<OME/>", ("zstd", {}, 2), None
                )
                assert ome_tiff_pyramid_dims(path) == [
                    (200, 300),
                    (100, 150),
                    (50, 75),
                    (25, 37),
                ]
                level0 = load_ome_tiff_level(path, 0).reshape(200, 300, channels)
                np.testing.assert_allclose(level0, image / 255, atol=1e-6)
                assert (
                    load_ome_tiff_level(path, 1).reshape(100, 150, -1).shape[2]
                    == channels
                )

    def test_legacy_separate_series_pyramids_still_read(self) -> None:
        """Outputs written before microscopy-3 (one series per channel per level) keep reading."""
        import os
        import tempfile

        import tifffile

        from focus.io.ome import load_ome_tiff_level, ome_tiff_pyramid_dims

        image = np.random.default_rng(0).integers(0, 256, (2, 200, 300), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.ome.tiff")
            with tifffile.TiffWriter(path) as tif:
                for level in (image, image[:, ::2, ::2]):
                    for plane in level:
                        tif.write(
                            plane, photometric="minisblack", metadata={"axes": "YX"}
                        )
            assert ome_tiff_pyramid_dims(path) == [(200, 300), (100, 150)]
            np.testing.assert_allclose(
                load_ome_tiff_level(path, 0), np.moveaxis(image, 0, -1) / 255, atol=1e-6
            )
