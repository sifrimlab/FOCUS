"""Format readers: CZI through pylibCZIrw and the ND2 metadata mapping."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from focus.preprocessing.microscopy.nd2_source import _channels, _hint
from focus.preprocessing.microscopy.source import open_source


def test_czi_channels_planes_scaling_and_scene(tmp_path: Path) -> None:
    """A two-channel, three-plane CZI reads lazily with names, pixel size and exact planes."""
    pyczi: Any = pytest.importorskip("pylibCZIrw.czi")
    path = str(tmp_path / "x.czi")
    rng = np.random.default_rng(0)
    planes = {
        (c, z): rng.integers(0, 4096, (300, 400)).astype(np.uint16)
        for c in range(2)
        for z in range(3)
    }
    with pyczi.create_czi(path, exist_ok=True) as writer:
        for (c, z), data in planes.items():
            writer.write(data=data, plane={"C": c, "Z": z}, location=(10, 20))
        writer.write_metadata(
            document_name="x",
            channel_names={0: "DAPI", 1: "CD45"},
            scale_x=0.325e-6,
            scale_y=0.325e-6,
        )
    with open_source(path) as source:
        assert source.format == "czi" and not source.rgb
        assert source.data.shape == (1, 3, 300, 400, 2)
        assert [c.name for c in source.channels] == ["DAPI", "CD45"]
        np.testing.assert_allclose(source.pixel_size, [0.325, 0.325])  # type: ignore[arg-type]
        np.testing.assert_array_equal(
            source.data[0, 2, :, :, 1].compute(), planes[(1, 2)]
        )
        np.testing.assert_array_equal(
            source.data[0, 1, 50:60, 70:90, 0].compute(), planes[(0, 1)][50:60, 70:90]
        )


def test_nd2_channel_metadata_and_modality() -> None:
    """ND2 channel names, colours and wavelengths map to ChannelInfo; modality flags state the type."""
    meta = SimpleNamespace(
        name="FITC",
        color=SimpleNamespace(r=0, g=255, b=0),
        emissionLambdaNm=520.0,
        excitationLambdaNm=488.0,
    )
    microscope = SimpleNamespace(modalityFlags=["fluorescence", "camera"])
    channels = _channels(
        [SimpleNamespace(channel=meta, microscope=microscope)], 1, False
    )
    assert channels[0].name == "FITC" and channels[0].color == "00FF00"
    assert channels[0].emission_nm == 520.0
    assert _hint(channels, microscope) == (
        "fluorescence",
        "emission wavelength or fluorophore in the channel metadata",
    )
    plain = _channels([], 2, False)
    assert [c.name for c in plain] == ["Channel 0", "Channel 1"]
    hint = _hint(plain, SimpleNamespace(modalityFlags=["brightfield"]))
    assert hint is not None and hint[0] == "brightfield"
