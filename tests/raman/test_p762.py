"""P762 session (local data): the acquisitions selected from a real LAS X session."""

import os

import pytest
from readlif.reader import LifFile

from focus.preprocessing.raman.inventory import read_elements, select

_LIF = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "test_data",
    "P762",
    "Raman",
    "20260916_P762.lif",
)


@pytest.mark.local_data
@pytest.mark.skipif(not os.path.exists(_LIF), reason="P762 LIF not available")
def test_p762_selection() -> None:
    """Complete, latest scans per window; aborted scans, merges and set-up frames are skipped."""
    lif = LifFile(_LIF)
    elements = read_elements(_LIF, lif.xml_root)
    assert [e.path for e in elements] == [i["name"] for i in lif.image_list]
    acquisitions, decisions = select(elements)
    assert [a.name for a in acquisitions] == [
        "R4 (2)",
        "R4 (4)",
        "R4 (8)",
        "R8",
        "R8 (2)",
    ]
    reasons = {d.path: d.reason for d in decisions}
    assert reasons["R4 (7)"].startswith("incomplete scan (70 of 91")
    assert reasons["R4 (6)"] == "superseded by the later 'R8 (2)'"
    assert reasons["TileScan 1/R4"] == "superseded by the later 'R4 (8)'"
    assert "merged" in reasons["3100-2800cm-1"]
    ch = acquisitions[1]
    assert ch.first.channels == 20 and round(float(ch.wavenumbers.max())) == 3100
