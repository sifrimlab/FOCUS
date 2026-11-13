"""Read aligned anchor geometry and resolve image physical scale for encoding."""

from numpy.typing import NDArray

from typing import cast
from focus.spatial.footprint import AnchorGeometry
from focus.runtime.reporting import StepReporter

from dataclasses import dataclass

import h5py
import numpy as np
from anndata.io import read_elem

from focus.config.options import ModalityType
from focus.spatial.footprint_shape import read_spot_size
from focus.spatial.warp import stored_warp_is_nonrigid
from focus.runtime.reporter_context import get_reporter
from focus.io.ome import read_ome_physical_size_um

_DEFAULT_SPOT_SIZE = np.array([1.0, 1.0])


@dataclass
class AnchorArrays:
    """What registration needs from one aligned anchor file."""

    spatial: NDArray[np.float32 | np.float64]  # (N, 2) anchor-native coordinates
    mapped: NDArray[
        np.float32 | np.float64
    ]  # (N, 2) the same spots in the target frame
    spot_size: NDArray[np.float64 | np.float32]  # (2,) anchor spot size, anchor units
    spot_size_given: bool  # False when the anchor carried no usable spot_size
    nonrigid: bool = (
        False  # the alignment stored a non-rigid (landmark) warp for this target
    )

    @property
    def n_obs(self) -> int:
        """Return the number of anchor observations without loading feature matrices."""
        return len(self.spatial)


def read_anchor_arrays(path: str, target_name: str) -> AnchorArrays | None:
    """Read the anchor's coordinates and spot size; None when ``obsm['{target}_spatial']`` is missing."""
    with h5py.File(path, "r") as f:
        key = f"obsm/{target_name}_spatial"
        if key not in f:
            return None
        spatial = np.asarray(read_elem(f["obsm/spatial"]), dtype=np.float32)
        mapped = np.asarray(read_elem(f[key]), dtype=np.float32)
        raw = read_elem(f["uns/spot_size"]) if "uns/spot_size" in f else None
        warp_key = f"uns/focus_alignment_warps/{target_name}"
        warp = read_elem(f[warp_key]) if warp_key in f else None
    spot_size = read_spot_size(raw)
    given = spot_size is not None
    return AnchorArrays(
        spatial,
        mapped,
        cast(np.ndarray, spot_size) if given else _DEFAULT_SPOT_SIZE.copy(),
        given,
        stored_warp_is_nonrigid(warp),
    )


def resolve_um_per_px(
    image_path: str,
    geom: AnchorGeometry,
    anchor_type: str | None,
    reporter: StepReporter | None = None,
) -> float | None:
    """
    Level-0 µm per pixel of a preprocessed image.

    1. ``PhysicalSizeX/Y`` of the image's OME-XML (written by FOCUS preprocessing from the
       source file's metadata).
    2. For an MSI anchor, whose coordinates are always µm, the scale of the alignment map.
    3. Otherwise unknown (None, with a warning).
    """
    reporter = reporter or get_reporter()
    size = read_ome_physical_size_um(image_path)
    if size is not None:
        return float(np.sqrt(size[0] * size[1]))
    if anchor_type == ModalityType.MSI:
        scale = geom.px_per_anchor_unit()
        if np.isfinite(scale) and scale > 0:
            um = 1.0 / scale
            reporter.detail(
                f"Pixel size taken from the alignment with the MSI anchor: {um:.4g} µm/px"
            )
            return um
    reporter.warning("The image has no pixel size: it is encoded at full resolution")
    return None
