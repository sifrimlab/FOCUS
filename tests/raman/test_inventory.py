"""Selection of SRS acquisitions from a LAS X session."""

import xml.etree.ElementTree as ET

import numpy as np

from focus.preprocessing.raman.inventory import (
    Acquisition,
    Decision,
    read_elements,
    select,
)
from tests.raman.lif_xml import STOKES_NM, element, session


def _decisions(root: ET.Element) -> tuple[list[Acquisition], dict[str, Decision]]:
    acquisitions, decisions = select(read_elements("session.lif", root))
    return acquisitions, {d.path: d for d in decisions}


class TestInventory:
    """Every element gets a decision with its reason."""

    def test_skips_incomplete_merged_and_frames(self) -> None:
        """Aborted scans, LAS X merges, frames without stage positions and snapshots are skipped."""
        root = session(
            element("scan"),
            element("aborted", stored_tiles=2),
            element("merged", tiles=None, planned=1, has_z=False, size=16),
            element("frame", tiles=None, planned=0),
            element("snapshot", tiles=None, planned=1),
        )
        acquisitions, decisions = _decisions(root)
        assert [a.name for a in acquisitions] == ["scan"]
        assert decisions["aborted"].reason.startswith("incomplete scan (2 of 4")
        assert "merged" in decisions["merged"].reason
        assert decisions["frame"].reason == "single frame without a stage position"
        assert decisions["snapshot"].reason == "single snapshot outside a tile scan job"

    def test_latest_acquisition_of_a_window_supersedes(self) -> None:
        """Same window over overlapping areas: the later scan is kept; other windows stay."""
        root = session(
            element("first", timestamp=1, gain=20),
            element("fingerprint", timestamp=2, pump=(870.0, 894.0)),
            element("second", timestamp=3, gain=50),
            element("elsewhere", timestamp=4, origin_m=(0.09, 0.09)),
        )
        acquisitions, decisions = _decisions(root)
        assert [a.name for a in acquisitions] == ["fingerprint", "second", "elsewhere"]
        assert decisions["first"].reason == "superseded by the later 'second'"
        assert acquisitions[1].superseded == ["first"]

    def test_mark_and_find_positions_form_one_acquisition(self) -> None:
        """Single-tile positions of one job with the same settings are grouped."""
        positions = [
            element(f"P{i}", tiles=None, planned=1, origin_m=(0.05 + i * 1e-3, 0.04))
            for i in (1, 2, 3)
        ]
        acquisitions, _ = _decisions(session(folders={"TileScan 1": positions}))
        assert len(acquisitions) == 1
        assert acquisitions[0].name == "TileScan 1/P1-P3"
        assert acquisitions[0].tile_count == 3
        assert acquisitions[0].stage_um.shape == (3, 2)

    def test_wavenumbers_and_single_wavenumber_scans(self) -> None:
        """Raman shift from pump and Stokes; a scan without a spectral axis uses the laser line."""
        root = session(
            element("ch", channels=3, timestamp=1),
            element("lipid", channels=None, pump=(795.0, 795.0), timestamp=2),
        )
        acquisitions, _ = _decisions(root)
        ch, lipid = acquisitions
        pump = np.linspace(781.0, 801.0, 3)
        np.testing.assert_allclose(ch.wavenumbers, 1e7 / pump - 1e7 / STOKES_NM)
        assert lipid.first.channels == 1
        np.testing.assert_allclose(lipid.wavenumbers, [1e7 / 795.0 - 1e7 / STOKES_NM])
        assert ch.first.pixel_um == (0.5, 0.5)
        np.testing.assert_allclose(
            ch.stage_um[1] - ch.stage_um[0], [3.8, 0.0], atol=1e-6
        )
