"""Content fingerprints for preprocessing and its downstream artifacts."""

from typing import cast

from os import PathLike
from collections.abc import Iterable, Mapping
from numpy.typing import ArrayLike
import hashlib
import json
import os
import xml.etree.ElementTree as ET

import h5py
import numpy as np

# Namespace of the OME MapAnnotation that carries FOCUS provenance inside an OME-TIFF.
FOCUS_OME_NAMESPACE = "focus"
PREPROCESSING_FINGERPRINT_KEY = "focus_preprocessing_fingerprint"
PIXEL_DIGEST_KEY = "focus_pixel_digest"
# Level-0 pixel offset "x,y" of a preprocessed image's origin in its source image (tissue crop).
CROP_OFFSET_KEY = "focus_crop_offset"
# JSON stat signature of the file a regenerated, pixel-identical image replaced.
SUPERSEDED_SIGNATURE_KEY = "focus_superseded_signature"
# JSON {"stat", "sha256"} of the input file, stored in the output (uns or OME MapAnnotation).
INPUT_SIGNATURE_KEY = "focus_input_signature"

_TIFF_SUFFIXES = (".tif", ".tiff", ".qptiff")
_SIGNATURE_CHUNK = 8 * 1024 * 1024


def fingerprint(payload: object) -> str:
    """Return a deterministic digest of the existing JSON-compatible contract."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_digest(path: str | PathLike[str]) -> str:
    """Stream a file into its digest without loading the whole file in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(_SIGNATURE_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_digest(array: ArrayLike) -> str:
    """sha256 of an array's dtype, shape and bytes."""
    array = np.ascontiguousarray(array)
    digest = hashlib.sha256(json.dumps([array.dtype.str, list(array.shape)]).encode())
    digest.update(array.data)
    return digest.hexdigest()


def planes_digest(planes: Iterable[ArrayLike]) -> str:
    """sha256 over a sequence of image planes, in order (dtype, shape and bytes of each)."""
    digest = hashlib.sha256()
    for plane in planes:
        plane = np.ascontiguousarray(plane)
        digest.update(json.dumps([plane.dtype.str, list(plane.shape)]).encode())
        digest.update(plane.data)
    return digest.hexdigest()


def stat_signature(path: str | PathLike[str]) -> list[str | int]:
    """Path, size and modification time: the identity used for files that carry no fingerprint."""
    stat = os.stat(path)
    return [os.path.abspath(path), stat.st_size, stat.st_mtime_ns]


def file_signature(path: str | PathLike[str]) -> list[str | int]:
    """Size and sha256 of the first and last 8 MiB: a cheap content identity for large raw files."""
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        if size <= 2 * _SIGNATURE_CHUNK:
            digest.update(stream.read())
        else:
            digest.update(stream.read(_SIGNATURE_CHUNK))
            stream.seek(size - _SIGNATURE_CHUNK)
            digest.update(stream.read(_SIGNATURE_CHUNK))
    return [size, digest.hexdigest()]


def is_tiff(path: str | PathLike[str]) -> bool:
    """Identify supported TIFF suffixes without opening the path."""
    return str(path).lower().endswith(_TIFF_SUFFIXES)


def read_ome_annotations(path: str | PathLike[str]) -> dict[str, str] | None:
    """FOCUS key/value pairs stored in an OME-TIFF's OME-XML MapAnnotation, or None."""
    import tifffile

    try:
        with tifffile.TiffFile(path) as tif:
            xml = cast(tifffile.TiffPage, tif.pages[0]).description
    except Exception:
        return None
    if not xml or "<OME" not in xml:
        return None
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    values = {}
    for element in root.iter():
        if (
            element.tag.rsplit("}", 1)[-1] != "MapAnnotation"
            or element.get("Namespace") != FOCUS_OME_NAMESPACE
        ):
            continue
        for entry in element.iter():
            if entry.tag.rsplit("}", 1)[-1] == "M" and entry.get("K") is not None:
                values[cast(str, entry.get("K"))] = entry.text or ""
    return values or None


def read_crop_offset(path: str | PathLike[str]) -> np.ndarray | None:
    """``(x, y)`` level-0 offset of a preprocessed image's origin in its source image, or None.

    Microscopy preprocessing records it (``focus_crop_offset``) when it crops to the tissue, and
    ``"0,0"`` otherwise; files written before it was stored, and other files, carry none.
    """
    return parse_crop_offset(read_ome_annotations(path) or {})


def parse_crop_offset(annotations: Mapping[str, str]) -> np.ndarray | None:
    """``(x, y)`` of a ``focus_crop_offset`` annotation value ``"x,y"``, or None when absent."""
    value = annotations.get(CROP_OFFSET_KEY)
    if value is None:
        return None
    return np.array([float(v) for v in value.split(",")], dtype=np.float32)


def cached_fingerprint(
    path: str | PathLike[str], key: str = PREPROCESSING_FINGERPRINT_KEY
) -> str | None:
    """The fingerprint stored under ``key``: in the OME MapAnnotation of a TIFF, in ``uns`` of an h5ad."""
    if is_tiff(path):
        return (read_ome_annotations(path) or {}).get(key)
    try:
        with h5py.File(path, "r") as f:
            value = f[f"uns/{key}"][()]
            return value.decode() if isinstance(value, bytes) else str(value)
    except (OSError, KeyError):
        return None


def content_signature(path: str | PathLike[str]) -> str | list[str | int]:
    """Identity of an upstream file for downstream caches.

    A FOCUS OME-TIFF is identified by its level-0 pixel digest, an h5ad by its preprocessing
    fingerprint; any other file falls back to its stat signature.
    """
    if is_tiff(path):
        digest = (read_ome_annotations(path) or {}).get(PIXEL_DIGEST_KEY)
        return f"pixels:{digest}" if digest else stat_signature(path)
    key = cached_fingerprint(path)
    return key if key is not None else stat_signature(path)


def input_signature(path: str | PathLike[str], output_file: str | PathLike[str]) -> str:
    """
    JSON ``{"stat": ..., "sha256": ...}`` of the input file.

    The SHA-256 stored in ``output_file`` (``uns`` of an h5ad, the MapAnnotation of an OME-TIFF) is
    reused while the input's path, size and modification time are unchanged, so an unchanged input
    is not re-hashed on every run.
    """
    stat = stat_signature(path)
    stored = cached_fingerprint(output_file, key=INPUT_SIGNATURE_KEY)
    if stored is not None:
        try:
            previous = json.loads(stored)
        except json.JSONDecodeError:
            previous = None
        if isinstance(previous, dict) and previous.get("stat") == stat:
            return stored
    return json.dumps({"stat": stat, "sha256": file_digest(path)})
