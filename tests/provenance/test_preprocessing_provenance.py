"""PreprocessingProvenance behavioral scenarios."""

from typing import cast

from focus.preprocessing.microscopy.pyramid import ImagePyramid
import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import tifffile

from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.io.provenance import (
    read_ome_annotations,
    planes_digest,
    cached_fingerprint,
    content_signature,
    PIXEL_DIGEST_KEY,
    CROP_OFFSET_KEY,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.io.ome import read_ome_physical_size_um, ome_tiff_pyramid_dims
from tests.provenance.data import _OFFSET, _write_source


class TestPreprocessingProvenance:
    """Behavioral contracts for preprocessing provenance."""

    def setup_method(self) -> None:
        """Create the isolated fixture state."""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_source(self.root / "s" / "he" / "img.tif")
        self.sample = MicroscopyImage(str(self.root), "s", "he")

    def teardown_method(self) -> None:
        """Release the isolated fixture resources."""
        self.tmp.cleanup()

    def test_output_carries_pixel_size_offset_fingerprint_and_digest(self) -> None:
        """Verify output carries pixel size offset fingerprint and digest."""
        out = self.sample.process_image(crop_margin=100)
        annotations = cast(dict[str, str], read_ome_annotations(out))
        np.testing.assert_allclose(
            cast(np.ndarray, read_ome_physical_size_um(out)), [0.5, 0.5]
        )
        np.testing.assert_array_equal(
            [float(v) for v in annotations[CROP_OFFSET_KEY].split(",")], _OFFSET
        )
        assert cached_fingerprint(out) is not None
        assert (
            planes_digest(ImagePyramid._read_level0_planes(out))
            == annotations[PIXEL_DIGEST_KEY]
        )
        assert content_signature(out) == f"pixels:{annotations[PIXEL_DIGEST_KEY]}"
        assert SUPERSEDED_SIGNATURE_KEY not in annotations

    def test_coarser_levels_scale_the_pixel_size(self) -> None:
        """Verify coarser levels scale the pixel size."""
        _write_source(self.root / "big" / "he" / "img.tif", shape=(3200, 3000))
        out = MicroscopyImage(str(self.root), "big", "he").process_image(
            crop_to_tissue=False
        )
        dims = ome_tiff_pyramid_dims(out)
        assert len(dims) == 2
        with tifffile.TiffFile(out) as tif:
            root = ET.fromstring(cast(tifffile.TiffPage, tif.pages[0]).description)
        sizes = [
            float(cast(str, e.get("PhysicalSizeX")))
            for e in root.iter()
            if e.tag.endswith("Pixels")
        ]
        np.testing.assert_allclose(sizes, [0.5, 0.5 * dims[0][1] / dims[1][1]])
        assert cast(dict[str, str], read_ome_annotations(out))[CROP_OFFSET_KEY] == "0,0"

    def test_rerun_is_cached_and_a_setting_change_regenerates(self) -> None:
        """Verify rerun is cached and a setting change regenerates."""
        out = self.sample.process_image(crop_margin=100)
        key, mtime = cached_fingerprint(out), os.stat(out).st_mtime_ns
        self.sample.process_image(crop_margin=100)
        assert os.stat(out).st_mtime_ns == mtime
        self.sample.process_image(crop_margin=60)
        assert cached_fingerprint(out) != key
        assert (
            cast(dict[str, str], read_ome_annotations(out))[CROP_OFFSET_KEY]
            == "740,1140"
        )
