"""Microscopy preprocessing (microscopy-3): input contract, presets, lossless output and metadata."""

from pathlib import Path
from typing import cast

import numpy as np
import pytest
import tifffile

from focus.io.ome import load_ome_tiff_level, read_channel_names, read_display_settings
from focus.io.provenance import planes_digest, read_ome_annotations
from focus.preprocessing.microscopy.pyramid import _level0_planes
from focus.preprocessing.microscopy.sample import MicroscopyImage
from focus.preprocessing.microscopy.source import ChannelInfo, hint_from_channels

from tests.microscopy.data import (
    NAMES,
    brightfield,
    fluorescence,
    ome_root,
    settings,
    tags,
    write,
)


def _hint(channels: list[ChannelInfo]) -> tuple[str, str]:
    """The stated type, or empty strings when the metadata states none."""
    return hint_from_channels(channels) or ("", "")


class TestInput:
    """One image per sample, every channel, named axes and Z reduction."""

    def test_one_image_per_sample_folder(self, tmp_path: Path) -> None:
        """No image raises FileNotFoundError; two images raise ValueError (ST parity)."""
        folder = tmp_path / "s" / "img"
        folder.mkdir(parents=True)
        with pytest.raises(FileNotFoundError):
            MicroscopyImage(str(tmp_path), "s", "img")
        for name in ("a.ome.tif", "b.czi"):
            (folder / name).write_bytes(b"")
        with pytest.raises(ValueError, match="Several image files"):
            MicroscopyImage(str(tmp_path), "s", "img")

    def test_every_channel_name_and_value_is_kept(self, tmp_path: Path) -> None:
        """Six uint16 channels are written unchanged with their names and the zstd codec."""
        image = fluorescence(6)
        names = NAMES + ["Ki67", "CD20"]
        sample = write(
            tmp_path,
            "if.ome.tif",
            image,
            photometric="minisblack",
            metadata={
                "axes": "CYX",
                "PhysicalSizeX": 0.65,
                "PhysicalSizeY": 0.65,
                "Channel": {"Name": names},
            },
        )
        out = sample.process_image(settings())
        with tifffile.TiffFile(out) as tif:
            series = tif.series[0]
            assert series.axes == "CYX" and series.dtype == np.uint16
            np.testing.assert_array_equal(series.asarray(), image)
            assert tif.pages[0].compression == tifffile.COMPRESSION.ZSTD
        assert read_channel_names(out) == names
        annotations = cast(dict[str, str], read_ome_annotations(out))
        assert annotations["focus_image_type"] == "fluorescence"
        assert planes_digest(_level0_planes(out)) == annotations["focus_pixel_digest"]

    @pytest.mark.parametrize(
        ("rule", "plane"),
        [("auto", None), ("max", None), ("middle", 2), ("best_focus", 3)],
    )
    def test_z_projection(self, tmp_path: Path, rule: str, plane: int | None) -> None:
        """Each Z rule selects the expected plane; auto is the maximum projection for fluorescence."""
        stack = np.stack([fluorescence(2, (400, 500)) // 4 for _ in range(5)])
        stack[3] = fluorescence(2, (400, 500))  # the brightest and sharpest plane
        stack[3, :, ::2, ::2] = (
            0  # high-frequency detail: highest variance of the Laplacian
        )
        sample = write(
            tmp_path,
            "z.ome.tif",
            stack,
            photometric="minisblack",
            metadata={"axes": "ZCYX"},
        )
        out = tifffile.imread(sample.process_image(settings(z_projection=rule)))
        expected = stack.max(axis=0) if plane is None else stack[plane]
        np.testing.assert_array_equal(out, expected)

    def test_unlabelled_pages_are_channels(self, tmp_path: Path) -> None:
        """A plain multi-page TIFF without axes metadata is read as channels."""
        folder = tmp_path / "s" / "img"
        folder.mkdir(parents=True)
        tifffile.imwrite(
            folder / "pages.tif", fluorescence(3), photometric="minisblack"
        )
        out = MicroscopyImage(str(tmp_path), "s", "img").process_image(settings())
        assert tifffile.TiffFile(out).series[0].shape == (3, 900, 1100)


class TestImageType:
    """Configuration first, then acquisition metadata, then layout, then polarity."""

    def test_metadata_states_the_type(self) -> None:
        """OME acquisition mode, contrast method and emission wavelength state the image type."""
        assert (
            _hint(
                [ChannelInfo("a", acquisition_mode="LaserScanningConfocalMicroscopy")]
            )[0]
            == "fluorescence"
        )
        assert (
            _hint([ChannelInfo("a", contrast_method="Brightfield")])[0] == "brightfield"
        )
        assert _hint([ChannelInfo("a", emission_nm=520.0)])[0] == "fluorescence"
        assert _hint([ChannelInfo("a")]) == ("", "")

    def test_rgb_brightfield_is_stored_lossless_as_rgb(self, tmp_path: Path) -> None:
        """An RGB section is stored interleaved, JPEG 2000 lossless, bit-identical."""
        image = brightfield()
        sample = write(
            tmp_path, "he.ome.tif", image, photometric="rgb", metadata={"axes": "YXS"}
        )
        out = sample.process_image(settings())
        with tifffile.TiffFile(out) as tif:
            assert tif.series[0].axes == "YXS"
            assert tif.pages[0].compression == tifffile.COMPRESSION.JPEG2000
            np.testing.assert_array_equal(tif.series[0].asarray(), image)
        annotations = cast(dict[str, str], read_ome_annotations(out))
        assert annotations["focus_image_type"] == "brightfield"
        assert "bright background" in annotations["focus_image_type_evidence"]

    def test_configured_type_wins(self, tmp_path: Path) -> None:
        """A configured image_type overrides the inferred one and is recorded as such."""
        sample = write(
            tmp_path,
            "he.ome.tif",
            brightfield(),
            photometric="rgb",
            metadata={"axes": "YXS"},
        )
        out = sample.process_image(settings(image_type="fluorescence"))
        annotations = cast(dict[str, str], read_ome_annotations(out))
        assert annotations["focus_image_type"] == "fluorescence"
        assert annotations["focus_image_type_evidence"] == "set in the configuration"


class TestOutput:
    """Tiled SubIFD pyramid, OME channels, tissue ROI, display rendering, crop and fill."""

    def test_pyramid_metadata_roi_and_display(self, tmp_path: Path) -> None:
        """SubIFD levels, 512 px tiles, OME channels, one tissue polygon and per-channel windows."""
        sample = write(
            tmp_path,
            "if.ome.tif",
            fluorescence(4),
            photometric="minisblack",
            metadata={
                "axes": "CYX",
                "PhysicalSizeX": 0.5,
                "PhysicalSizeY": 0.5,
                "Channel": {"Name": NAMES},
            },
        )
        out = sample.process_image(settings())
        with tifffile.TiffFile(out) as tif:
            assert [lvl.shape for lvl in tif.series[0].levels] == [
                (4, 900, 1100),
                (4, 450, 550),
                (4, 225, 275),
            ]
            page = cast(tifffile.TiffPage, tif.pages[0])
            assert page.is_tiled and page.tilewidth == 512
        root = ome_root(out)
        pixels = tags(root, "Pixels")[0]
        assert pixels.get("SizeC") == "4" and pixels.get("PhysicalSizeXUnit") == "µm"
        assert len(tags(root, "Polygon")) == 1
        display = cast(dict[str, object], read_display_settings(out))
        channels = cast(list[dict[str, object]], display["channels"])
        assert channels[0]["color"] == "0000FF"  # DAPI
        low, high = cast(list[float], channels[3]["window"])
        assert (
            60 <= low < 2460 <= high <= 2500
        )  # tissue of the fourth channel spans 2460 to 2499

    @pytest.mark.parametrize(
        ("kind", "fill"), [("fluorescence", 0), ("brightfield", 255)]
    )
    def test_crop_and_fill_follow_the_type(
        self, tmp_path: Path, kind: str, fill: int
    ) -> None:
        """Opt-in crop and fill: white for brightfield, 0 for fluorescence."""
        rgb = kind == "brightfield"
        image = brightfield() if rgb else fluorescence(3)
        sample = write(
            tmp_path,
            "x.ome.tif",
            image,
            photometric="rgb" if rgb else "minisblack",
            metadata={
                "axes": "YXS" if rgb else "CYX",
                "PhysicalSizeX": 2.0,
                "PhysicalSizeY": 2.0,
            },
        )
        out = sample.process_image(
            settings(image_type=kind, crop_to_tissue=True, remove_background=True)
        )
        annotations = cast(dict[str, str], read_ome_annotations(out))
        assert annotations["focus_background_fill"] == str(fill)
        x, y = (int(v) for v in annotations["focus_crop_offset"].split(","))
        data = tifffile.imread(out)
        corner = data[0, 0] if rgb else data[:, 0, 0]
        assert (x, y) != (0, 0) and np.all(corner == fill)

    def test_pixel_size_fallback_and_metadata_precedence(self, tmp_path: Path) -> None:
        """The configured pixel_size is used when the metadata has none."""
        sample = write(
            tmp_path,
            "he.ome.tif",
            brightfield(),
            photometric="rgb",
            metadata={"axes": "YXS"},
        )
        out = sample.process_image(settings(pixel_size=0.25))
        assert (
            cast(dict[str, str], read_ome_annotations(out))["focus_pixel_size_source"]
            == "configuration"
        )
        assert tags(ome_root(out), "Pixels")[0].get("PhysicalSizeX") == "0.25"

    def test_level_reader_returns_channels_last(self, tmp_path: Path) -> None:
        """Stored CYX levels read back as HWC."""
        sample = write(
            tmp_path,
            "if.ome.tif",
            fluorescence(4),
            photometric="minisblack",
            metadata={"axes": "CYX"},
        )
        level = load_ome_tiff_level(sample.process_image(settings()), 1)
        assert level.shape == (450, 550, 4)
