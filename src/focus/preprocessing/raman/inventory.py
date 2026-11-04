"""Inventory of a Leica LIF session: which elements are SRS acquisitions FOCUS stitches.

A LAS X session file records everything acquired while setting up and imaging a slide: tile scans,
Mark & Find positions, single snapshots, aborted scans and the mosaics LAS X merged itself. The
inventory reads only the XML header, describes every image element, and selects the acquisitions
to process with fixed rules (``select``). Every decision is returned with its reason so the
activity log can state it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

import numpy as np

# Leica dimension identifiers (LAS X DimensionDescription@DimID).
_DIM_X, _DIM_Y, _DIM_SPECTRAL, _DIM_TILES = 1, 2, 9, 10
_LENGTH_TO_UM = {"m": 1e6, "mm": 1e3, "um": 1.0, "µm": 1.0}
# Wavelength range comparison tolerance (nm) when matching spectral windows.
_WINDOW_TOLERANCE_NM = 0.05


@dataclass(frozen=True)
class LifElement:
    """One image element of a LIF file, described from its XML only.

    ``strides`` are the byte increments of the ``(tile, spectral, y, x)`` axes of its memory block.
    Stage positions are tile origins in µm, ``(x, y)``. Wavelengths are the tunable (pump) beam,
    one per channel, in acquisition order; ``stokes_nm`` is the fixed beam.
    """

    index: int
    path: str
    lif: str
    size_x: int
    size_y: int
    pixel_um: tuple[float, float]
    channels: int
    tiles: int
    strides: tuple[int, int, int, int]
    bits: int
    memory_bytes: int
    planned_tiles: int
    stage_um: np.ndarray
    has_z: bool
    pump_nm: np.ndarray
    stokes_nm: float | None
    gain: float | None
    line_average: int | None
    laser_power_w: float | None
    timestamp: int | None
    parent: str

    @property
    def name(self) -> str:
        """Element name without its folder path."""
        return self.path.rsplit("/", 1)[-1]

    @property
    def bytes_per_sample(self) -> int:
        """Sample size in bytes (8-bit or 16-bit LIF data)."""
        return 1 if self.bits <= 8 else 2

    @property
    def required_bytes(self) -> int:
        """Bytes of a complete memory block."""
        return (
            self.tiles
            * self.channels
            * self.size_y
            * self.size_x
            * self.bytes_per_sample
        )

    @property
    def wavenumbers(self) -> np.ndarray:
        """Raman shift per channel, cm-1: ``1e7 / pump - 1e7 / stokes``."""
        if self.stokes_nm is None:
            return np.full(self.channels, np.nan)
        return 1e7 / self.pump_nm - 1e7 / self.stokes_nm


@dataclass
class Acquisition:
    """An SRS acquisition FOCUS processes: one tile scan, or a group of Mark & Find positions."""

    name: str
    elements: list[LifElement]
    superseded: list[str] = field(default_factory=list)

    @property
    def first(self) -> LifElement:
        """The element whose settings describe the acquisition."""
        return self.elements[0]

    @property
    def tile_count(self) -> int:
        """Tiles over all elements."""
        return sum(e.tiles for e in self.elements)

    @property
    def stage_um(self) -> np.ndarray:
        """``(T, 2)`` tile origins in µm over all elements, in element order."""
        return np.concatenate([e.stage_um[: e.tiles] for e in self.elements])

    @property
    def wavenumbers(self) -> np.ndarray:
        """Raman shift per stored channel, cm-1."""
        return self.first.wavenumbers

    @property
    def timestamp(self) -> int:
        """Acquisition start (Windows FILETIME), or the element order when absent."""
        stamps = [e.timestamp for e in self.elements if e.timestamp is not None]
        return min(stamps) if stamps else self.first.index

    def footprint_um(self) -> tuple[float, float, float, float]:
        """``(x0, y0, x1, y1)`` stage box covered by the tiles, µm."""
        first = self.first
        stage = self.stage_um
        w = first.size_x * first.pixel_um[0]
        h = first.size_y * first.pixel_um[1]
        return (
            float(stage[:, 0].min()),
            float(stage[:, 1].min()),
            float(stage[:, 0].max() + w),
            float(stage[:, 1].max() + h),
        )

    def window(self) -> tuple[float, float, int]:
        """Spectral window identity: first and last pump wavelength and channel count."""
        pump = self.first.pump_nm
        return float(pump[0]), float(pump[-1]), int(self.first.channels)


@dataclass(frozen=True)
class Decision:
    """One element's fate in the inventory."""

    path: str
    kept: bool
    reason: str


# --- XML parsing ---


def _elements(root: ET.Element) -> Iterator[tuple[str, ET.Element]]:
    """Image elements in readlif's order (depth first), with their folder path."""

    def walk(node: ET.Element, path: str) -> Iterator[tuple[str, ET.Element]]:
        children = node.findall("./Children/Element")
        if not children:
            children = node.findall("./Element")
        for item in children:
            name = item.get("Name", "")
            appended = name if path == "" else f"{path}/{name}"
            if item.find("./Data/Image") is not None:
                # readlif drops the top-level (file) element from the name.
                yield "/".join(appended.split("/")[1:]), item
            if item.findall("./Children/Element/Data"):
                yield from walk(item, appended)

    yield from walk(root, "")


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _hardware(item: ET.Element) -> ET.Element | None:
    for path in (
        './Data/Image/Attachment[@Name="HardwareSetting"]/ATLConfocalSettingDefinition',
        './Data/Image/Attachment[@Name="HardwareSetting"]//ATLConfocalSettingDefinition',
    ):
        found = item.find(path)
        if found is not None:
            return found
    return None


def _crs_laser(settings: ET.Element | None) -> ET.Element | None:
    """The coherent Raman laser: the ``Laser`` carrying the fixed-beam ``PumpWavelength``."""
    if settings is None:
        return None
    return next(
        (
            laser
            for laser in settings.findall(".//LaserArray/Laser")
            if laser.get("PumpWavelength")
        ),
        None,
    )


def parse_element(index: int, path: str, item: ET.Element, lif: str) -> LifElement:
    """Describe one image element from its XML."""
    image = item.find("./Data/Image")
    assert image is not None
    dims = {
        int(d.get("DimID", "0")): d
        for d in image.findall("./ImageDescription/Dimensions/DimensionDescription")
    }

    def size(dim: int) -> int:
        return int(dims[dim].get("NumberOfElements", "1")) if dim in dims else 1

    def stride(dim: int) -> int:
        return int(dims[dim].get("BytesInc", "0")) if dim in dims else 0

    def pixel(dim: int) -> float:
        d = dims.get(dim)
        if d is None:
            return float("nan")
        unit = _LENGTH_TO_UM.get(d.get("Unit", "m"), 1e6)
        n = int(d.get("NumberOfElements", "1"))
        return float(d.get("Length", "0")) * unit / max(n, 1)

    channel = image.find("./ImageDescription/Channels/ChannelDescription")
    bits = int(channel.get("Resolution", "8")) if channel is not None else 8
    unit = (
        _LENGTH_TO_UM.get(dims[_DIM_X].get("Unit", "m"), 1e6) if _DIM_X in dims else 1e6
    )
    tiles = image.findall('./Attachment[@Name="TileScanInfo"]/Tile')
    stage = (
        np.array(
            [[float(t.get("PosX", "0")), float(t.get("PosY", "0"))] for t in tiles],
            dtype=np.float64,
        ).reshape(-1, 2)
        * unit
    )
    settings = _hardware(item)
    laser = _crs_laser(settings)
    channels = size(_DIM_SPECTRAL)
    lam = (
        settings.find("LambdaDefinition/LambdaExcitation")
        if settings is not None
        else None
    )
    begin = end = None
    if lam is not None:
        begin = _float(
            lam.get("LambdaExcitationBeginDouble", lam.get("LambdaExcitationBegin"))
        )
        end = _float(
            lam.get("LambdaExcitationEndDouble", lam.get("LambdaExcitationEnd"))
        )
    if _DIM_SPECTRAL in dims and begin and end:
        pump = np.linspace(begin, end, channels, dtype=np.float64)
    else:
        tuned = _float(laser.get("Wavelength")) if laser is not None else None
        pump = np.full(channels, tuned if tuned else np.nan, dtype=np.float64)
    detector = (
        next(
            (
                d
                for d in settings.findall(".//DetectorList/Detector")
                if d.get("IsActive") == "1"
            ),
            None,
        )
        if settings is not None
        else None
    )
    stamps = image.find("./TimeStampList")
    timestamp = None
    if stamps is not None and stamps.text and stamps.text.split():
        try:
            timestamp = int(stamps.text.split()[0], 16)
        except ValueError:
            timestamp = None
    memory = item.find("./Memory")
    line_average = settings.get("LineAverage") if settings is not None else None
    return LifElement(
        index=index,
        path=path,
        lif=lif,
        size_x=size(_DIM_X),
        size_y=size(_DIM_Y),
        pixel_um=(pixel(_DIM_X), pixel(_DIM_Y)),
        channels=channels,
        tiles=size(_DIM_TILES),
        strides=(
            stride(_DIM_TILES),
            stride(_DIM_SPECTRAL),
            stride(_DIM_Y),
            stride(_DIM_X),
        ),
        bits=bits,
        memory_bytes=int(memory.get("Size", "0")) if memory is not None else 0,
        planned_tiles=len(tiles),
        stage_um=stage,
        has_z=bool(tiles) and all(t.get("PosZ") is not None for t in tiles),
        pump_nm=pump,
        stokes_nm=_float(laser.get("PumpWavelength")) if laser is not None else None,
        gain=_float(detector.get("Gain")) if detector is not None else None,
        line_average=int(line_average)
        if line_average and line_average.isdigit()
        else None,
        laser_power_w=_float(laser.get("PumpOutputPower"))
        if laser is not None
        else None,
        timestamp=timestamp,
        parent=path.rsplit("/", 1)[0] if "/" in path else "",
    )


def read_elements(lif_path: str, xml_root: ET.Element) -> list[LifElement]:
    """Every image element of one LIF file, in readlif's image order."""
    return [
        parse_element(index, path, item, lif_path)
        for index, (path, item) in enumerate(_elements(xml_root))
    ]


# --- Selection ---


def _classify(element: LifElement) -> str | None:
    """Why an element is not an acquisition (None when it is one)."""
    if element.stokes_nm is None or not np.all(np.isfinite(element.pump_nm)):
        return "no coherent Raman laser settings"
    if element.planned_tiles == 0:
        return "single frame without a stage position"
    if element.planned_tiles == 1 and element.tiles == 1 and not element.has_z:
        return "image without a Z stage position (LAS X merged mosaic or snapshot)"
    if (
        element.tiles != element.planned_tiles
        or element.memory_bytes < element.required_bytes
    ):
        stored = element.memory_bytes // max(
            element.channels
            * element.size_y
            * element.size_x
            * element.bytes_per_sample,
            1,
        )
        return f"incomplete scan ({stored} of {element.planned_tiles} tiles stored)"
    if not all(np.isfinite(element.pixel_um)) or min(element.pixel_um) <= 0:
        return "no pixel size"
    if element.tiles == 1 and element.parent == "":
        return "single snapshot outside a tile scan job"
    return None


def _settings_key(element: LifElement) -> tuple[object, ...]:
    """Acquisition settings that Mark & Find positions of one job share."""
    return (
        element.lif,
        element.parent,
        element.size_x,
        element.size_y,
        round(element.pixel_um[0], 6),
        round(element.pixel_um[1], 6),
        element.channels,
        tuple(np.round(element.pump_nm, 3)),
        element.gain,
        element.line_average,
        element.bits,
    )


def _overlaps(a: Acquisition, b: Acquisition) -> bool:
    ax0, ay0, ax1, ay1 = a.footprint_um()
    bx0, by0, bx1, by1 = b.footprint_um()
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def _same_window(a: Acquisition, b: Acquisition) -> bool:
    a0, a1, an = a.window()
    b0, b1, bn = b.window()
    return (
        an == bn
        and abs(a0 - b0) <= _WINDOW_TOLERANCE_NM
        and abs(a1 - b1) <= _WINDOW_TOLERANCE_NM
    )


def select(elements: Sequence[LifElement]) -> tuple[list[Acquisition], list[Decision]]:
    """Acquisitions to process and the decision taken on every element.

    Rules: skip elements without coherent Raman settings, frames without stage positions, LAS X
    merged mosaics (one stage tile without a Z position), incomplete scans and snapshots outside a
    job. Single-tile positions of one job with identical settings form one acquisition. When two
    acquisitions measure the same spectral window (same pump range and channel count) over
    overlapping areas, the latest supersedes the earlier one.
    """
    decisions: dict[str, Decision] = {}
    tile_scans: list[Acquisition] = []
    positions: dict[tuple[object, ...], Acquisition] = {}
    for element in elements:
        reason = _classify(element)
        if reason is not None:
            decisions[element.path] = Decision(element.path, False, reason)
            continue
        if element.tiles > 1:
            tile_scans.append(Acquisition(element.path, [element]))
            continue
        key = _settings_key(element)
        if key in positions:
            positions[key].elements.append(element)
        else:
            positions[key] = Acquisition(element.path, [element])
    for group in positions.values():
        if len(group.elements) > 1:
            group.name = (
                f"{group.first.parent}/{group.first.name}-{group.elements[-1].name}"
            )
    candidates = sorted(
        tile_scans + list(positions.values()), key=lambda a: a.timestamp
    )
    kept: list[Acquisition] = []
    for acquisition in reversed(candidates):  # latest first
        newer = next(
            (
                k
                for k in kept
                if _same_window(k, acquisition) and _overlaps(k, acquisition)
            ),
            None,
        )
        if newer is not None:
            newer.superseded.append(acquisition.name)
            for element in acquisition.elements:
                decisions[element.path] = Decision(
                    element.path, False, f"superseded by the later '{newer.name}'"
                )
            continue
        kept.append(acquisition)
    kept.sort(key=lambda a: (a.timestamp, a.first.index))
    for acquisition in kept:
        for element in acquisition.elements:
            decisions[element.path] = Decision(element.path, True, acquisition.name)
    ordered = [decisions[e.path] for e in elements]
    return kept, ordered
