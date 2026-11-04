"""Tile stacks of a LIF acquisition, read straight from the file's memory blocks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from readlif.reader import LifFile

from focus.preprocessing.raman.inventory import Acquisition, LifElement


@dataclass
class TileStack:
    """The ``(tile, channel, y, x)`` samples of one acquisition, memory-mapped from the LIF.

    Channels are ordered by increasing wavenumber (Leica scans the pump upwards, so the stored order
    is reversed); ``order`` maps output channel ``c`` to stored channel ``order[c]``. Values are the
    native samples; nothing is rescaled.
    """

    blocks: list[np.ndarray]
    order: np.ndarray
    wavenumbers: np.ndarray
    pump_nm: np.ndarray
    saturation: int

    @property
    def tiles(self) -> int:
        """Number of tiles over all elements."""
        return sum(b.shape[0] for b in self.blocks)

    @property
    def channels(self) -> int:
        """Number of spectral channels."""
        return int(self.order.size)

    @property
    def tile_shape(self) -> tuple[int, int]:
        """``(rows, columns)`` of one tile."""
        return int(self.blocks[0].shape[2]), int(self.blocks[0].shape[3])

    def _locate(self, tile: int) -> tuple[np.ndarray, int]:
        for block in self.blocks:
            if tile < block.shape[0]:
                return block, tile
            tile -= block.shape[0]
        raise IndexError(tile)

    def tile(self, tile: int) -> np.ndarray:
        """``(C, Y, X)`` native samples of one tile, channels by increasing wavenumber."""
        block, local = self._locate(tile)
        return np.asarray(block[local][self.order])

    def plane(self, tile: int, channel: int) -> np.ndarray:
        """``(Y, X)`` native samples of one tile and output channel."""
        block, local = self._locate(tile)
        return np.asarray(block[local, int(self.order[channel])])


def _memory_view(element: LifElement, offset: int) -> np.ndarray:
    """Strided ``(T, C, Y, X)`` view of an element's memory block."""
    dtype = np.dtype(np.uint8 if element.bytes_per_sample == 1 else "<u2")
    flat = np.memmap(
        element.lif,
        dtype=np.uint8,
        mode="r",
        offset=offset,
        shape=(element.required_bytes,),
    )
    shape = (element.tiles, element.channels, element.size_y, element.size_x)
    strides = tuple(
        s if s else 0 for s in element.strides
    )  # a missing dimension has size 1, its stride is irrelevant
    return np.lib.stride_tricks.as_strided(
        flat.view(dtype) if dtype.itemsize == 1 else np.frombuffer(flat, dtype=dtype),
        shape=shape,
        strides=strides,
        writeable=False,
    )


def open_stack(acquisition: Acquisition, lif: LifFile) -> TileStack:
    """Memory-map every element of an acquisition (all from the same LIF file)."""
    blocks = []
    for element in acquisition.elements:
        offset, length = lif.get_image(element.index).offsets[:2]
        if length < element.required_bytes:
            raise ValueError(
                f"'{element.path}': memory block holds {length} bytes, {element.required_bytes} expected"
            )
        blocks.append(_memory_view(element, int(offset)))
    first = acquisition.first
    wavenumbers = first.wavenumbers
    order = np.argsort(wavenumbers, kind="stable")
    return TileStack(
        blocks=blocks,
        order=order,
        wavenumbers=wavenumbers[order],
        pump_nm=first.pump_nm[order],
        saturation=(1 << first.bits) - 1,
    )
