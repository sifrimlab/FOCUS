"""Anchor footprint geometry and its mapping into the aligned target frame."""

from numpy.typing import NDArray

from typing import cast

from dataclasses import dataclass

import numpy as np

from focus.spatial.transforms import AnchorToTarget, fit_anchor_to_target
from focus.spatial.footprint_shape import FootprintShape, footprint_shape


# --------------------------------------------------------------------------- geometry


@dataclass
class AnchorGeometry:
    """Anchor footprints plus the map into one target frame."""

    centres: NDArray[np.float64]  # (N, 2) anchor frame
    valid: NDArray[np.bool_]  # (N,) spots that can be placed in the target frame
    shape: FootprintShape
    transform: AnchorToTarget

    @classmethod
    def from_arrays(
        cls,
        spatial: np.ndarray,
        mapped: np.ndarray,
        spot_size: np.ndarray,
        nonrigid: bool = False,
    ) -> "AnchorGeometry":
        """Fit anchor footprints from native coordinates, mapped coordinates, and spot size.

        ``nonrigid`` marks coordinates produced by a landmark warp (see
        :func:`focus.spatial.transforms.fit_anchor_to_target`)."""
        transform, valid = fit_anchor_to_target(spatial, mapped, spot_size, nonrigid)
        centres = np.asarray(spatial, dtype=np.float64)
        shape = footprint_shape(centres[valid], spot_size)
        geom = cls(centres, valid, shape, transform)
        # A spot whose footprint crosses the projective horizon cannot be placed.
        corners = geom._target_corners(np.flatnonzero(valid))
        geom.valid[np.flatnonzero(valid)[~np.isfinite(corners).all(axis=(1, 2))]] = (
            False
        )
        return geom

    def describe(self) -> dict[str, object]:
        """Provenance of the geometry, for ``uns['focus_registration_geometry']``."""
        return {
            "transform": self.transform.kind,
            "lattice": self.shape.lattice,
            "footprint": self.shape.kind,
            "footprint_angle": float(self.shape.angle),
        }

    def _corner_offsets(self) -> np.ndarray:
        """``(4, 2)`` corners of the footprint's bounding rectangle, relative to its centre."""
        hx, hy = self.shape.half
        local = np.array([[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy]])
        c, s = np.cos(self.shape.angle), np.sin(self.shape.angle)
        return cast(np.ndarray, local @ np.array([[c, s], [-s, c]]))

    def _target_corners(self, rows: np.ndarray) -> np.ndarray:
        """``(K, 4, 2)`` footprint bounding-rectangle corners in the target frame."""
        pts = (self.centres[rows][:, None, :] + self._corner_offsets()[None]).reshape(
            -1, 2
        )
        return self.transform.to_target(pts, np.repeat(rows, 4)).reshape(
            len(rows), 4, 2
        )

    def target_centres(self) -> np.ndarray:
        """Return the mapped centers of the anchor footprints."""
        rows = np.arange(len(self.centres))
        out = np.full((len(rows), 2), np.nan)
        ok = rows[self.valid]
        out[ok] = self.transform.to_target(self.centres[ok], ok)
        return out

    def target_bboxes(self) -> np.ndarray:
        """``(N, 4)`` ``[xmin, ymin, xmax, ymax]`` of each footprint in the target frame (NaN if invalid).

        The image of the footprint's bounding rectangle is convex in front of the horizon, so
        the box of its four mapped corners contains the whole mapped footprint.
        """
        out = np.full((len(self.centres), 4), np.nan)
        ok = np.flatnonzero(self.valid)
        corners = self._target_corners(ok)
        out[ok, :2] = corners.min(axis=1)
        out[ok, 2:] = corners.max(axis=1)
        return out

    def search_radii(self) -> np.ndarray:
        """Radius around each mapped centre that contains the whole mapped footprint."""
        out = np.full(len(self.centres), np.nan)
        ok = np.flatnonzero(self.valid)
        corners = self._target_corners(ok)
        centre = self.transform.to_target(self.centres[ok], ok)
        reach = np.linalg.norm(corners - centre[:, None, :], axis=2).max(axis=1)
        out[ok] = reach * (1.0 + 1e-6) + self.transform.max_residual + 1e-9
        return out

    def pixel_bboxes(self, height: int, width: int) -> np.ndarray:
        """``(N, 4)`` int64 half-open ``[c0, r0, c1, r1)`` of the pixels whose centres can fall
        inside each footprint, clamped to a ``height x width`` image (empty for invalid spots)."""
        boxes = self.target_bboxes()
        out = np.zeros((len(boxes), 4), dtype=np.int64)
        ok = np.isfinite(boxes).all(axis=1)
        b = boxes[ok]
        out[ok, 0] = np.clip(np.ceil(b[:, 0] - 0.5), 0, width)
        out[ok, 1] = np.clip(np.ceil(b[:, 1] - 0.5), 0, height)
        out[ok, 2] = np.clip(np.floor(b[:, 2] - 0.5) + 1, 0, width)
        out[ok, 3] = np.clip(np.floor(b[:, 3] - 0.5) + 1, 0, height)
        out[:, 2] = np.maximum(out[:, 2], out[:, 0])
        out[:, 3] = np.maximum(out[:, 3], out[:, 1])
        return out

    def contains(
        self, rows: np.ndarray, pts: np.ndarray, closed: bool = True
    ) -> tuple[np.ndarray, np.ndarray]:
        """Pairwise test: is target-frame point ``pts[k]`` inside footprint ``rows[k]``?

        Returns ``(inside, d2)`` where ``d2`` is the squared anchor-frame distance from the
        anchor centre (NaN where the point cannot be mapped back). With ``closed=False`` the
        upper edges are excluded, so footprints that tile the plane never share a boundary point.
        """
        rows = np.asarray(rows, dtype=np.intp)
        q = self.transform.to_anchor(pts, rows)
        d = q - self.centres[rows]
        c, s = np.cos(self.shape.angle), np.sin(self.shape.angle)
        u = d @ np.array([[c, -s], [s, c]])
        h = self.shape.half
        with np.errstate(invalid="ignore"):
            if self.shape.kind == "ellipse":
                radius = (u[:, 0] / h[0]) ** 2 + (u[:, 1] / h[1]) ** 2
                inside = radius <= 1.0 if closed else radius < 1.0
            elif closed:
                inside = (np.abs(u[:, 0]) <= h[0]) & (np.abs(u[:, 1]) <= h[1])
            else:
                inside = ((u >= -h) & (u < h)).all(axis=1)
        inside &= self.valid[rows] & np.isfinite(u).all(axis=1)
        return inside, (d**2).sum(axis=1)

    def px_per_anchor_unit(self) -> float:
        """Linear scale of the map at the centroid of the placed spots (target units per anchor unit)."""
        ok = np.flatnonzero(self.valid)
        centre = self.centres[ok].mean(axis=0, keepdims=True)
        row = ok[np.argmin(np.linalg.norm(self.centres[ok] - centre, axis=1))]
        jac = self.transform.jacobian(
            centre if self.transform.kind != "local_affine" else self.centres[[row]],
            np.array([row]),
        )
        return float(np.sqrt(abs(np.linalg.det(jac[0]))))
