"""Compact native spot transport and validated acquisition-lattice footprints."""

from numpy.typing import ArrayLike
from typing import cast

from anndata import AnnData
import struct
import numpy as np

from focus.spatial.footprint_shape import _reduce_basis, classify_lattice
from focus.spatial.lattice import infer_lattice


def pack_spots(
    coords: np.ndarray,
    classes: ArrayLike,
    foreground: ArrayLike,
    ids: ArrayLike | None = None,
    features: np.ndarray | None = None,
) -> bytes:
    """
    FSP1: 16-byte LE header (magic, n, version 1, 0), xy f32, class u32, row id u32,
    foreground u8.

    With ``features`` (n x m uint8 feature images), version 2: the last header field holds ``m``
    and the images follow spot by spot (``features[i, :]``), 0 meaning no value.
    """
    n = len(coords)
    ids = np.arange(n, dtype=np.uint32) if ids is None else ids
    images = (
        None if features is None else np.asarray(features, dtype="u1").reshape(n, -1)
    )
    version, m = (
        (1, 0) if images is None or images.shape[1] == 0 else (2, images.shape[1])
    )
    parts = [
        struct.pack("<4sIII", b"FSP1", n, version, m),
        np.asarray(coords, dtype="<f4").tobytes(),
        np.asarray(classes, dtype="<u4").tobytes(),
        np.asarray(ids, dtype="<u4").tobytes(),
        np.asarray(foreground, dtype="u1").tobytes(),
    ]
    if version == 2:
        parts.append(np.ascontiguousarray(images).tobytes())
    return b"".join(parts)


def acquisition_footprint(adata: AnnData) -> tuple[list[list[float]], str] | None:
    """Oriented display footprint of the acquisition lattice: unit axes ``[u, v]`` and shape.

    The lattice is inferred from ``.obsm['spatial']`` alone (no array index columns needed).

    - ``rectangle``: orthogonal axes whose steps match ``.uns['spot_size']``; a ``spot_size``
      rectangle along the lattice axes.
    - ``hexagon``: a hexagonal lattice (:func:`classify_lattice`). ``u`` is a nearest-neighbour
      direction and ``v`` its perpendicular; the footprint is the hexagonal Voronoi cell of the
      lattice (flats facing the six neighbours), ``spot_size[0]`` across the flats along ``u``.
      It tiles the lattice when ``spot_size`` equals the pitch. An axis-aligned square of the
      same size overlaps the rows, which are 0.87 pitch apart, and staggers every other row by
      half a spot.

    Other layouts return None and keep generic axis-aligned footprints. This is display
    metadata, not an alignment transform, and never changes stored coordinates.
    """
    lattice = infer_lattice(np.asarray(adata.obsm["spatial"], dtype=float))
    if lattice is None:
        return None
    axes = lattice[1]
    if classify_lattice(axes)[0] == "hexagonal":
        a = _reduce_basis(axes)[0]
        u = a / np.linalg.norm(a)
        return [[float(u[0]), float(u[1])], [float(-u[1]), float(u[0])]], "hexagon"
    lengths = np.linalg.norm(axes, axis=1)
    unit = axes / lengths[:, None]
    size = np.broadcast_to(np.asarray(adata.uns.get("spot_size", 1)).flatten(), (2,))
    if abs(unit[0] @ unit[1]) > 0.02 or not np.allclose(
        lengths, size, rtol=0.05, atol=0.01
    ):
        return None
    return cast(list[list[float]], unit.tolist()), "rectangle"
