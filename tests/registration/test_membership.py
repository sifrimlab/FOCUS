"""Membership behavioral scenarios."""

import scipy.sparse as sp
from typing import cast

import numpy as np

from focus.spatial.footprint import AnchorGeometry
from focus.spatial.membership import (
    footprint_membership,
    pixel_footprint_membership,
    row_normalize,
)
from tests.registration.data import square_lattice, legacy_interpolate


class TestMembership:
    """Behavioral contracts for membership."""

    def test_matches_legacy_when_frames_coincide(self) -> None:
        """Verify matches legacy when frames coincide."""
        rng = np.random.default_rng(3)
        for anchor in (
            square_lattice(15, 10.0),
            np.round(rng.uniform(0, 150, (200, 2)) * 4) / 4,
        ):
            target = np.round(rng.uniform(-10, 160, (3000, 2)) * 4) / 4
            features = rng.normal(size=(len(target), 4))
            spot = np.array([10.0, 6.0])
            geom = AnchorGeometry.from_arrays(anchor, anchor.astype(np.float32), spot)
            assert geom.shape.kind == "rectangle"
            w = footprint_membership(geom, target)
            expected, members = legacy_interpolate(anchor, spot, target, features)
            for i in range(len(anchor)):
                assert set(w[i].indices.tolist()) == members[i]
            np.testing.assert_allclose(row_normalize(w) @ features, expected, atol=1e-6)

    def test_scaling_both_frames_keeps_membership(self) -> None:
        """Verify scaling both frames keeps membership."""
        rng = np.random.default_rng(4)
        anchor = square_lattice(12, 10.0)
        target = rng.uniform(0, 120, (2000, 2))
        spot = np.array([10.0, 10.0])
        base = footprint_membership(
            AnchorGeometry.from_arrays(anchor, anchor, spot), target, "ones"
        )
        moved = footprint_membership(
            AnchorGeometry.from_arrays(anchor, anchor * 2 + [3, 9], spot),
            target * 2 + [3, 9],
            "ones",
        )
        assert cast(sp.csr_matrix, base != moved).nnz == 0

    def test_pixel_membership_indexes_the_crop(self) -> None:
        """Verify pixel membership indexes the crop."""
        anchor = np.array([[10.5, 10.5], [20.5, 14.5], [3.0, 3.0], [8.0, 30.0]])
        geom = AnchorGeometry.from_arrays(anchor, anchor, np.array([4.0, 4.0]))
        crop = (5, 6, 25, 20)
        w = pixel_footprint_membership(geom, crop, "ones")
        width = crop[2] - crop[0]
        for i, (cx, cy) in enumerate(anchor):
            cols, rows = np.meshgrid(
                np.arange(crop[0], crop[2]), np.arange(crop[1], crop[3])
            )
            inside = (np.abs(cols + 0.5 - cx) <= 2) & (np.abs(rows + 0.5 - cy) <= 2)
            expected = set(
                ((rows[inside] - crop[1]) * width + cols[inside] - crop[0]).tolist()
            )
            assert set(w[i].indices.tolist()) == expected
