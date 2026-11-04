"""PreprocessingProvenance behavioral scenarios."""

from typing import cast

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import tifffile

from focus.io.ome_write import level0_planes as _level0_planes
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.io.provenance import (
    read_ome_annotations,
    planes_digest,
    cached_fingerprint,
    content_signature,
    INPUT_SIGNATURE_KEY,
    PIXEL_DIGEST_KEY,
    SUPERSEDED_SIGNATURE_KEY,
)
from focus.io.ome import read_ome_physical_size_um, ome_tiff_pyramid_dims
from tests.provenance.data import (
    CROP,
    FILLED,
    NO_CROP,
    _OFFSET,
    _write_source,
    crop_offset,
)


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
        out = self.sample.process_image(CROP)
        annotations = cast(dict[str, str], read_ome_annotations(out))
        np.testing.assert_allclose(
            cast(np.ndarray, read_ome_physical_size_um(out)), [0.5, 0.5]
        )
        np.testing.assert_allclose(crop_offset(out), _OFFSET, atol=4)
        assert cached_fingerprint(out) is not None
        assert planes_digest(_level0_planes(out)) == annotations[PIXEL_DIGEST_KEY]
        assert content_signature(out) == f"pixels:{annotations[PIXEL_DIGEST_KEY]}"
        assert SUPERSEDED_SIGNATURE_KEY not in annotations

    def test_coarser_levels_scale_the_resolution_tags(self) -> None:
        """Every SubIFD level carries resolution tags scaled by its measured size ratio."""
        _write_source(self.root / "big" / "he" / "img.tif", shape=(3200, 3000))
        out = MicroscopyImage(str(self.root), "big", "he").process_image(NO_CROP)
        dims = ome_tiff_pyramid_dims(out)
        assert dims[0] == (3200, 3000) and len(dims) == 4
        with tifffile.TiffFile(out) as tif:
            levels = tif.series[0].levels
            res = [lvl.keyframe.get_resolution() for lvl in levels]
        for (h, w), (rx, ry) in zip(dims, res):
            np.testing.assert_allclose(
                [rx, ry], [2e4 * w / 3000, 2e4 * h / 3200], rtol=1e-6
            )
        np.testing.assert_array_equal(crop_offset(out), [0, 0])

    def test_rerun_is_cached_and_a_setting_change_regenerates(self) -> None:
        """Verify rerun is cached and a setting change regenerates."""
        out = self.sample.process_image(CROP)
        key, mtime = cached_fingerprint(out), os.stat(out).st_mtime_ns
        self.sample.process_image(CROP)
        assert os.stat(out).st_mtime_ns == mtime
        self.sample.process_image(FILLED)
        assert cached_fingerprint(out) != key

    def test_unchanged_input_is_not_rehashed(self) -> None:
        """The input SHA-256 stored in the output is reused while the input stat is unchanged."""
        out = self.sample.process_image(CROP)
        assert INPUT_SIGNATURE_KEY in cast(dict[str, str], read_ome_annotations(out))
        with patch("focus.io.provenance.file_digest") as digest:
            self.sample.process_image(CROP)
            digest.assert_not_called()
        os.utime(self.sample.filename, ns=(1, 1))
        with patch("focus.io.provenance.file_digest", return_value="x" * 64) as digest:
            self.sample.process_image(CROP)
            digest.assert_called_once()
