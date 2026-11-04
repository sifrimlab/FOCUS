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


def _write_raman(
    path: str, images: list[tuple[np.ndarray, dict[str, object], list[str]]]
) -> None:
    """Multi-image Raman OME-TIFF as raman-2 writes it (``(C, Y, X)`` float32 per image)."""
    import zarr

    from focus.io import ome_write

    staged, described = [], []
    workdir = path + ".stage"
    for index, (data, values, names) in enumerate(images):
        level0 = zarr.create_array(
            store=os.path.join(workdir, f"{index}.zarr"),
            shape=data.shape,
            chunks=(1, 64, 64),
            dtype=np.float32,
            fill_value=np.nan,
        )
        level0[:] = data
        levels = ome_write.levels_from(level0, False, workdir, f"i{index}_")
        staged.append(ome_write.StagedImage(levels, False, np.array([1.0, 1.0])))
        described.append(
            ome_write.OmeImage(
                name=f"acq{index}",
                shape_yx=data.shape[1:],
                dtype=np.dtype(np.float32),
                rgb=False,
                channels=[ome_write.ChannelInfo(n) for n in names],
                colours=["FFFFFF"] * len(names),
                pixel_size=np.array([1.0, 1.0]),
                significant_bits=None,
                objective=(None, None),
                contours=[],
                annotations=values,
            )
        )
    xml = ome_write.build_ome_xml(described, {"focus_method": "raman-2"})
    ome_write.write_images(path, staged, xml, ("zstd", {"level": 3}, 3))
    ome_write.remove_workdir(workdir)


class TestRamanImages:
    """Several acquisitions in one file: each is read through its placement."""

    def test_spots_outside_an_acquisition_get_nan(self) -> None:
        """Image 1 covers part of image 0; its channels are NaN where it measured nothing."""
        from focus.registration.raman_pixel import raman_targets

        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp, patch("focus.io.ome_write.TILE", 64):
            h = w = 300
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            base = np.stack([xx + 0.5, yy + 0.5])
            roi = np.stack([np.full((100, 120), 7.0, np.float32)] * 2)
            roi[:, :, :10] = np.nan  # a gap the ROI did not measure
            raman = os.path.join(tmp, "raman.ome.tiff")
            _write_raman(
                raman,
                [
                    (
                        base,
                        {
                            "focus_acquisition": "whole",
                            "focus_placement": "0,0,1,1",
                            "focus_wavenumbers_cm1": "[2850.0, 2930.0]",
                        },
                        ["2850.0 cm-1", "2930.0 cm-1"],
                    ),
                    (
                        roi,
                        {
                            "focus_acquisition": "roi",
                            "focus_placement": "150.00,100.00,1.0,1.0",
                            "focus_wavenumbers_cm1": "[2850.0, 1650.0]",
                        },
                        ["2850.0 cm-1", "1650.0 cm-1"],
                    ),
                ],
            )
            with tifffile.TiffFile(raman) as tif:
                assert len(tif.series[0].levels) > 1  # read through a real pyramid
            targets = raman_targets(raman)
            assert [t.placement for t in targets] == [(0, 0, 1, 1), (150, 100, 1, 1)]

            spatial = hex_lattice(n=8, pitch=40.0) - 480.0  # 20 to 300 px
            anchor = os.path.join(tmp, "anchor.h5ad")
            write_anchor(anchor, spatial, spatial, "raman", [10.0, 10.0])
            files = RamanPixelInterpolationRegistration(tmp).register_dataset(
                {"s1": anchor}, {"s1": raman}, "st", "raman"
            )
            out = ad.read_h5ad(files["s1"])
            assert list(out.var_names) == [
                "2850.0 cm-1 (whole)",
                "2930.0 cm-1",
                "2850.0 cm-1 (roi)",
                "1650.0 cm-1",
            ]
            assert list(out.var["acquisition"]) == ["whole", "whole", "roi", "roi"]
            within = (spatial < 300 - 6).all(axis=1)
            np.testing.assert_allclose(out.X[within, 0], spatial[within, 0], atol=0.05)
            assert np.isnan(out.X[(spatial[:, 0] > 300 + 6), :2]).all()
            inside = (
                (spatial[:, 0] > 150 + 10 + 6)
                & (spatial[:, 0] < 270 - 6)
                & (spatial[:, 1] > 100 + 6)
                & (spatial[:, 1] < 200 - 6)
            )
            outside = (
                (spatial[:, 0] < 150 - 6)
                | (spatial[:, 0] > 270 + 6)
                | (spatial[:, 1] < 100 - 6)
                | (spatial[:, 1] > 200 + 6)
            )
            assert inside.any() and outside.any()
            np.testing.assert_allclose(out.X[inside, 2:], 7.0, rtol=1e-5)
            assert np.isnan(out.X[outside, 2:]).all()
            assert np.isfinite(out.X[within, :2]).all()
