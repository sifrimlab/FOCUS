"""Synthetic LAS X session XML for inventory tests (no pixel data)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

STOKES_NM = 1032.0


def element(
    name: str,
    *,
    channels: int | None = 3,
    tiles: int | None = 4,
    planned: int | None = 4,
    pump: tuple[float, float] = (781.0, 801.0),
    origin_m: tuple[float, float] = (0.05, 0.04),
    has_z: bool = True,
    stored_tiles: int | None = None,
    timestamp: int = 1,
    size: int = 8,
    gain: float = 10.0,
) -> ET.Element:
    """One image element: 8 x 8 px tiles of 0.5 µm, a 2 x 2 tile grid with 5 % overlap."""
    item = ET.Element("Element", Name=name)
    data = ET.SubElement(item, "Data")
    image = ET.SubElement(data, "Image")
    description = ET.SubElement(image, "ImageDescription")
    ET.SubElement(
        ET.SubElement(description, "Channels"), "ChannelDescription", Resolution="8"
    )
    dims = ET.SubElement(description, "Dimensions")
    length = str(size * 0.5e-6)
    ET.SubElement(
        dims,
        "DimensionDescription",
        DimID="1",
        NumberOfElements=str(size),
        Length=length,
        Unit="m",
        BytesInc="1",
    )
    ET.SubElement(
        dims,
        "DimensionDescription",
        DimID="2",
        NumberOfElements=str(size),
        Length=length,
        Unit="m",
        BytesInc=str(size),
    )
    plane = size * size
    n_channels = channels or 1
    if channels is not None:
        ET.SubElement(
            dims,
            "DimensionDescription",
            DimID="9",
            NumberOfElements=str(channels),
            Length="1e-8",
            Unit="m",
            BytesInc=str(plane),
        )
    stored = tiles if stored_tiles is None else stored_tiles
    if tiles is not None:
        ET.SubElement(
            dims,
            "DimensionDescription",
            DimID="10",
            NumberOfElements=str(stored),
            Length="0",
            Unit="",
            BytesInc=str(plane * n_channels),
        )
    hardware = ET.SubElement(image, "Attachment", Name="HardwareSetting")
    settings = ET.SubElement(hardware, "ATLConfocalSettingDefinition", LineAverage="2")
    lam = ET.SubElement(ET.SubElement(settings, "LambdaDefinition"), "LambdaExcitation")
    lam.set("LambdaExcitationBeginDouble", str(pump[0]))
    lam.set("LambdaExcitationEndDouble", str(pump[1]))
    lasers = ET.SubElement(settings, "LaserArray")
    ET.SubElement(
        lasers,
        "Laser",
        LaserName="CRS",
        PumpWavelength=str(STOKES_NM),
        Wavelength=str(pump[0]),
        PumpOutputPower="0.3",
    )
    detectors = ET.SubElement(settings, "DetectorList")
    ET.SubElement(detectors, "Detector", Name="F-SRS", IsActive="1", Gain=str(gain))
    if planned:
        info = ET.SubElement(image, "Attachment", Name="TileScanInfo")
        step = size * 0.5e-6 * 0.95
        for t in range(planned):
            tile = ET.SubElement(info, "Tile", FieldX=str(t % 2), FieldY=str(t // 2))
            tile.set("PosX", str(origin_m[0] + (t % 2) * step))
            tile.set("PosY", str(origin_m[1] + (t // 2) * step))
            if has_z:
                tile.set("PosZ", "0.001")
    stamps = ET.SubElement(image, "TimeStampList", NumberOfTimeStamps="1")
    stamps.text = format(timestamp, "x")
    ET.SubElement(item, "Memory", Size=str(plane * n_channels * (stored or 1)))
    return item


def session(
    *children: ET.Element, folders: dict[str, list[ET.Element]] | None = None
) -> ET.Element:
    """A LIF header whose top element holds ``children`` and job folders."""
    root = ET.Element("LMSDataContainerHeader")
    top = ET.SubElement(root, "Element", Name="session.lif")
    container = ET.SubElement(top, "Children")
    for child in children:
        container.append(child)
    for name, items in (folders or {}).items():
        folder = ET.SubElement(container, "Element", Name=name)
        ET.SubElement(folder, "Data")
        ET.SubElement(folder, "Memory", Size="0")
        sub = ET.SubElement(folder, "Children")
        for item in items:
            sub.append(item)
    return root
