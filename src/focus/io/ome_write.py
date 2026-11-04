"""OME-TIFF writing shared by the image modalities.

Each written image is a tiled pyramid: level 0 is staged in zarr, every further level halves both
sides by 2x2 area averaging, and the levels are stored as SubIFDs of the level-0 pages. A file holds
one or several OME Images (one series each); the OME-XML in the first page describes all of them,
plus FOCUS key/value annotations.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast
import hashlib
import json
import os
import shutil

import dask.array as da
import numpy as np
import tifffile
import zarr
from ome_types.model import (
    OME,
    ROI,
    AnnotationRef,
    Channel,
    Image,
    Instrument,
    InstrumentRef,
    Map,
    MapAnnotation,
    Objective,
    ObjectiveSettings,
    Pixels,
    Polygon,
    ROIRef,
    StructuredAnnotations,
    TiffData,
)

from focus.io.provenance import (
    FOCUS_OME_NAMESPACE,
    PIXEL_DIGEST_KEY,
    SUPERSEDED_SIGNATURE_KEY,
    planes_digest,
    read_ome_annotations,
    stat_signature,
)

# Output tiles, and the size at which the pyramid stops (the coarsest level fits in one tile).
TILE = 512
# Namespace of the per-image MapAnnotations; the file-level FOCUS annotation keeps
# ``FOCUS_OME_NAMESPACE`` so ``read_ome_annotations`` returns only the file-level values.
FOCUS_IMAGE_NAMESPACE = "focus.image"
# Tiles compressed per batch by tifffile's thread pool; bounds the writer's memory.
_WRITE_BUFFER = 64 * 2**20

# Output sample type -> OME PixelType spelling.
OME_PIXEL_TYPE = {
    np.dtype(np.uint8): "uint8",
    np.dtype(np.uint16): "uint16",
    np.dtype(np.uint32): "uint32",
    np.dtype(np.int8): "int8",
    np.dtype(np.int16): "int16",
    np.dtype(np.int32): "int32",
    np.dtype(np.float32): "float",
    np.dtype(np.float64): "double",
}

Codec = tuple[str, dict[str, object], int | None]


@dataclass(frozen=True)
class ChannelInfo:
    """Acquisition metadata of one channel; every field is optional except the name."""

    name: str
    color: str | None = None  # "RRGGBB"
    excitation_nm: float | None = None
    emission_nm: float | None = None
    fluor: str | None = None
    illumination: str | None = None  # OME IlluminationType
    acquisition_mode: str | None = None  # OME AcquisitionMode
    contrast_method: str | None = None  # OME ContrastMethod


def ome_pixel_type(dtype: np.dtype) -> str:
    """OME PixelType of a sample type; ValueError for types OME cannot store (64-bit integers)."""
    try:
        return OME_PIXEL_TYPE[np.dtype(dtype)]
    except KeyError:
        raise ValueError(
            f"Unsupported sample type {np.dtype(dtype).name} for OME-TIFF"
        ) from None


# --- Pyramid staging and writing ---


def stage_levels(
    plane: da.Array,
    rgb: bool,
    workdir: str,
    channel_first: bool = False,
    prefix: str = "",
) -> list[zarr.Array[Any]]:
    """Write a level-0 array and its 2x2 area-averaged levels to zarr arrays in ``workdir``.

    ``plane`` is ``(Y, X, C)``, or ``(C, Y, X)`` when ``channel_first``. RGB levels are stored
    ``(Y, X, 3)``; other levels ``(C, Y, X)``, one plane per channel, the layout of the output pages.
    Each level halves both sides (floor) until the image fits in one tile. Integer levels round the
    average to the nearest value; floating levels average the finite samples only, so missing (NaN)
    samples do not erase their measured neighbours.
    """
    array = plane if (rgb or channel_first) else da.moveaxis(plane, -1, 0)
    level0 = _store(array, rgb, os.path.join(workdir, f"{prefix}0.zarr"))
    return levels_from(level0, rgb, workdir, prefix)


def levels_from(
    level0: zarr.Array[Any],
    rgb: bool,
    workdir: str,
    prefix: str = "",
    step: float | None = None,
) -> list[zarr.Array[Any]]:
    """Stage the 2x2 area-averaged levels of an already staged level 0 (see ``stage_levels``).

    ``step`` rounds floating levels to the grid of a quantised level 0, so they compress alike.
    """
    levels = [level0]
    floating = np.issubdtype(np.dtype(level0.dtype), np.floating)
    while max(_hw(levels[-1], rgb)) > TILE:
        previous = da.from_zarr(levels[-1])  # type: ignore[no-untyped-call]
        h, w = _hw(levels[-1], rgb)
        axes = (0, 1) if rgb else (1, 2)
        factors = {axes[0]: 2 if h > 1 else 1, axes[1]: 2 if w > 1 else 1}
        if floating:
            averaged = da.coarsen(
                _finite_mean, previous.astype(np.float32), factors, trim_excess=True
            )
        else:
            averaged = da.coarsen(
                np.mean, previous.astype(np.float32), factors, trim_excess=True
            )
        if np.issubdtype(previous.dtype, np.integer):
            averaged = da.rint(averaged)
        elif step is not None:
            averaged = da.rint(averaged / step) * step
        array = averaged.astype(previous.dtype)
        levels.append(
            _store(array, rgb, os.path.join(workdir, f"{prefix}{len(levels)}.zarr"))
        )
    return levels


def _finite_mean(block: np.ndarray, axis: tuple[int, ...] | None = None) -> np.ndarray:
    """Mean over ``axis`` of the finite samples; NaN where none is finite (no warning).

    ``axis`` defaults to None because dask probes the reduction once without it.
    """
    finite = np.isfinite(block)
    count = finite.sum(axis=axis)
    total = np.where(finite, block, 0).sum(axis=axis)
    with np.errstate(invalid="ignore", divide="ignore"):
        out: np.ndarray = np.where(count > 0, total / count, np.nan)
    return out


def _hw(level: zarr.Array[Any], rgb: bool) -> tuple[int, int]:
    return (level.shape[0], level.shape[1]) if rgb else (level.shape[1], level.shape[2])


def _store(array: da.Array, rgb: bool, path: str) -> zarr.Array[Any]:
    chunks = (TILE, TILE, array.shape[-1]) if rgb else (1, TILE, TILE)
    target = zarr.open_array(
        path, mode="w", shape=array.shape, chunks=chunks, dtype=array.dtype
    )
    da.store(array.rechunk(chunks), cast(Any, target), lock=False)  # type: ignore[no-untyped-call]
    return target


def level0_digest(level: zarr.Array[Any], rgb: bool) -> str:
    """``planes_digest`` of level 0 in page order, streamed in row bands of one tile height."""
    digest = hashlib.sha256()
    update_digest(digest, level, rgb)
    return digest.hexdigest()


def images_digest(images: Sequence[StagedImage]) -> str:
    """``planes_digest`` of every image's level 0, in file page order."""
    digest = hashlib.sha256()
    for image in images:
        update_digest(digest, image.levels[0], image.rgb)
    return digest.hexdigest()


def update_digest(digest: Any, level: zarr.Array[Any], rgb: bool) -> None:
    """Feed level 0's planes to a running sha256, exactly as ``level0_digest`` hashes them."""
    h, w = _hw(level, rgb)
    shape = [h, w, level.shape[2]] if rgb else [h, w]
    for c in range(1 if rgb else level.shape[0]):
        digest.update(json.dumps([np.dtype(level.dtype).str, shape]).encode())
        for y in range(0, h, TILE):
            band = level[y : y + TILE] if rgb else level[c, y : y + TILE]
            digest.update(np.ascontiguousarray(band).data)


def _tiles(level: zarr.Array[Any], rgb: bool) -> Iterator[np.ndarray]:
    """Tiles of every page of ``level`` in TIFF order (page, then row-major tiles)."""
    t = TILE
    h, w = _hw(level, rgb)
    for c in range(1 if rgb else level.shape[0]):
        for y in range(0, h, t):
            for x in range(0, w, t):
                yield np.asarray(
                    level[y : y + t, x : x + t]
                    if rgb
                    else level[c, y : y + t, x : x + t]
                )


@dataclass(frozen=True)
class StagedImage:
    """The staged levels of one OME Image and its ``[x, y]`` level-0 pixel size in µm."""

    levels: list[zarr.Array[Any]]
    rgb: bool
    pixel_size: np.ndarray | None


def write_pyramid(
    levels: list[zarr.Array[Any]],
    path: str,
    rgb: bool,
    description: str,
    codec: Codec,
    pixel_size: np.ndarray | None,
) -> None:
    """Write the staged levels as one OME-TIFF series: level-0 pages with SubIFD levels."""
    write_images(path, [StagedImage(levels, rgb, pixel_size)], description, codec)


def write_images(
    path: str, images: Sequence[StagedImage], description: str, codec: Codec
) -> None:
    """Write several staged images as consecutive series; the OME-XML goes in the first page."""
    with ImagesWriter(path, codec) as writer:
        for index, image in enumerate(images):
            writer.write(image, description if index == 0 else None)


class ImagesWriter:
    """Append staged images to one OME-TIFF, one series each, as they become ready.

    The first image carries the OME-XML; a description that is only complete once every image is
    written can be replaced afterwards with ``replace_description``.
    """

    def __init__(self, path: str, codec: Codec) -> None:
        """Open ``path`` for writing a BigTIFF with the given tile codec."""
        self._tif = tifffile.TiffWriter(path, bigtiff=True)
        self._codec = codec

    def __enter__(self) -> ImagesWriter:
        """Return the open writer."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the file."""
        self.close()

    def close(self) -> None:
        """Finish the TIFF file."""
        self._tif.close()

    def write(self, image: StagedImage, description: str | None) -> None:
        """Write one image: level-0 pages, then its SubIFD levels."""
        compression, args, predictor = self._codec
        h0, w0 = _hw(image.levels[0], image.rgb)
        for i, level in enumerate(image.levels):
            h, w = _hw(level, image.rgb)
            options: dict[str, Any] = dict(
                shape=level.shape,
                dtype=level.dtype,
                tile=(TILE, TILE),
                photometric="rgb" if image.rgb else "minisblack",
                compression=compression,
                compressionargs=args,
                predictor=predictor,
                metadata=None,
                buffersize=_WRITE_BUFFER,
            )
            if image.pixel_size is not None:
                # TIFF resolution tags in pixels per centimetre, so non-OME readers keep the scale.
                options["resolution"] = (
                    1e4 / (image.pixel_size[0] * w0 / w),
                    1e4 / (image.pixel_size[1] * h0 / h),
                )
                options["resolutionunit"] = "CENTIMETER"
            if i == 0:
                if description is not None:
                    # UTF-8 bytes, as tifffile's own OME writer stores them: Bio-Formats does not
                    # resolve XML character references (the µ of µm) in the TIFF description.
                    options["description"] = description.encode("utf-8")
                self._tif.write(
                    _tiles(level, image.rgb), subifds=len(image.levels) - 1, **options
                )
            else:
                self._tif.write(_tiles(level, image.rgb), subfiletype=1, **options)


def replace_description(path: str, description: str) -> None:
    """Replace the OME-XML of the first page (written at the end of the file when longer)."""
    tifffile.tiffcomment(path, description.encode("utf-8"))


def remove_workdir(workdir: str) -> None:
    """Delete the zarr staging directory."""
    shutil.rmtree(workdir, ignore_errors=True)


def previous_output(output_file: str) -> tuple[str, list[str | int] | None] | None:
    """``(pixel digest, superseded signature)`` of an existing output, or None when there is none.

    An output written before FOCUS stored provenance carries no digest: its level-0 pixels are
    digested here, and the file itself (its stat signature) is what a pixel-identical regeneration
    supersedes. An annotated output passes its own superseded signature on.
    """
    if not os.path.exists(output_file):
        return None
    annotations = read_ome_annotations(output_file) or {}
    if PIXEL_DIGEST_KEY in annotations:
        superseded = annotations.get(SUPERSEDED_SIGNATURE_KEY)
        return annotations[PIXEL_DIGEST_KEY], (
            json.loads(superseded) if superseded else None
        )
    try:
        return planes_digest(level0_planes(output_file)), stat_signature(output_file)
    except Exception:
        return None


def level0_planes(path: str) -> list[np.ndarray]:
    """Level-0 planes of an output in page order: the leading pages that share the first size."""
    planes = []
    with tifffile.TiffFile(path) as tif:
        size = tif.pages[0].shape[:2]
        for page in tif.pages:
            if page.shape[:2] != size:
                break
            planes.append(page.asarray())
    return planes


# --- OME-XML ---


def _rgba(colour: str | None) -> int | None:
    """``"RRGGBB"`` as the signed 32-bit RGBA integer OME stores."""
    if not colour:
        return None
    value = (int(colour, 16) << 8) | 0xFF
    return value - (1 << 32) if value >= 1 << 31 else value


def _channel(
    image: int, index: int, channel: ChannelInfo, colour: str | None, samples: int
) -> Channel:
    """OME Channel; enumeration values OME does not define are left out instead of failing."""
    fields: dict[str, Any] = {
        "id": f"Channel:{image}:{index}",
        "name": channel.name,
        "samples_per_pixel": samples,
        "fluor": channel.fluor,
    }
    rgba = _rgba(colour)
    if rgba is not None:
        fields["color"] = rgba
    for key, wavelength in (
        ("excitation_wavelength", channel.excitation_nm),
        ("emission_wavelength", channel.emission_nm),
    ):
        if wavelength:
            fields[key] = float(wavelength)
            fields[f"{key}_unit"] = "nm"
    for key, value in (
        ("illumination_type", channel.illumination),
        ("acquisition_mode", channel.acquisition_mode),
        ("contrast_method", channel.contrast_method),
    ):
        if value and _valid(key, value):
            fields[key] = value
    return Channel.model_validate({k: v for k, v in fields.items() if v is not None})


def _valid(key: str, value: str) -> bool:
    """True when ``value`` is an OME enumeration value of the Channel field ``key``."""
    try:
        Channel.model_validate({"id": "Channel:0:0", key: value})
    except ValueError:
        return False
    return True


def _roi(index: int, contours: Sequence[np.ndarray]) -> ROI | None:
    """One ``tissue`` ROI whose polygons outline the detected tissue in level-0 pixels."""
    if not contours:
        return None
    shapes = [
        Polygon(
            id=f"Shape:{index}:{i}",
            text="tissue",
            points=" ".join(f"{x:g},{y:g}" for x, y in contour),
        )
        for i, contour in enumerate(contours)
    ]
    return ROI.model_validate({"id": f"ROI:{index}", "name": "tissue", "union": shapes})


@dataclass(frozen=True)
class OmeImage:
    """What the OME-XML describes for one image: its level 0 and its acquisition metadata.

    ``annotations`` are the image's own FOCUS key/value pairs (namespace ``focus.image``).
    """

    name: str
    shape_yx: tuple[int, int]
    dtype: np.dtype
    rgb: bool
    channels: Sequence[ChannelInfo]
    colours: Sequence[str]
    pixel_size: np.ndarray | None
    significant_bits: int | None
    objective: tuple[float | None, float | None]
    contours: Sequence[np.ndarray]
    annotations: Mapping[str, object] = field(default_factory=dict)

    @property
    def pages(self) -> int:
        """Top-level TIFF pages of level 0: one interleaved RGB page, else one per channel."""
        return 1 if self.rgb else len(self.channels)


def _planes(
    index: int, image: OmeImage, first_ifd: int
) -> tuple[list[Channel], list[TiffData]]:
    """Channels and page mapping: one interleaved RGB plane, or one page per channel."""
    if image.rgb:
        acquisition = replace(
            image.channels[0],
            name="RGB",
            color=None,
            excitation_nm=None,
            emission_nm=None,
            fluor=None,
        )
        return [_channel(index, 0, acquisition, None, 3)], [
            TiffData(ifd=first_ifd, plane_count=1)
        ]
    channels = [
        _channel(index, i, c, image.colours[i], 1) for i, c in enumerate(image.channels)
    ]
    return channels, [
        TiffData(ifd=first_ifd + c, first_c=c, plane_count=1)
        for c in range(len(channels))
    ]


def _pixels(index: int, image: OmeImage, first_ifd: int) -> Pixels:
    """Pixels element of one image's level 0."""
    h, w = image.shape_yx
    channels, tiff_data = _planes(index, image, first_ifd)
    fields: dict[str, Any] = dict(
        id=f"Pixels:{index}",
        dimension_order="XYCZT",
        type=ome_pixel_type(image.dtype),
        size_x=w,
        size_y=h,
        size_z=1,
        size_c=3 if image.rgb else len(channels),
        size_t=1,
        interleaved=image.rgb,
        channels=channels,
        tiff_data_blocks=tiff_data,
    )
    if image.significant_bits:
        fields["significant_bits"] = int(image.significant_bits)
    if image.pixel_size is not None:
        fields.update(
            physical_size_x=float(image.pixel_size[0]),
            physical_size_x_unit="µm",
            physical_size_y=float(image.pixel_size[1]),
            physical_size_y_unit="µm",
        )
    return Pixels(**fields)


def _map_annotation(
    identifier: str, namespace: str, values: Mapping[str, object]
) -> MapAnnotation:
    entries = [{"k": k, "value": str(v)} for k, v in values.items()]
    return MapAnnotation(
        id=identifier, namespace=namespace, value=cast(Map, {"ms": entries})
    )


def build_ome_xml(
    images: OmeImage | Sequence[OmeImage], annotations: Mapping[str, object]
) -> str:
    """OME-XML for one or several images (pyramid levels are SubIFDs of their planes).

    ``annotations`` is the file-level FOCUS MapAnnotation (``Annotation:0``). Images are written in
    order, so image ``i``'s planes start at the top-level IFD after all planes of the images before.
    """
    if isinstance(images, OmeImage):
        images = [images]
    ome = OME()
    instruments: list[Instrument] = []
    rois: list[ROI] = []
    map_annotations = [
        _map_annotation("Annotation:0", FOCUS_OME_NAMESPACE, annotations)
    ]
    first_ifd = 0
    built = []
    for index, image in enumerate(images):
        fields: dict[str, Any] = dict(
            id=f"Image:{index}",
            name=image.name,
            pixels=_pixels(index, image, first_ifd),
        )
        first_ifd += image.pages
        magnification, na = image.objective
        if magnification or na:
            objective = Objective(
                id=f"Objective:{index}",
                nominal_magnification=magnification,
                lens_na=na,
            )
            instruments.append(
                Instrument(id=f"Instrument:{index}", objectives=[objective])
            )
            fields.update(
                instrument_ref=InstrumentRef(id=f"Instrument:{index}"),
                objective_settings=ObjectiveSettings(id=f"Objective:{index}"),
            )
        roi = _roi(index, image.contours)
        if roi is not None:
            rois.append(roi)
            fields["roi_refs"] = [ROIRef(id=f"ROI:{index}")]
        if image.annotations:
            identifier = f"Annotation:{len(map_annotations)}"
            map_annotations.append(
                _map_annotation(identifier, FOCUS_IMAGE_NAMESPACE, image.annotations)
            )
            fields["annotation_refs"] = [AnnotationRef(id=identifier)]
        built.append(Image(**fields))
    if instruments:
        ome.instruments = instruments
    if rois:
        ome.rois = rois
    ome.images = built
    ome.structured_annotations = StructuredAnnotations(map_annotations=map_annotations)
    return ome.to_xml()
