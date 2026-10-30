"""Microscopy preprocessing provenance and the alignment cache built on it.

Run with: PYTHONPATH=src python -m unittest tests.test_microscopy_provenance -v
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import anndata as ad
import numpy as np
import tifffile

from focus.alignment.alignment import DirectMappingAligner
from focus.preprocessing.microscopy_image import (
    MicroscopyImage, _czi_pixel_size, _nd2_pixel_size,
)
from focus.provenance import (
    read_ome_annotations, planes_digest, cached_fingerprint, content_signature,
    PIXEL_DIGEST_KEY, CROP_OFFSET_KEY, SUPERSEDED_SIGNATURE_KEY,
)
from focus.reporting import get_reporter
from focus.utils import read_ome_physical_size_um, ome_tiff_pyramid_dims

# Blob rows 1200:2000, cols 800:1500; with a 100 px margin the crop starts at (700, 1100).
_BLOB = (slice(1200, 2000), slice(800, 1500))
_OFFSET = np.array([700, 1100], dtype=np.float32)


def _write_source(path: Path, shape=(2500, 2000)):
    """RGB uint8 brightfield-like TIFF at 0.5 µm/px (resolution tags in centimetres)."""
    image = np.full((*shape, 3), 245, np.uint8)
    image[_BLOB] = [150, 60, 120]
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, image, photometric="rgb", resolution=(20000, 20000), resolutionunit="CENTIMETER")


def _strip_provenance(path: str):
    """Rewrite ``path`` as the pre-provenance writer did: same pixels, no MapAnnotation, no PhysicalSize."""
    with tifffile.TiffFile(path) as tif:
        description = tif.pages[0].description
        pages = [(p.asarray(), p.photometric) for p in tif.pages]
    root = ET.fromstring(description)
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.endswith("StructuredAnnotations"):
                parent.remove(child)
            elif child.tag.endswith("Pixels"):
                for attr in [a for a in child.attrib if a.startswith("PhysicalSize")]:
                    del child.attrib[attr]
    xml = ET.tostring(root, encoding="unicode").encode("ascii", "xmlcharrefreplace").decode("ascii")
    with tifffile.TiffWriter(path, bigtiff=True) as tif:
        for i, (data, photometric) in enumerate(pages):
            rgb = data.ndim == 3
            tif.write(data, description=xml if i == 0 else None, photometric="rgb" if rgb else "minisblack",
                      metadata={"axes": "YXC" if rgb else "YX"}, compression="zlib", predictor=2)


class PreprocessingProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_source(self.root / "s" / "he" / "img.tif")
        self.sample = MicroscopyImage(str(self.root), "s", "he")

    def tearDown(self):
        self.tmp.cleanup()

    def test_output_carries_pixel_size_offset_fingerprint_and_digest(self):
        out = self.sample.process_image(crop_margin=100)
        annotations = read_ome_annotations(out)
        np.testing.assert_allclose(read_ome_physical_size_um(out), [0.5, 0.5])
        np.testing.assert_array_equal([float(v) for v in annotations[CROP_OFFSET_KEY].split(",")], _OFFSET)
        self.assertIsNotNone(cached_fingerprint(out))
        self.assertEqual(planes_digest(MicroscopyImage._read_level0_planes(out)), annotations[PIXEL_DIGEST_KEY])
        self.assertEqual(content_signature(out), f"pixels:{annotations[PIXEL_DIGEST_KEY]}")
        self.assertNotIn(SUPERSEDED_SIGNATURE_KEY, annotations)

    def test_coarser_levels_scale_the_pixel_size(self):
        _write_source(self.root / "big" / "he" / "img.tif", shape=(3200, 3000))
        out = MicroscopyImage(str(self.root), "big", "he").process_image(crop_to_tissue=False)
        dims = ome_tiff_pyramid_dims(out)
        self.assertEqual(len(dims), 2)
        with tifffile.TiffFile(out) as tif:
            root = ET.fromstring(tif.pages[0].description)
        sizes = [float(e.get("PhysicalSizeX")) for e in root.iter() if e.tag.endswith("Pixels")]
        np.testing.assert_allclose(sizes, [0.5, 0.5 * dims[0][1] / dims[1][1]])
        self.assertEqual(read_ome_annotations(out)[CROP_OFFSET_KEY], "0,0")

    def test_rerun_is_cached_and_a_setting_change_regenerates(self):
        out = self.sample.process_image(crop_margin=100)
        key, mtime = cached_fingerprint(out), os.stat(out).st_mtime_ns
        self.sample.process_image(crop_margin=100)
        self.assertEqual(os.stat(out).st_mtime_ns, mtime)
        self.sample.process_image(crop_margin=60)
        self.assertNotEqual(cached_fingerprint(out), key)
        self.assertEqual(read_ome_annotations(out)[CROP_OFFSET_KEY], "740,1140")


class AlignmentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_source(self.root / "s" / "he" / "img.tif")
        self.sample = MicroscopyImage(str(self.root), "s", "he")
        self.image = self.sample.process_image(crop_margin=100)

        rng = np.random.default_rng(0)
        self.spatial = rng.uniform([800, 1200], [1500, 2000], size=(40, 2)).astype(np.float32)
        anchor = ad.AnnData(np.zeros((40, 1), np.float32))
        anchor.obsm["spatial"] = self.spatial
        anchor.uns["focus_preprocessing_fingerprint"] = "anchor-fingerprint"
        self.anchor = str(self.root / "anchor.h5ad")
        anchor.write_h5ad(self.anchor)

        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._path = str(self.root)
        aligner._target_modality = {"s": self.anchor}
        aligner._reference_modality = {"s": self.image}
        aligner._target_modality_type = "st"
        aligner._reference_modality_type = "microscopy_image"
        aligner._target_modality_name = "st"
        aligner._reference_modality_name = "he"
        aligner._common_samples = ["s"]
        aligner._reporter = get_reporter()
        self.aligner = aligner

    def tearDown(self):
        self.tmp.cleanup()

    def _stamp_manual_alignment(self):
        """An aligned anchor file whose key was computed against the image as it is now."""
        adata = ad.read_h5ad(self.anchor)
        adata.obsm["he_spatial"] = self.spatial + 5
        adata.uns["focus_alignment_inputs"] = {"he": self.aligner._input_key("s")}
        path = self.aligner._aligned_output_path("s")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        adata.write_h5ad(path)
        return path

    def test_pixel_identical_regeneration_keeps_a_legacy_alignment(self):
        _strip_provenance(self.image)
        self.assertIsNone(cached_fingerprint(self.image))
        path = self._stamp_manual_alignment()                 # stat-based legacy key
        self.sample.process_image(crop_margin=100)            # no fingerprint: regenerated
        self.assertIn(SUPERSEDED_SIGNATURE_KEY, read_ome_annotations(self.image))
        self.assertTrue(self.aligner._cache_matches("s"))
        stored = ad.read_h5ad(path).uns["focus_alignment_inputs"]["he"]
        self.assertEqual(stored, self.aligner._input_key("s"))
        self.assertTrue(self.aligner._cache_matches("s"))     # idempotent

    def test_changed_pixels_invalidate_a_legacy_alignment(self):
        _strip_provenance(self.image)
        self._stamp_manual_alignment()
        self.sample.process_image(crop_margin=60)
        self.assertNotIn(SUPERSEDED_SIGNATURE_KEY, read_ome_annotations(self.image))
        self.assertFalse(self.aligner._cache_matches("s"))

    def test_current_alignment_matches_and_survives_a_pixel_identical_rerun(self):
        self._stamp_manual_alignment()
        self.assertTrue(self.aligner._cache_matches("s"))
        self.sample.process_image(crop_margin=100, force_recomputing=True)
        self.assertTrue(self.aligner._cache_matches("s"))

    def test_pre_aligned_coordinates_subtract_the_crop_offset(self):
        aligned = self.aligner.uniform_aligned_dataset()
        result = ad.read_h5ad(aligned["s"])
        np.testing.assert_allclose(result.obsm["he_spatial"], self.spatial - _OFFSET)
        self.assertTrue(self.aligner._cache_matches("s", pre_aligned=True))

        self.sample.process_image(crop_margin=60)
        self.assertFalse(self.aligner._cache_matches("s", pre_aligned=True))
        aligned = self.aligner.uniform_aligned_dataset()
        np.testing.assert_allclose(ad.read_h5ad(aligned["s"]).obsm["he_spatial"], self.spatial - (_OFFSET + 40))


class PixelSizeParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reader = MicroscopyImage.__new__(MicroscopyImage)

    def tearDown(self):
        self.tmp.cleanup()

    def _size(self, name, **kwargs):
        path = self.root / name
        tifffile.imwrite(path, np.zeros((32, 32), np.uint8), **kwargs)
        return self.reader._source_pixel_size_um(str(path))

    def test_aperio_description(self):
        description = "Aperio Image Library v11.2.1\r\n32x32 (256x256) JPEG/RGB Q=30|AppMag = 40|MPP = 0.2527"
        size, source = self._size("slide.tif", description=description)
        np.testing.assert_allclose(size, [0.2527, 0.2527])
        self.assertEqual(source, "aperio")

    def test_imagej_unit(self):
        size, source = self._size("ij.tif", imagej=True, resolution=(1 / 0.65, 1 / 0.65), metadata={"unit": "um"})
        np.testing.assert_allclose(size, [0.65, 0.65])
        self.assertEqual(source, "imagej")

    def test_dpi_default_is_rejected(self):
        self.assertEqual(self._size("dpi.tif", resolution=(72, 72), resolutionunit="INCH"), (None, "none"))

    def test_czi_scaling(self):
        xml = ('<ImageDocument><Metadata><Scaling><Items>'
               '<Distance Id="X"><Value>2.2e-07</Value></Distance>'
               '<Distance Id="Y"><Value>2.4e-07</Value></Distance>'
               '</Items></Scaling></Metadata></ImageDocument>')
        np.testing.assert_allclose(_czi_pixel_size(xml), [0.22, 0.24])

    def test_nd2_voxel_size_needs_calibration(self):
        def nd2_file(calibrated):
            volume = SimpleNamespace(axesCalibrated=calibrated)
            return SimpleNamespace(metadata=SimpleNamespace(channels=[SimpleNamespace(volume=volume)]),
                                   voxel_size=lambda: SimpleNamespace(x=0.325, y=0.325, z=1.0))
        np.testing.assert_allclose(_nd2_pixel_size(nd2_file((True, True, False))), [0.325, 0.325])
        self.assertIsNone(_nd2_pixel_size(nd2_file((False, False, False))))


if __name__ == "__main__":
    unittest.main()
