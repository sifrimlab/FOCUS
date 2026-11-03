"""PixelSizeParser behavioral scenarios."""

import nd2
from typing import cast

from typing import Any
from types import SimpleNamespace
import tempfile
from pathlib import Path

import numpy as np
import tifffile

from focus.preprocessing.microscopy.metadata import _czi_pixel_size, _nd2_pixel_size
from focus.preprocessing.microscopy.source import open_source


class TestPixelSizeParser:
    """Behavioral contracts for pixel size parser."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        self.tmp.cleanup()

    def _size(self, name: str, **kwargs: Any) -> tuple[np.ndarray | None, str]:
        """Write a controlled TIFF with the supplied metadata and return its parsed physical pixel size."""
        path = self.root / name
        tifffile.imwrite(path, np.zeros((32, 32), np.uint8), **kwargs)
        with open_source(str(path)) as source:
            return source.pixel_size, source.pixel_size_source

    def test_aperio_description(self) -> None:
        """Verify aperio description."""
        description = "Aperio Image Library v11.2.1\r\n32x32 (256x256) JPEG/RGB Q=30|AppMag = 40|MPP = 0.2527"
        size, source = self._size("slide.tif", description=description)
        np.testing.assert_allclose(cast(np.ndarray, size), [0.2527, 0.2527])
        assert source == "aperio"

    def test_imagej_unit(self) -> None:
        """Verify imagej unit."""
        size, source = self._size(
            "ij.tif",
            imagej=True,
            resolution=(1 / 0.65, 1 / 0.65),
            metadata={"unit": "um"},
        )
        np.testing.assert_allclose(cast(np.ndarray, size), [0.65, 0.65])
        assert source == "imagej"

    def test_dpi_default_is_rejected(self) -> None:
        """Verify dpi default is rejected."""
        for dpi in (72, 300, 600):
            size, source = self._size(
                f"dpi{dpi}.tif", resolution=(dpi, dpi), resolutionunit="INCH"
            )
            assert size is None and source == "none"

    def test_czi_scaling(self) -> None:
        """Verify czi scaling."""
        xml = (
            "<ImageDocument><Metadata><Scaling><Items>"
            '<Distance Id="X"><Value>2.2e-07</Value></Distance>'
            '<Distance Id="Y"><Value>2.4e-07</Value></Distance>'
            "</Items></Scaling></Metadata></ImageDocument>"
        )
        np.testing.assert_allclose(cast(np.ndarray, _czi_pixel_size(xml)), [0.22, 0.24])

    def test_nd2_voxel_size_needs_calibration(self) -> None:
        """Verify nd2 voxel size needs calibration."""

        def nd2_file(calibrated: tuple[bool, bool, bool]) -> SimpleNamespace:
            """Construct the controlled ND2 metadata handle used by the source-pixel-size test."""
            volume = SimpleNamespace(axesCalibrated=calibrated)
            return SimpleNamespace(
                metadata=SimpleNamespace(channels=[SimpleNamespace(volume=volume)]),
                voxel_size=lambda: SimpleNamespace(x=0.325, y=0.325, z=1.0),
            )

        np.testing.assert_allclose(
            cast(
                np.ndarray,
                _nd2_pixel_size(cast(nd2.ND2File, nd2_file((True, True, False)))),
            ),
            [0.325, 0.325],
        )
        assert (
            _nd2_pixel_size(cast(nd2.ND2File, nd2_file((False, False, False)))) is None
        )
