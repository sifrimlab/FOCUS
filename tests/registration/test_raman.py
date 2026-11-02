"""Raman behavioral scenarios."""

import os
import tempfile

import anndata as ad
import numpy as np
import tifffile

from focus.registration.provenance import REGISTRATION_GEOMETRY_KEY
from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import pixel_footprint_membership
from focus.registration.raman_pixel import RamanPixelInterpolationRegistration
from tests.registration.data import hex_lattice, write_anchor


class TestRaman:
    """Behavioral contracts for raman."""

    def test_footprint_honours_the_raman_pixel_size(self) -> None:
        """Verify footprint honours the raman pixel size."""
        with tempfile.TemporaryDirectory() as tmp:
            h = w = 260
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            image = np.stack([np.ones((h, w), np.float32), xx + 0.5, yy + 0.5])
            raman = os.path.join(tmp, "raman.ome.tiff")
            tifffile.imwrite(
                raman,
                image,
                ome=True,
                metadata={"axes": "CYX", "PhysicalSizeX": 2.0, "PhysicalSizeY": 2.0},
            )
            spatial = hex_lattice(n=4) - 400.0  # µm, 55 µm Visium-like discs
            mapped = spatial / 2.0 + [10.0, 20.0]  # 2 µm per Raman pixel
            anchor = os.path.join(tmp, "anchor.h5ad")
            write_anchor(anchor, spatial, mapped, "raman", [55.0, 55.0])

            files = RamanPixelInterpolationRegistration(tmp).register_dataset(
                {"s1": anchor}, {"s1": raman}, "st", "raman"
            )
            out = ad.read_h5ad(files["s1"])
            np.testing.assert_allclose(out.X[:, 0], 1.0, rtol=1e-6)
            np.testing.assert_allclose(out.X[:, 1:], mapped, atol=0.05)
            assert out.uns[REGISTRATION_GEOMETRY_KEY]["footprint"] == "ellipse"

            geom = AnchorGeometry.from_arrays(spatial, mapped, np.array([55.0, 55.0]))
            counts = np.diff(
                pixel_footprint_membership(geom, (0, 0, w, h), "ones").indptr
            )
            assert np.all(np.abs(counts - np.pi * 13.75**2) < 2 * np.pi * 13.75)
