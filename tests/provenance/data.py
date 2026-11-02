"""Synthetic data and shared setup for provenance scenarios."""

from typing import cast

import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import tifffile

# Blob rows 1200:2000, cols 800:1500; with a 100 px margin the crop starts at (700, 1100).
_BLOB = (slice(1200, 2000), slice(800, 1500))
_OFFSET = np.array([700, 1100], dtype=np.float32)


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
