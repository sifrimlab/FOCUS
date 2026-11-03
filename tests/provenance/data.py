"""Synthetic data and shared setup for provenance scenarios."""

from typing import cast

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import tifffile

from focus.io.provenance import CROP_OFFSET_KEY, read_ome_annotations
from focus.preprocessing.microscopy.parameters import ImageSettings

# Blob rows 1200:2000, cols 800:1500 at 0.5 µm/px; the 100 µm margin is 200 px, so the crop
# starts near (600, 1000), within the detection canvas pixel (about 2 px here).
_BLOB = (slice(1200, 2000), slice(800, 1500))
_OFFSET = np.array([600, 1000], dtype=np.float32)
CROP = ImageSettings("brightfield", "auto", True, False, None)
NO_CROP = ImageSettings("brightfield", "auto", False, False, None)
FILLED = ImageSettings("brightfield", "auto", True, True, None)


def crop_offset(path: str) -> np.ndarray:
    """``focus_crop_offset`` of an output as ``[x, y]``."""
    value = cast(dict[str, str], read_ome_annotations(path))[CROP_OFFSET_KEY]
    return np.array([float(v) for v in value.split(",")], dtype=np.float32)


def _write_source(path: Path, shape: tuple[int, int] = (2500, 2000)) -> None:
    """RGB uint8 brightfield-like TIFF at 0.5 µm/px (resolution tags in centimetres)."""
    image = np.full((*shape, 3), 245, np.uint8)
    image[_BLOB] = [150, 60, 120]
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path,
        image,
        photometric="rgb",
        resolution=(20000, 20000),
        resolutionunit="CENTIMETER",
    )


def _strip_provenance(path: str) -> None:
    """Rewrite ``path`` as the pre-provenance writer did: same pixels, no MapAnnotation, no PhysicalSize."""
    with tifffile.TiffFile(path) as tif:
        description = cast(tifffile.TiffPage, tif.pages[0]).description
        pages = [
            (p.asarray(), cast(tifffile.TiffPage, p).photometric) for p in tif.pages
        ]
    root = ET.fromstring(description)
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.endswith("StructuredAnnotations"):
                parent.remove(child)
            elif child.tag.endswith("Pixels"):
                for attr in [a for a in child.attrib if a.startswith("PhysicalSize")]:
                    del child.attrib[attr]
    xml = (
        ET.tostring(root, encoding="unicode")
        .encode("ascii", "xmlcharrefreplace")
        .decode("ascii")
    )
    with tifffile.TiffWriter(path, bigtiff=True) as tif:
        for i, (data, photometric) in enumerate(pages):
            rgb = data.ndim == 3
            tif.write(
                data,
                description=xml if i == 0 else None,
                photometric="rgb" if rgb else "minisblack",
                metadata={"axes": "YXC" if rgb else "YX"},
                compression="zlib",
                predictor=2,
            )
