"""Compact native spot transport and validated acquisition-lattice footprints."""
import struct
import numpy as np

from focus.preprocessing._utils import _fit_lattice


def pack_spots(coords, classes, foreground, ids=None):
    """FSP1: 16-byte LE header, xy f32, class u32, row id u32, foreground u8."""
    n = len(coords)
    ids = np.arange(n, dtype=np.uint32) if ids is None else ids
    return b"".join((struct.pack("<4sIII", b"FSP1", n, 1, 0),
        np.asarray(coords, dtype="<f4").tobytes(),
        np.asarray(classes, dtype="<u4").tobytes(),
        np.asarray(ids, dtype="<u4").tobytes(),
        np.asarray(foreground, dtype="u1").tobytes()))


def acquisition_basis(adata):
    """Infer square axes only when row/col metadata genuinely describes a regular lattice.

    Arbitrary cell layouts keep generic axis-aligned footprints. This is display metadata,
    not an alignment transform and never changes stored coordinates.
    """
    if not {"array_row", "array_col"}.issubset(adata.obs):
        return None
    axes = _fit_lattice(adata.obs[["array_col", "array_row"]].to_numpy(dtype=float), adata.obsm["spatial"])
    if axes is None:
        return None
    lengths = np.linalg.norm(axes, axis=1)
    unit = axes / lengths[:, None]
    size = np.broadcast_to(np.asarray(adata.uns.get("spot_size", 1)).flatten(), (2,))
    if abs(unit[0] @ unit[1]) > 0.02 or not np.allclose(lengths, size, rtol=0.05, atol=0.01):
        return None
    return unit.tolist()
