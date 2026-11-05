"""Per-sample pixel phenotype label maps: one uint8 OME image per source image, same placement.

Pixel value ``0`` is not measured (or not read: outside every footprint's bounding region), value
``k`` is the ``k``-th composition entry with the largest share (the dominant endmember, or the Pixie
metacluster). Each image keeps its source placement so the map overlays the preprocessed file.
Pyramid levels take every other pixel: labels are categories and are never averaged.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

import numpy as np
import zarr

from focus.io import ome_write
from focus.registration.pixel_readout import IDENTITY_PLACEMENT, RamanTarget

NOT_MEASURED = "not measured"
SUFFIX = "_pixel_phenotypes.ome.tiff"
_CODEC: ome_write.Codec = ("zstd", {"level": 10}, None)


def label_map_file(path: str, sample_id: str, modality: str) -> str:
    """Label map of one sample, next to its registration file."""
    from focus.io.paths import registration_file

    out = registration_file(path, sample_id, modality, "h5ad")
    return out.replace("_processed_aligned_registered.h5ad", SUFFIX)


class LabelMaps:
    """Staged label planes of one sample, written as one OME-TIFF by :meth:`write`."""

    def __init__(self, output: str) -> None:
        """Stage the planes next to ``output`` (removed after writing)."""
        self._output = output
        self._workdir = output + ".stage"
        self._images: list[tuple[RamanTarget, zarr.Array[Any], list[str], str]] = []

    def plane(
        self, target: RamanTarget, shape: tuple[int, int], names: list[str], group: str
    ) -> zarr.Array[Any]:
        """A zero-filled ``(1, H, W)`` uint8 plane for one image's labels."""
        os.makedirs(self._workdir, exist_ok=True)
        plane = zarr.create_array(
            store=os.path.join(self._workdir, f"{len(self._images)}_0.zarr"),
            shape=(1, *shape),
            chunks=(1, ome_write.TILE, ome_write.TILE),
            dtype=np.uint8,
            fill_value=0,
        )
        self._images.append((target, plane, names, group))
        return plane

    def _levels(self, index: int, plane: zarr.Array[Any]) -> list[zarr.Array[Any]]:
        """Nearest-neighbour pyramid of a staged label plane."""
        levels = [plane]
        while max(levels[-1].shape[1:]) > ome_write.TILE:
            previous = levels[-1]
            h, w = previous.shape[1:]
            level = zarr.create_array(
                store=os.path.join(self._workdir, f"{index}_{len(levels)}.zarr"),
                shape=(1, (h + 1) // 2, (w + 1) // 2),
                chunks=(1, ome_write.TILE, ome_write.TILE),
                dtype=np.uint8,
                fill_value=0,
            )
            rows = 8 * ome_write.TILE
            for top in range(0, level.shape[1], rows):
                level[:, top : top + rows] = previous[
                    :, 2 * top : 2 * (top + rows) : 2, ::2
                ]
            levels.append(level)
        return levels

    def write(self, pixel_size: np.ndarray | None) -> str | None:
        """Write the staged maps; the output path, or None when there is none."""
        if not self._images:
            return None
        staged, described = [], []
        for index, (target, plane, names, group) in enumerate(self._images):
            scale = np.asarray(target.placement[2:], dtype=np.float64)
            size = None if pixel_size is None else pixel_size * scale
            staged.append(
                ome_write.StagedImage(self._levels(index, plane), False, size)
            )
            values: dict[str, object] = {
                "focus_phenotype_group": group,
                "focus_phenotype_labels": json.dumps([NOT_MEASURED, *names]),
            }
            if target.placement != IDENTITY_PLACEMENT:
                values["focus_placement"] = ",".join(f"{v:g}" for v in target.placement)
            described.append(
                ome_write.OmeImage(
                    name=target.name,
                    shape_yx=(int(plane.shape[1]), int(plane.shape[2])),
                    dtype=np.dtype(np.uint8),
                    rgb=False,
                    channels=[ome_write.ChannelInfo("phenotype")],
                    colours=["FFFFFF"],
                    pixel_size=size,
                    significant_bits=None,
                    objective=(None, None),
                    contours=[],
                    annotations=values,
                )
            )
        xml = ome_write.build_ome_xml(described, {"focus_method": "pixel-phenotypes-1"})
        ome_write.write_images(self._output, staged, xml, _CODEC)
        shutil.rmtree(self._workdir, ignore_errors=True)
        return self._output

    def discard(self) -> None:
        """Remove the staged planes without writing."""
        shutil.rmtree(self._workdir, ignore_errors=True)
