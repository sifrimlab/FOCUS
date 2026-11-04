"""NativeDisplay behavioral scenarios."""

from focus.alignment.result import parse_alignment_result
from unittest.mock import patch
from typing import cast

from tests.assertions import assert_close
import threading

import anndata as ad
import numpy as np
import scipy.sparse as sp

from focus.alignment.coordinator import DirectMappingAligner
from focus.alignment.spot_payload import pack_spots, acquisition_footprint
from focus.gui.alignment import DirectMappingAlignmentGUI
from focus.spot_colors import cluster_colors, label_order


class TestNativeDisplay:
    """Behavioral contracts for native display."""

    def test_packed_payload_retains_ids_classes_and_geometry(self) -> None:
        """Verify packed payload retains ids classes and geometry."""
        xy = np.array([[1, 2], [3, 4], [5, 6]], np.float32)
        payload = pack_spots(xy, [0, 16, 32], [True, False, True], [8, 4, 1])
        assert payload[:4] == b"FSP1"
        assert len(payload) == 16 + 17 * 3
        np.testing.assert_array_equal(
            np.frombuffer(payload, "<f4", count=6, offset=16).reshape(-1, 2), xy
        )
        np.testing.assert_array_equal(
            np.frombuffer(payload, "<u4", count=3, offset=16 + 12 * 3), [8, 4, 1]
        )
        gui = DirectMappingAlignmentGUI(1, threading.Event())
        gui._target_metadata = {"modality_type": "SPOT"}
        gui._target_payload = payload
        response = gui.app.test_client().get("/target/payload")
        assert response.data == payload
        assert response.mimetype == "application/octet-stream"

    def test_validated_rotated_lattice_and_irregular_layout(self) -> None:
        """Verify validated rotated lattice and irregular layout."""
        row, col = np.mgrid[:8, :8]
        theta = 0.2
        basis = np.array(
            [[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]]
        )
        a = ad.AnnData(sp.csr_matrix((row.size, 2)))
        a.obsm["spatial"] = np.column_stack([col.ravel(), row.ravel()]) @ basis * 16
        a.uns["spot_size"] = [16, 16]
        # Inferred from coordinates alone; each axis matches a true axis up to sign and order.
        footprint = acquisition_footprint(a)
        assert footprint is not None and footprint[1] == "rectangle"
        for axis in np.asarray(footprint[0]):
            assert_close(np.max(np.abs(basis @ axis)), 1, places=6)
        a.obsm["spatial"][0] += 8
        assert acquisition_footprint(a) is None

    def test_hexagonal_lattice_gets_an_oriented_hexagon(self) -> None:
        """A rotated hexagonal lattice (20 um pitch, rows 17.32 um apart) is drawn as hexagons."""
        row, col = np.mgrid[:12, :12]
        xy = np.column_stack(
            [col.ravel() * 20.0 + (row.ravel() % 2) * 10.0, row.ravel() * 10 * np.sqrt(3)]
        )
        theta = 0.4
        rot = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        a = ad.AnnData(sp.csr_matrix((len(xy), 2)))
        a.obsm["spatial"] = xy @ rot
        a.uns["spot_size"] = [20, 20]
        footprint = acquisition_footprint(a)
        assert footprint is not None and footprint[1] == "hexagon"
        u, v = np.asarray(footprint[0])
        assert_close(u @ v, 0, places=9)
        # u is a nearest-neighbour direction: a multiple of 60 degrees from the rotated x axis.
        offset = (np.arctan2(u[1], u[0]) - np.arctan2(rot[0, 1], rot[0, 0])) % (np.pi / 3)
        assert_close(min(offset, np.pi / 3 - offset), 0, places=6)

    def test_large_display_has_every_original_spot_and_transform(self) -> None:
        """Verify large display has every original spot and transform."""
        n = 100_003
        xy = np.column_stack([np.arange(n), np.arange(n) % 316]).astype("float32")
        labels = np.array([str(i % 33) for i in range(n)])
        aligner = DirectMappingAligner.__new__(DirectMappingAligner)
        aligner._exact_spots = False
        fake_spots = (
            xy,
            np.array([16.0, 16.0]),
            np.ones(n, bool),
            labels,
            cluster_colors(labels),
        )
        with patch.object(
            aligner.inputs, "_load_anndata_spots", return_value=fake_spots
        ):
            meta, payload, full, _ = aligner.inputs._prepare_spot_data("unused", "ST")
        assert isinstance(payload, bytes)
        assert full is not None
        assert meta["spot_size"] == [16, 16]
        assert meta["spot_count"] == n
        ids = np.frombuffer(payload, "<u4", count=n, offset=16 + 12 * n)
        points = np.frombuffer(payload, "<f4", count=2 * n, offset=16).reshape(-1, 2)
        np.testing.assert_array_equal(points, xy[ids])
        classes = np.frombuffer(payload, "<u4", count=n, offset=16 + 8 * n)
        np.testing.assert_array_equal(
            np.array(meta["cluster_labels"])[classes], labels[ids]
        )
        matrix = np.array([[1, 0.1, 20], [0, 1, -5], [0, 0, 1]])
        result = parse_alignment_result(
            {"transform_matrix": matrix.ravel(order="F").tolist()}, payload, full
        )
        np.testing.assert_allclose(
            cast(np.ndarray, result),
            (np.column_stack([xy, np.ones(n)]) @ matrix.T)[:, :2],
            rtol=1e-6,
        )

    def test_label_order_and_colors_do_not_cycle(self) -> None:
        """Verify label order and colors do not cycle."""
        labels = [str(i) for i in range(100)] + ["cell type", "unassigned"]
        colors = cluster_colors(labels)
        assert len(set(colors.values())) == len(labels)
        assert label_order(["10", "2", "unassigned"]) == ["2", "10", "unassigned"]
        assert colors == cluster_colors(list(reversed(labels)))
